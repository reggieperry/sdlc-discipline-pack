#!/bin/bash
# SDLC claude-with-retry wrapper (pack #47).
#
# Interposes between gc's pool-spawn and the claude binary to add mid-task
# resume-and-retry on the two stall modes observed in chain operation
# (per-turn duration cap; API 529 overload). Rig opts in by setting
# `[providers.claude] command = "<path>/sdlc-claude-with-retry.sh"` in
# city.toml; gc then launches the wrapper with the claude argv it would
# have used directly.
#
# After each claude exit the wrapper delegates the retry-or-exit decision
# to claude_retry.py (the Python module's `decide` subcommand). Three
# possible decisions:
#   EXIT_SUCCESS         — bead's current_step advanced past this template;
#                          wrapper exits 0.
#   EXIT_EXHAUSTED <c>   — attempts cap reached without a handoff; wrapper
#                          exits 75 (EX_TEMPFAIL).
#   RETRY <delay> <c>    — retry after sleeping `delay` seconds.
#
# Two operating modes:
#   Active mode — STORY_ID is set (pool agents). Full retry loop with
#                 bd metadata writes and decide-call delegation.
#   Passthrough — STORY_ID is unset (mayor, freelance claude sessions,
#                 ACP transport). Wrapper execs claude directly with
#                 the passed argv. Required because city.toml's
#                 `[providers.claude] command` is a global override
#                 applying to every claude spawn, not just pool agents.
#
# Required env (active mode only):
#   STORY_ID          — bead ID of the story this template is working on.
#                       gc sets this at agent spawn. Unset → passthrough.
#
# Auto-resolved env (explicit value wins; production gc rarely sets these):
#   SDLC_TEMPLATE     — pool template (worker/tester/reviewer/documenter/
#                       finalizer). Auto-resolves from GC_SESSION_NAME
#                       (e.g., `sdlc-discipline.worker-1` → `worker`).
#                       Exits nonzero if neither SDLC_TEMPLATE nor
#                       GC_SESSION_NAME is set.
#   CLAUDE_RETRY_PY   — absolute path to claude_retry.py. Auto-resolves
#                       from the wrapper's own location:
#                       `<wrapper-dir>/../../overlay/per-provider/claude/
#                       .claude/sdlc-discipline/claude_retry.py`.
#
# Optional env:
#   SDLC_CLAUDE_SESSION_LOG    — path to claude's session JSONL. Defaults to
#                                /dev/null (causes decide to classify exits
#                                as UNKNOWN/CRASH; harmless on clean handoffs
#                                because handoff is checked first).
#   SDLC_RETRY_SLEEP_OVERRIDE  — override per-retry sleep (seconds). Used
#                                by tests; production leaves it unset.
#   SDLC_MAX_ATTEMPTS          — attempts cap. Default 5.

set -u

# Passthrough mode: STORY_ID unset → no bead to track → exec claude
# directly. `[providers.claude] command` is a global override; the
# mayor and any freelance claude spawn pass through here without
# STORY_ID and must not die on the `STORY_ID:?` line below (gc waits
# 60s for claude's `❯` prompt before declaring the session dead).
if [ -z "${STORY_ID:-}" ]; then
    exec claude "$@"
fi

STORY_ID="${STORY_ID:?STORY_ID env required}"

# SDLC_TEMPLATE auto-resolution: production gc sets GC_SESSION_NAME
# (e.g., `sdlc-discipline.worker-1`) but does NOT set SDLC_TEMPLATE.
# Extract the template name from GC_SESSION_NAME by stripping the
# `sdlc-discipline.` prefix and the `-N` suffix. Explicit SDLC_TEMPLATE
# wins for tests + operator overrides.
if [ -z "${SDLC_TEMPLATE:-}" ] && [ -n "${GC_SESSION_NAME:-}" ]; then
    _template_candidate="${GC_SESSION_NAME#sdlc-discipline.}"
    SDLC_TEMPLATE="${_template_candidate%-*}"
fi
SDLC_TEMPLATE="${SDLC_TEMPLATE:?SDLC_TEMPLATE env required (or set GC_SESSION_NAME)}"

# CLAUDE_RETRY_PY auto-resolution: the wrapper lives at
# `<pack>/assets/scripts/sdlc-claude-with-retry.sh`; the Python module
# lives at `<pack>/overlay/per-provider/claude/.claude/sdlc-discipline/
# claude_retry.py`. Resolve relative to the wrapper's own location so
# production gc doesn't have to know the pack layout.
if [ -z "${CLAUDE_RETRY_PY:-}" ]; then
    _wrapper_dir="$(cd "$(dirname "$0")" && pwd)"
    CLAUDE_RETRY_PY="${_wrapper_dir}/../../overlay/per-provider/claude/.claude/sdlc-discipline/claude_retry.py"
fi
SESSION_LOG="${SDLC_CLAUDE_SESSION_LOG:-/dev/null}"
SLEEP_OVERRIDE="${SDLC_RETRY_SLEEP_OVERRIDE:-}"
MAX_ATTEMPTS="${SDLC_MAX_ATTEMPTS:-5}"

