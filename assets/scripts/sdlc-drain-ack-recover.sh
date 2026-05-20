#!/usr/bin/env bash
# sdlc-drain-ack-recover.sh — pack-side subscriber for gascity's typed event
# session.drain_acked_with_assigned_work (gascity#2380).
#
# Triggered by orders/sdlc-drain-ack-recover.toml on every emission. Drives
# the 5-step Mode B recovery recipe documented in
# reference_chain_failure_modes.md §"Validated Mode B recovery":
#
#   1. Commit staged worktree changes (delegated to sdlc-stall-recover.sh)
#   2. Push the recovery branch to origin
#   3. Clear assignee and reset status to open on the bead
#   4. Kill the stalled gc session
#   5. Reload the supervisor (in case the reconciler is parked)
#
# Idempotency invariant: reset-to-pristine. Every step is safe to repeat;
# subsequent runs after success are no-ops. Failure on any step is fail-closed:
# halt, alert the operator via sdlc-notify.sh, leave the system in a state the
# operator can inspect.
#
# Feature gate: defaults OFF. The pack ships disabled; deployment sets
# SDLC_DRAIN_ACK_RECOVERY_ENABLED=true in the supervisor's env.
#
# Environment provided by the order trigger:
#   GC_EVENT_TYPE       "session.drain_acked_with_assigned_work"
#   GC_EVENT_PAYLOAD    JSON: {session_id, bead_id, template, bead_status, reason}
#   GC_RIG              rig name
#
# Test injection points (override the executable used by each step):
#   SDLC_DRAIN_ACK_GC               default `gc`
#   SDLC_DRAIN_ACK_GIT              default `git`
#   SDLC_DRAIN_ACK_STALL_RECOVER    default `sdlc-stall-recover.sh` (on PATH)
#   SDLC_DRAIN_ACK_NOTIFY           default `sdlc-notify.sh` (on PATH)
#
# Tunable:
#   SDLC_DRAIN_ACK_RECOVERY_ENABLED   default "false"; set to "true" to enable
#
# Exit codes:
#   0   Recovery completed (or feature gate off, or no-op for non-matching event)
#   2   Bead lookup failed
#   3   Commit step failed
#   4   Push step failed
#   5   Bead assignee-clear step failed
#   6   Session-kill step failed
#   7   Supervisor-reload step failed

set -uo pipefail

GC_BIN="${SDLC_DRAIN_ACK_GC:-gc}"
GIT_BIN="${SDLC_DRAIN_ACK_GIT:-git}"
STALL_RECOVER_BIN="${SDLC_DRAIN_ACK_STALL_RECOVER:-sdlc-stall-recover.sh}"
NOTIFY_BIN="${SDLC_DRAIN_ACK_NOTIFY:-sdlc-notify.sh}"

alert_operator() {
    local subject="$1"
    local body="$2"
    if command -v "$NOTIFY_BIN" >/dev/null 2>&1; then
        printf "%s\n" "$body" | "$NOTIFY_BIN" --subject "$subject" >/dev/null 2>&1 || true
    fi
}

bail() {
    local code="$1"
    local subject="$2"
    local body="$3"
    alert_operator "$subject" "$body"
    echo "drain-ack-recover: $subject — $body" >&2
    exit "$code"
}

lookup_bead() {
    if [ -n "$RIG" ] && [ "$RIG" != "-" ]; then
        "$GC_BIN" bd --rig "$RIG" show "$BEAD_ID" --json 2>/dev/null
    else
        "$GC_BIN" bd show "$BEAD_ID" --json 2>/dev/null
    fi
}

commit_worktree() {
    local work_dir="$1"
    local phase="$2"
    local bead_id="$3"
    (
        cd "$work_dir" || exit 90
        "$STALL_RECOVER_BIN" --phase "$phase" --bead-id "$bead_id"
    )
}

push_branch() {
    local work_dir="$1"
    local branch="$2"
    (
        cd "$work_dir" || exit 90
        "$GIT_BIN" push -u origin "$branch"
    )
}

clear_assignee() {
    local bead_id="$1"
    if [ -n "$RIG" ] && [ "$RIG" != "-" ]; then
        "$GC_BIN" bd --rig "$RIG" update "$bead_id" --assignee "" --status open
    else
        "$GC_BIN" bd update "$bead_id" --assignee "" --status open
    fi
}

