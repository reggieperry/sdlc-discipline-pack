#!/usr/bin/env bash
# sdlc-rebase-watcher.sh — observer hook fired by orders/sdlc-rebase-watcher.toml
#
# Fires on every bead.closed event in the city. Only acts when the closed
# bead is a chain bead that just merged (metadata.final_state=merged) — for
# every other close (cost-rollup orders, escalations, no-PR closes, beads
# from other contexts), exits cleanly without side effects.
#
# When acting: find sibling chain beads in the same rig that have open PRs
# against the same target branch. Check each PR's mergeStateStatus via gh.
# If BEHIND or DIRTY, trigger `gc sdlc-stories rebase <story_id>` — entering
# the v2.7.0 bounce loop. Mergeable PRs (CLEAN), or PRs whose state is
# ambiguous (UNKNOWN, BLOCKED), are left alone.
#
# Environment provided by the order trigger:
#   GC_EVENT_TYPE       e.g. "bead.closed"
#   GC_EVENT_SUBJECT    the closed bead ID
#   GC_EVENT_PAYLOAD    JSON payload of the event
#   GC_CITY_ROOT        absolute path to the city root
#   GC_RIG              rig name (when the event is rig-scoped)
#   GC_RIG_ROOT         absolute path to the rig (when set by the supervisor)
#
# Tunable:
#   SDLC_WATCHER_ENABLED  default "true". Set to "false" to disable the
#                         autonomous trigger. The manual command
#                         `gc sdlc-stories rebase <story_id>` still works.

set -euo pipefail

# Feature gate — operator override
if [ "${SDLC_WATCHER_ENABLED:-true}" != "true" ]; then
    exit 0
fi

BEAD_ID="${GC_EVENT_SUBJECT:-}"
[ -z "$BEAD_ID" ] && exit 0

# Read the closed bead. Silent on failure — the event subject may not be a
# bead (some events carry other identifiers), and we don't want a noisy
# log every time a non-bead close fires the hook.
BEAD_JSON=$(bd show "$BEAD_ID" --json 2>/dev/null || true)
[ -z "$BEAD_JSON" ] && exit 0

# Only react to merged chain beads. The merge path is the one that moves
# the target branch and invalidates sibling PRs; every other close is a
# no-op for the watcher's purpose.
FINAL_STATE=$(echo "$BEAD_JSON" | jq -r '.[0].metadata.final_state // empty')
[ "$FINAL_STATE" != "merged" ] && exit 0

RIG=$(echo "$BEAD_JSON" | jq -r '.[0].metadata.rig // empty')
[ -z "$RIG" ] && exit 0

TARGET=$(echo "$BEAD_JSON" | jq -r '.[0].metadata.target // "main"')

# Resolve the rig root. GC_RIG_ROOT when the supervisor sets it; otherwise
# strip the .gc/worktrees suffix from the closing bead's work_dir.
WORK_DIR=$(echo "$BEAD_JSON" | jq -r '.[0].metadata.work_dir // empty')
RIG_ROOT="${GC_RIG_ROOT:-${WORK_DIR%/.gc/worktrees/*}}"
if [ -z "$RIG_ROOT" ] || [ ! -d "$RIG_ROOT" ]; then
    echo "watcher: cannot resolve rig root for bead $BEAD_ID (rig=$RIG); skipping" >&2
    exit 0
fi

# Find sibling chain beads: same rig, same target, in the post-finalize
# state (closed with an open PR), excluding the just-merged bead itself.
# Linear scan over the rig's closed beads; fine at the scales we see today.
SIBLINGS=$(bd list --status=closed --limit 5000 --json 2>/dev/null \
    | jq -r --arg rig "$RIG" --arg target "$TARGET" --arg me "$BEAD_ID" \
        '.[] | select(
            (.metadata.rig // "") == $rig
            and (.metadata.final_state // "") == "pr_open_for_human"
            and (.metadata.target // "main") == $target
            and .id != $me
        ) | .id' 2>/dev/null || true)

[ -z "$SIBLINGS" ] && exit 0

echo "watcher: rig $RIG, target $TARGET — bead $BEAD_ID merged; checking siblings" >&2

# Walk siblings, check each PR's mergeable state, trigger rebase on stale.
echo "$SIBLINGS" | while IFS= read -r sibling_id; do
    [ -z "$sibling_id" ] && continue

    SIBLING_JSON=$(bd show "$sibling_id" --json 2>/dev/null || true)
    [ -z "$SIBLING_JSON" ] && continue

    PR_URL=$(echo "$SIBLING_JSON" | jq -r '.[0].metadata.pr_url // empty')
    STORY_ID=$(echo "$SIBLING_JSON" | jq -r '.[0].metadata.story_id // empty')
    if [ -z "$PR_URL" ] || [ -z "$STORY_ID" ]; then
        continue
    fi

    PR_NUMBER=$(echo "$PR_URL" | grep -oE '[0-9]+$')
    [ -z "$PR_NUMBER" ] && continue

    # Query GitHub for the PR's current mergeable state from inside the rig
    # so gh authenticates against the rig's remote.
    PR_STATE=$(cd "$RIG_ROOT" && gh pr view "$PR_NUMBER" --json mergeStateStatus,state 2>/dev/null || echo "")
    [ -z "$PR_STATE" ] && continue

    MERGEABLE=$(echo "$PR_STATE" | jq -r '.mergeStateStatus // empty')
    STATE=$(echo "$PR_STATE" | jq -r '.state // empty')

    # PR must still be open; closed/merged PRs are nothing to rebase against.
    [ "$STATE" != "OPEN" ] && continue

    case "$MERGEABLE" in
        BEHIND|DIRTY)
            echo "watcher: PR #$PR_NUMBER ($STORY_ID) is $MERGEABLE; triggering gc sdlc-stories rebase" >&2
            cd "$RIG_ROOT" && gc sdlc-stories rebase "$STORY_ID" >&2 || true
            ;;
        CLEAN|UNKNOWN|BLOCKED|HAS_HOOKS|UNSTABLE)
            # CLEAN — no rebase needed.
            # UNKNOWN/BLOCKED/HAS_HOOKS/UNSTABLE — GitHub is mid-computation
            # or branch protection is engaged; let the human or the next
            # event resolve it.
            ;;
        CONFLICTING)
            # The PR is already in textual conflict with main on GitHub's
            # side. A finalizer that runs will discover this too. Leave it
            # alone here — multiple watchers shouldn't pile on the same bead.
            ;;
        *)
            echo "watcher: PR #$PR_NUMBER ($STORY_ID) has unknown mergeStateStatus '$MERGEABLE'; skipping" >&2
            ;;
    esac
done

exit 0