# Extract --session-id from gc's argv so the retry path can use
# `claude --resume <id>`. gc always passes --session-id when launching a
# pool agent; absence means either a test that doesn't need retry or a
# misconfigured caller. The wrapper only fails if a retry is actually
# needed and the id is absent.
extract_session_id() {
    while [ $# -gt 0 ]; do
        if [ "$1" = "--session-id" ] && [ $# -ge 2 ]; then
            printf '%s' "$2"
            return
        fi
        shift
    done
}

SESSION_ID=$(extract_session_id "$@")

# Convenience wrapper for the bd metadata writes. Two K=V pairs is the
# common shape; the function accepts any number for flexibility.
write_metadata() {
    bd update "$STORY_ID" "$@" >/dev/null
}

# Append a single exit-cause entry to `<template>.exit_history` (pack
# #105 audit-trail improvement). Reads the prior value, appends
# `<ISO-ts>~<kind>~<cause>` separated by `|`, writes back. Best-effort —
# never blocks the retry loop on metadata write failure. The `|` and
# `~` separators are chosen so they never collide with ISO timestamps
# or wrapper-side cause strings (e.g. TURN_CAP, API_OVERLOAD).
#
# After the wrapper exits, the operator can read the full history via:
#   bd show <id> --json | jq -r ".[0].metadata.\"<template>.exit_history\""
# and parse with:
#   tr '|' '\n' | awk -F'~' '{print $1, $2, $3}'
append_exit_history() {
    local kind="$1"
    local cause="$2"
    local ts new_entry prior new_history
    ts=$(date -Iseconds)
    new_entry="${ts}~${kind}~${cause}"
    prior=$(bd show "$STORY_ID" --json 2>/dev/null \
        | jq -r ".[0].metadata.\"${SDLC_TEMPLATE}.exit_history\" // \"\"" 2>/dev/null)
    if [ -z "$prior" ] || [ "$prior" = "null" ]; then
        new_history="$new_entry"
    else
        new_history="${prior}|${new_entry}"
    fi
    bd update "$STORY_ID" --set-metadata "${SDLC_TEMPLATE}.exit_history=${new_history}" \
        >/dev/null 2>&1 || true
}

ATTEMPT=1

while true; do
    write_metadata \
        --set-metadata "${SDLC_TEMPLATE}.attempt_n=${ATTEMPT}" \
        --set-metadata "${SDLC_TEMPLATE}.state=running"

    # `set -e` is off so a non-zero claude exit doesn't abort the wrapper;
    # the decide call interprets the exit and the rc together.
    if [ "$ATTEMPT" -eq 1 ]; then
        claude "$@"
    else
        if [ -z "$SESSION_ID" ]; then
            echo "wrapper: --session-id required for retry but absent from argv" >&2
            exit 1
        fi
        CONTINUATION=$(python3 "$CLAUDE_RETRY_PY" build-prompt)
        claude --resume "$SESSION_ID" "$CONTINUATION"
    fi
    RC=$?

    RESULT=$(python3 "$CLAUDE_RETRY_PY" decide \
        --bead "$STORY_ID" \
        --template "$SDLC_TEMPLATE" \
        --session-log "$SESSION_LOG" \
        --return-code "$RC" \
        --attempt "$ATTEMPT" \
        --max-attempts "$MAX_ATTEMPTS")

    case "$RESULT" in
        EXIT_SUCCESS*)
            exit 0
            ;;
        EXIT_EXHAUSTED*)
            CAUSE=$(echo "$RESULT" | awk '{print $2}')
            # exhausted_at: enables the supervisor-side
            # exhausted-bead-retry watcher (pack #47) to compute
            # "minutes since exhaustion" and decide when to re-sling
            # the bead for a fresh wrapper-loop attempt. Without
            # this timestamp the watcher can't distinguish a bead
            # that exhausted 30 seconds ago from one that exhausted
            # an hour ago.
            write_metadata \
                --set-metadata "${SDLC_TEMPLATE}.state=exhausted" \
                --set-metadata "${SDLC_TEMPLATE}.last_exit_cause=${CAUSE}" \
                --set-metadata "${SDLC_TEMPLATE}.exhausted_at=$(date -Iseconds)"
            append_exit_history "exhausted" "$CAUSE"
            exit 75
            ;;
        RETRY*)
            DELAY=$(echo "$RESULT" | awk '{print $2}')
            CAUSE=$(echo "$RESULT" | awk '{print $3}')
            write_metadata \
                --set-metadata "${SDLC_TEMPLATE}.last_exit_cause=${CAUSE}" \
                --set-metadata "${SDLC_TEMPLATE}.state=resuming"
            append_exit_history "retry" "$CAUSE"
            if [ -n "$SLEEP_OVERRIDE" ]; then
                sleep "$SLEEP_OVERRIDE"
            else
                sleep "$DELAY"
            fi
            ATTEMPT=$((ATTEMPT + 1))
            ;;
        *)
            echo "wrapper: unknown decide result: $RESULT" >&2
            exit 1
            ;;
    esac
done