kill_session() {
    "$GC_BIN" session kill "$1"
}

reload_supervisor() {
    "$GC_BIN" supervisor reload
}

# --- Feature gate ---
if [ "${SDLC_DRAIN_ACK_RECOVERY_ENABLED:-false}" != "true" ]; then
    exit 0
fi

# --- Event parse ---
PAYLOAD="${GC_EVENT_PAYLOAD:-}"
[ -z "$PAYLOAD" ] && exit 0

SESSION_ID=$(echo "$PAYLOAD" | jq -r '.session_id // empty' 2>/dev/null || true)
BEAD_ID=$(echo "$PAYLOAD" | jq -r '.bead_id // empty' 2>/dev/null || true)
PHASE=$(echo "$PAYLOAD" | jq -r '.template // empty' 2>/dev/null || true)

[ -z "$SESSION_ID" ] && exit 0
[ -z "$BEAD_ID" ] && exit 0

RIG="${GC_RIG:-}"

# --- Bead lookup ---
BEAD_JSON=$(lookup_bead)
if [ -z "$BEAD_JSON" ] || ! echo "$BEAD_JSON" | jq -e 'type == "array"' >/dev/null 2>&1; then
    bail 2 \
        "drain-ack-recover: bead lookup failed" \
        "Could not load bead $BEAD_ID via $GC_BIN bd${RIG:+ --rig $RIG} show. The bead remains stranded; manual recovery via the 5-step recipe in reference_chain_failure_modes.md is required."
fi

WORK_DIR=$(echo "$BEAD_JSON" | jq -r '.[0].metadata.work_dir // empty')
BRANCH=$(echo "$BEAD_JSON" | jq -r '.[0].metadata.branch // empty')

if [ -z "$WORK_DIR" ] || [ ! -d "$WORK_DIR" ]; then
    bail 2 \
        "drain-ack-recover: work_dir missing" \
        "Bead $BEAD_ID has no usable metadata.work_dir (saw: '$WORK_DIR'); cannot recover."
fi
if [ -z "$BRANCH" ]; then
    bail 2 \
        "drain-ack-recover: branch missing" \
        "Bead $BEAD_ID has no metadata.branch; cannot push."
fi
if [ -z "$PHASE" ]; then
    PHASE="implementor"
fi

# --- Step 1: commit ---
# sdlc-stall-recover.sh exits 3 ("nothing to commit after exclusions") when
# the worktree is already clean. That's the idempotent re-emission case —
# treat as success and continue. Exits 2 and 4 are real failures.
commit_worktree "$WORK_DIR" "$PHASE" "$BEAD_ID"
COMMIT_RC=$?
if [ "$COMMIT_RC" -ne 0 ] && [ "$COMMIT_RC" -ne 3 ]; then
    bail 3 \
        "drain-ack-recover: commit failed" \
        "Step 1 (sdlc-stall-recover.sh commit, exit $COMMIT_RC) failed in $WORK_DIR for bead $BEAD_ID."
fi

# --- Step 2: push ---
if ! push_branch "$WORK_DIR" "$BRANCH"; then
    bail 4 \
        "drain-ack-recover: push failed" \
        "Step 2 (git push -u origin $BRANCH) failed in $WORK_DIR; the commit was made but the branch did not reach origin."
fi

# --- Step 3: clear assignee ---
if ! clear_assignee "$BEAD_ID"; then
    bail 5 \
        "drain-ack-recover: bd update failed" \
        "Step 3 (bd update --assignee \"\" --status open) failed for bead $BEAD_ID; supervisor will not re-spawn until this clears."
fi

# --- Step 4: kill session ---
if ! kill_session "$SESSION_ID"; then
    bail 6 \
        "drain-ack-recover: session kill failed" \
        "Step 4 (gc session kill $SESSION_ID) failed; the stalled session may linger."
fi

# --- Step 5: reload supervisor ---
if ! reload_supervisor; then
    bail 7 \
        "drain-ack-recover: supervisor reload failed" \
        "Step 5 (gc supervisor reload) failed; reconciler may stay parked."
fi

exit 0
