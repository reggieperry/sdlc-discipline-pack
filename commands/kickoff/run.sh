#!/bin/sh
# sdlc-kickoff.sh — non-LLM kickoff for the SDLC chain.
#
# Replaces the LLM-powered "gc sling <rig>/claude mol-sdlc --formula" path
# for chain initiation. Runs four bd commands locally instead of spawning
# a fresh Claude Code session whose only job was to run those four bd
# commands. The mol-sdlc.toml formula still exists for backward compat
# and for operators who want a wisp-tracked kickoff in their bead store.
#
# Usage: sdlc-kickoff.sh <story-bead-id>
#
# Env:
#   GC_RIG          Rig name. Defaults to the basename of the rig root
#                   discovered by walking up from cwd looking for .beads/.
#   SDLC_PACK_NAME  Pack binding name in city.toml. Defaults to
#                   "sdlc-discipline".
#
# Why this exists: empirical evidence from the v2.0 Phase 4 ten-story
# concurrent test showed launching ten stories produced a synchronized
# pulse of ten Claude session boots in a 76s window, each consuming
# ~350 MB RAM and ~20% CPU just to run four bd commands. On a 30 GB
# workstation that's tolerable; on a 16 GB laptop the kickoff pulse
# alone could push the host to swap. This script does the same four
# bd commands without spawning Claude, removing the worst synchronized
# resource pulse of the chain and dropping kickoff latency from
# 60-120s to under one second.

set -eu

STORY_ID="${1:?usage: sdlc-kickoff.sh <story-bead-id>}"
PACK="${SDLC_PACK_NAME:-sdlc-discipline}"

# Discover rig name. Operator may set GC_RIG explicitly. If not, walk
# up from cwd looking for a .beads/ directory; the parent of that is
# the rig root. Prefer the registered rig name from `gc rig list --json`
# (keyed by absolute path) because the rig's `name` field can differ
# from the directory basename — Elder's case is `name = "elder"` for a
# directory called `elder_trading_system`. Fall back to basename only
# when the json lookup fails (gc unavailable, jq unavailable, or the
# rig is not yet registered with the city).
if [ -n "${GC_RIG:-}" ]; then
    RIG="$GC_RIG"
else
    DIR=$(pwd)
    while [ "$DIR" != "/" ] && [ ! -d "$DIR/.beads" ]; do
        DIR=$(dirname "$DIR")
    done
    if [ ! -d "$DIR/.beads" ]; then
        echo "sdlc-kickoff: no .beads/ directory found walking up from $(pwd); set GC_RIG explicitly" >&2
        exit 1
    fi
    RIG=""
    if command -v gc >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
        # Resolve symlinks so `gc rig list`'s absolute path comparison matches
        # rig roots discovered via cwd walking when either side is a symlink.
        ABS_DIR=$(cd "$DIR" && pwd -P)
        RIG=$(gc rig list --json 2>/dev/null \
            | jq -r --arg p "$ABS_DIR" '.rigs[] | select(.path == $p) | .name' \
            | head -1)
    fi
    if [ -z "$RIG" ] || [ "$RIG" = "null" ]; then
        RIG=$(basename "$DIR")
    fi
fi

# Verify the story bead exists and capture its metadata in one query. A
# wrong bead ID fails fast here before we set any metadata.
BEAD_JSON=$(bd show "$STORY_ID" --json 2>/dev/null || true)
if [ -z "$BEAD_JSON" ]; then
    echo "sdlc-kickoff: story bead '$STORY_ID' not found in rig '$RIG'" >&2
    exit 1
fi

# Pack #197 — refuse to re-route a bead parked for a human decision.
# requires_human_decision=true is set when a phase escalates a decision the
# chain cannot make on its own (e.g. a gate block with no chain path; the
# worker's rebase-signals escalation). The pool reconciler admits on
# gc.routed_to + bd ready, so without this guard a kickoff re-opens and
# re-routes the bead, re-spawning a worker into the identical dead-end
# (Elder EL-173: three spawns). A non-"true" value (resolved / empty /
# absent) does not block, so a resolved bead re-kicks normally; the operator
# clears the flag via sdlc-human-decision.sh once the decision is made.
HUMAN_DECISION=$(printf '%s' "$BEAD_JSON" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d[0].get("metadata") or {}).get("requires_human_decision","") if d else "")' \
    2>/dev/null || true)
if [ "$HUMAN_DECISION" = "true" ]; then
    echo "sdlc-kickoff: $STORY_ID carries requires_human_decision=true; not re-routing (resolve it first, e.g. sdlc-human-decision.sh resolve $STORY_ID)." >&2
    exit 0
fi

WORKER_TARGET="$RIG/$PACK.worker"
NOW=$(date -Iseconds)

echo "sdlc-kickoff: routing $STORY_ID to $WORKER_TARGET"

bd update "$STORY_ID" \
    --status=open \
    --set-metadata gc.routed_to="$WORKER_TARGET" \
    --set-metadata sdlc_run_started="$NOW" \
    --set-metadata kickoff_mode="non_llm" \
    >/dev/null

bd update "$STORY_ID" \
    --append-notes "SDLC kickoff at $NOW: worker → tester → reviewer → documenter → finalizer. Kickoff via sdlc-kickoff.sh (no LLM)." \
    >/dev/null

# Operator-memory snapshot (pack #45). Writes the operator's Claude Code
# auto-memory entries — filtered to entries whose `metadata.type` is in
# {project, reference} — to a per-bead context file. Chain agents read
# the file at session start so they have the operator's project context
# alongside the rig's checked-in CLAUDE.md and rules. Graceful: writes
# an empty file when the operator's memory dir is absent.
SCRIPT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
SNAPSHOT_PY="$SCRIPT_DIR/overlay/per-provider/claude/.claude/sdlc-discipline/snapshot_operator_memory.py"
OPERATOR_CONTEXT="$DIR/.gc/operator-context/$STORY_ID.md"
if [ -f "$SNAPSHOT_PY" ]; then
    python3 "$SNAPSHOT_PY" --output "$OPERATOR_CONTEXT" --cwd "$DIR" 2>&1 | sed 's|^|  |' || true
    bd update "$STORY_ID" --set-metadata operator_context_path="$OPERATOR_CONTEXT" >/dev/null
fi

echo "sdlc-kickoff: done. Pool reconciler will spawn a worker on its next tick."
echo "  Watch progress:  sh $SCRIPT_DIR/assets/scripts/sdlc-watch.sh $STORY_ID"
echo "  Bead state:      bd show $STORY_ID --json | jq '.[0].metadata'"
