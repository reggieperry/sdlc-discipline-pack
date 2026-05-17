#!/bin/bash
# SDLC claude-with-retry wrapper smoke test (pack #47 sub-story 3).
#
# Exercises the wrapper through a real tmux session with a fake claude
# binary in the same spawn shape gc uses. Required to run before
# enabling `[providers.claude] command` on a production rig — unit
# tests cover the wrapper's logic in isolation but cannot catch
# spawn-shape failures like the one that broke T7920's chain on
# 2026-05-16.
#
# Two scenarios run:
#   1. Passthrough — STORY_ID unset (mayor / freelance / ACP sessions).
#                    Wrapper must `exec claude` directly.
#   2. Active      — STORY_ID set (pool agents under sdlc-discipline).
#                    Wrapper must reach `claude` after bd metadata writes.
#
# Both must show claude's readiness prompt ("❯ ") within
# READY_TIMEOUT seconds, mirroring gc's `waitForReady` step
# (`internal/runtime/tmux/adapter.go:725`).
#
# Run with:
#   bash assets/scripts/sdlc-smoke-test-claude-wrapper.sh
#
# Exits 0 if all scenarios pass; nonzero otherwise.

set -euo pipefail

WRAPPER_DIR="$(cd "$(dirname "$0")" && pwd)"
WRAPPER="${WRAPPER_DIR}/sdlc-claude-with-retry.sh"

# Readiness sentinel — printed by the fake claude. Starts with the
# same "❯" the real claude profile uses as ReadyPromptPrefix in
# `internal/worker/builtin/profiles.go`, but adds a distinctive suffix
# because `tmux capture-pane` strips trailing whitespace from each
# captured line by default, so plain "❯ " can't be matched reliably.
# The suffix makes the match deterministic across tmux versions.
READY_PROMPT="❯ SDLC_SMOKE_READY"
READY_TIMEOUT="${SDLC_SMOKE_READY_TIMEOUT:-15}"
SMOKE_SOCKET="sdlc-smoke-$$"

TMPDIR="$(mktemp -d)"
PASS_COUNT=0
FAIL_COUNT=0

cleanup() {
    tmux -L "$SMOKE_SOCKET" kill-server 2>/dev/null || true
    rm -rf "$TMPDIR"
}
trap cleanup EXIT

# Build fake claude that prints the readiness prompt then sleeps so the
# pane stays alive long enough for the capture-pane poll to observe it.
build_fakes() {
    cat >"$TMPDIR/claude" <<'CLAUDE_EOF'
#!/bin/bash
printf '❯ SDLC_SMOKE_READY\n'
exec sleep 30
CLAUDE_EOF
    chmod +x "$TMPDIR/claude"

    # Fake bd — silent no-op (active-mode `bd update` calls).
    cat >"$TMPDIR/bd" <<'BD_EOF'
#!/bin/bash
exit 0
BD_EOF
    chmod +x "$TMPDIR/bd"
}

# Poll the named pane for the readiness prompt with a deadline.
wait_for_prompt() {
    local session="$1"
    local deadline=$(( $(date +%s) + READY_TIMEOUT ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if tmux -L "$SMOKE_SOCKET" capture-pane -t "$session" -p 2>/dev/null \
            | grep -qF "$READY_PROMPT"; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

# Run one scenario: spawn the wrapper under tmux with the given env,
# wait for the readiness prompt, return 0 on PASS / 1 on FAIL.
#
# Env vars are set via an `env` prefix on the command string (not tmux's
# session-level `-e` flag) so they bind at exec time. tmux's `-e` sets
# the session environment for FUTURE processes; the initial command's
# environment can be a moving target depending on tmux version and
# socket-server state. `env PATH=… KEY=VAL … cmd` is unambiguous.
run_scenario() {
    local name="$1"; shift
    local session="sdlc-smoke-${name}-$$"
    local env_prefix="PATH=$TMPDIR:$PATH"
    for kv in "$@"; do
        env_prefix+=" $kv"
    done

    tmux -L "$SMOKE_SOCKET" new-session -d -s "$session" \
        "env $env_prefix $WRAPPER --session-id smoke-uuid"

    if wait_for_prompt "$session"; then
        echo "[$name] PASS — readiness prompt appeared"
        return 0
    fi
    echo "[$name] FAIL — readiness prompt not seen within ${READY_TIMEOUT}s"
    echo "  ---- captured pane content ----"
    tmux -L "$SMOKE_SOCKET" capture-pane -t "$session" -p 2>/dev/null \
        | sed 's/^/  | /' || true
    echo "  --------------------------------"
    return 1
}

record() {
    if "$@"; then
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

main() {
    echo "Smoke test — wrapper: $WRAPPER"
    echo "  Readiness prompt: '$READY_PROMPT'  (within ${READY_TIMEOUT}s)"
    echo

    build_fakes

    record run_scenario "passthrough"

    record run_scenario "active" \
        "STORY_ID=el-smoke" \
        "GC_SESSION_NAME=sdlc-discipline.worker-1" \
        "SDLC_CLAUDE_SESSION_LOG=$TMPDIR/session.jsonl"

    echo
    echo "Summary: $PASS_COUNT passed, $FAIL_COUNT failed."
    if [ "$FAIL_COUNT" -gt 0 ]; then
        exit 1
    fi
}

main "$@"
