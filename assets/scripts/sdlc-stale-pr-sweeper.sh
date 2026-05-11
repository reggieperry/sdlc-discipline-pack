#!/usr/bin/env bash
# sdlc-stale-pr-sweeper.sh — periodic mop-up for stale chain PRs
#
# Fired on a cooldown (5m default; configured in orders/sdlc-stale-pr-sweeper.toml).
# Scans chain beads in `final_state=pr_open_for_human` state, checks each PR's
# mergeable state via gh, and triggers a rebase pass on any that are BEHIND or
# DIRTY relative to their target branch.
#
# Why this exists: the event-triggered watcher (orders/sdlc-rebase-watcher.toml)
# fires immediately on bead.closed, but GitHub's mergeable computation has a
# ~10-60s lag. PRs that are stale-but-not-yet-computed return UNKNOWN at the
# watcher's check time and get skipped. This sweeper catches them on the next
# cooldown tick.
#
# Idempotent against in-flight rebases: a bead that's already been re-triggered
# leaves `status=closed` (it goes to `open`/`in_progress` during the re-walk).
# This script only looks at `status=closed` beads, so a re-triggered bead is
# naturally skipped until it closes again.
#
# Tunable: SDLC_SWEEPER_ENABLED (default "true"). When "false", exits at line
# one without scanning. The event-triggered watcher still fires.

set -euo pipefail

if [ "${SDLC_SWEEPER_ENABLED:-true}" != "true" ]; then
    exit 0
fi

RIG="${GC_RIG:-}"
if [ -z "$RIG" ]; then
    # Cron orders are city-scoped; without a rig in the env we can't filter
    # beads to one rig. Future: enumerate rigs from city.toml. For now exit.
    exit 0
fi

RIG_ROOT="${GC_RIG_ROOT:-}"
if [ -z "$RIG_ROOT" ] || [ ! -d "$RIG_ROOT" ]; then
    echo "sweeper: GC_RIG_ROOT not set or missing; skipping rig $RIG" >&2
    exit 0
fi

# Find chain beads in the post-finalize state. `bd list --status=closed`
# excludes in-flight rebases (status=open or status=in_progress), so this
# query is naturally idempotent against already-triggered beads.
STALE_CANDIDATES=$(bd list --status=closed --limit 5000 --json 2>/dev/null \
    | jq -r --arg rig "$RIG" \
        '.[] | select(
            (.metadata.rig // "") == $rig
            and (.metadata.final_state // "") == "pr_open_for_human"
        ) | .id' 2>/dev/null || true)

[ -z "$STALE_CANDIDATES" ] && exit 0

echo "sweeper: scanning rig=$RIG for stale chain PRs" >&2

echo "$STALE_CANDIDATES" | while IFS= read -r bead_id; do
    [ -z "$bead_id" ] && continue

    BEAD_JSON=$(bd show "$bead_id" --json 2>/dev/null || true)
    [ -z "$BEAD_JSON" ] && continue

    PR_URL=$(echo "$BEAD_JSON" | jq -r '.[0].metadata.pr_url // empty')
    STORY_ID=$(echo "$BEAD_JSON" | jq -r '.[0].metadata.story_id // empty')
    if [ -z "$PR_URL" ] || [ -z "$STORY_ID" ]; then
        continue
    fi

    PR_NUMBER=$(echo "$PR_URL" | grep -oE '[0-9]+$')
    [ -z "$PR_NUMBER" ] && continue

    PR_STATE=$(cd "$RIG_ROOT" && gh pr view "$PR_NUMBER" --json mergeStateStatus,state 2>/dev/null || echo "")
    [ -z "$PR_STATE" ] && continue

    MERGEABLE=$(echo "$PR_STATE" | jq -r '.mergeStateStatus // empty')
    STATE=$(echo "$PR_STATE" | jq -r '.state // empty')

    # PR must still be open. Closed/merged PRs are not actionable.
    [ "$STATE" != "OPEN" ] && continue

    case "$MERGEABLE" in
        BEHIND|DIRTY)
            echo "sweeper: PR #$PR_NUMBER ($STORY_ID) is $MERGEABLE; triggering rebase" >&2
            cd "$RIG_ROOT" && python3 "$PACK_DIR/overlay/per-provider/claude/.claude/sdlc-discipline/stories.py" rebase "$STORY_ID" >&2 || true
            ;;
        *)
            # CLEAN — no rebase needed.
            # UNKNOWN/BLOCKED/HAS_HOOKS/UNSTABLE/CONFLICTING — leave alone, same as the event-triggered watcher.
            ;;
    esac
done

exit 0
