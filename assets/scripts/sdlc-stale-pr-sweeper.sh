#!/usr/bin/env bash
# sdlc-stale-pr-sweeper.sh — periodic mop-up for stale chain PRs
#
# Fired on a cooldown (5m default; configured in orders/sdlc-stale-pr-sweeper.toml).
# Scans chain beads in `final_state=pr_open_for_human` state across every
# registered rig, checks each PR's mergeable state via gh, and triggers a
# rebase pass on any that are BEHIND, DIRTY, or CONFLICTING relative to
# their target branch.
#
# Why this exists:
#
#   1. The event-triggered watcher (orders/sdlc-rebase-watcher.toml) only
#      fires on bead.closed events whose metadata.final_state=merged — the
#      auto-merge path used by the v2.10.0 glance_merge tier. PRs that are
#      manually merged by a human via `gh pr merge` do not update bead
#      metadata, so the watcher never fires for them.
#
#   2. GitHub's mergeable computation has a ~10-60s lag after a merge,
#      so siblings of an auto-merged PR may return UNKNOWN at the watcher's
#      check time. The sweeper catches them on the next cooldown tick.
#
# Rig enumeration:
#
#   Cron orders fire city-scoped without a GC_RIG context. The sweeper
#   enumerates registered rigs via `gc rig list --json` from GC_CITY_ROOT,
#   then runs the scan logic per non-suspended, non-HQ rig.
#
# Idempotent against in-flight rebases: a bead that's already been re-triggered
# leaves `status=closed` (it goes to `open`/`in_progress` during the re-walk).
# This script only acts on `status=closed` beads, so a re-triggered bead is
# naturally skipped until it closes again.
#
# Tunable: SDLC_STALE_PR_SWEEPER_ENABLED (default "true"). When "false",
# exits at line one without scanning. The event-triggered watcher still fires.
# Legacy name `SDLC_SWEEPER_ENABLED` is still honored for one release with
# a stderr warning; will be removed in v2.30 alongside `SDLC_WATCHER_ENABLED`.

set -euo pipefail

# Feature gate — operator override.
# v2.29.9: renamed SDLC_SWEEPER_ENABLED → SDLC_STALE_PR_SWEEPER_ENABLED to match
# the SDLC_<SHORTNAME>_ENABLED convention used by the other detector scripts.
# The legacy name is still read for one release with a deprecation warning.
if [ -n "${SDLC_SWEEPER_ENABLED:-}" ] && [ -z "${SDLC_STALE_PR_SWEEPER_ENABLED:-}" ]; then
    echo "sdlc-stale-pr-sweeper: SDLC_SWEEPER_ENABLED is deprecated; use SDLC_STALE_PR_SWEEPER_ENABLED. Honoring legacy value for this release." >&2
    SDLC_STALE_PR_SWEEPER_ENABLED="$SDLC_SWEEPER_ENABLED"
fi
if [ "${SDLC_STALE_PR_SWEEPER_ENABLED:-true}" != "true" ]; then
    exit 0
fi

CITY_ROOT="${GC_CITY_ROOT:-}"
if [ -z "$CITY_ROOT" ] || [ ! -d "$CITY_ROOT" ]; then
    echo "sweeper: GC_CITY_ROOT not set or missing; cannot enumerate rigs" >&2
    exit 0
fi

# Locate the shared rig-enumeration library, relative to this script.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RIG_LISTER="$SCRIPT_DIR/lib/sdlc-list-rigs.sh"

# Resolve PACK_DIR for the stories.py rebase invocation later in this
# file. The library captures rig metadata; this remains needed for the
# bridge path.
PACK_DIR="${PACK_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

# Per-rig scan. Walks the rig's closed chain beads with final_state=pr_open_for_human,
# checks each PR's mergeable state, triggers rebase on stale.
sweep_rig() {
    local rig="$1"
    local rig_root="$2"

    if [ ! -d "$rig_root" ]; then
        echo "sweeper: rig=$rig root not found at $rig_root; skipping" >&2
        return
    fi

    local stale_candidates
    stale_candidates=$(bd -C "$rig_root" list --status=closed --limit 5000 --json 2>/dev/null \
        | jq -r --arg rig "$rig" \
            '.[] | select(
                (.metadata.rig // "") == $rig
                and (.metadata.final_state // "") == "pr_open_for_human"
            ) | .id' 2>/dev/null || true)

    [ -z "$stale_candidates" ] && return

    echo "sweeper: scanning rig=$rig for stale chain PRs" >&2

    echo "$stale_candidates" | while IFS= read -r bead_id; do
        [ -z "$bead_id" ] && continue

        local bead_json
        bead_json=$(bd -C "$rig_root" show "$bead_id" --json 2>/dev/null || true)
        [ -z "$bead_json" ] && continue

        # Dedup: if the bead raced into rebase between the list query and
        # this show (status moved closed → open or in_progress), skip.
        local bead_status
        bead_status=$(echo "$bead_json" | jq -r '.[0].status // empty')
        if [ "$bead_status" != "closed" ]; then
            echo "sweeper: bead $bead_id status=$bead_status (already in rebase iteration); skipping" >&2
            continue
        fi

        local pr_url story_id
        pr_url=$(echo "$bead_json" | jq -r '.[0].metadata.pr_url // empty')
        story_id=$(echo "$bead_json" | jq -r '.[0].metadata.story_id // empty')
        if [ -z "$pr_url" ] || [ -z "$story_id" ]; then
            continue
        fi

        local pr_number
        pr_number=$(echo "$pr_url" | grep -oE '[0-9]+$')
        [ -z "$pr_number" ] && continue

        local pr_state mergeable state
        pr_state=$(cd "$rig_root" && gh pr view "$pr_number" --json mergeStateStatus,state 2>/dev/null || echo "")
        [ -z "$pr_state" ] && continue

        mergeable=$(echo "$pr_state" | jq -r '.mergeStateStatus // empty')
        state=$(echo "$pr_state" | jq -r '.state // empty')

        # PR closed-without-merge: bead stays in pr_open_for_human until an
        # operator triages it. Sweeper has nothing actionable on this path.
        if [ "$state" = "CLOSED" ]; then
            continue
        fi

        # PR merged externally (human ran `gh pr merge` on a tier that the
        # finalizer parked — review_encouraged or human_required). The
        # finalizer never sees the merge event; without this reconciler the
        # bead stays at final_state=pr_open_for_human forever, the
        # rebase-watcher's `final_state=merged` path is dead for the most
        # common case, and the sweeper continues to call `gh pr view`
        # against this bead on every 5-min tick (bounded waste).
        #
        # Reconcile by promoting the bead to final_state=merged with the
        # observed timestamp + SHA. Closes #39 (Option A: sweeper-side side
        # effect; the sweeper is already iterating these beads with the gh
        # data in hand, so the cost is one extra bd update per merge event
        # — no new cron order, no new infrastructure).
        if [ "$state" = "MERGED" ]; then
            local merged_pr_state merged_at merged_sha
            merged_pr_state=$(cd "$rig_root" && gh pr view "$pr_number" --json mergedAt,mergeCommit 2>/dev/null || echo "")
            merged_at=$(echo "$merged_pr_state" | jq -r '.mergedAt // empty')
            merged_sha=$(echo "$merged_pr_state" | jq -r '.mergeCommit.oid // empty')
            echo "sweeper: PR #$pr_number ($story_id) merged externally; reconciling bead metadata (final_state=merged)" >&2
            cd "$rig_root" && bd update "$story_id" \
                --set-metadata "final_state=merged" \
                ${merged_at:+--set-metadata "final_merged_at=$merged_at"} \
                ${merged_sha:+--set-metadata "final_merged_sha=$merged_sha"} \
                >/dev/null 2>&1 || true
            continue
        fi

        # Any other non-OPEN state (race conditions, unexpected gh values).
        # Leave the bead alone; the next tick gets another shot.
        [ "$state" != "OPEN" ] && continue

        case "$mergeable" in
            BEHIND|DIRTY|CONFLICTING)
                echo "sweeper: PR #$pr_number ($story_id) is $mergeable; triggering rebase" >&2
                cd "$rig_root" && python3 "$PACK_DIR/overlay/per-provider/claude/.claude/sdlc-discipline/stories.py" rebase "$story_id" >&2 || true
                ;;
            CLEAN)
                # No rebase needed. The PR is ready for human merge (or auto-merge
                # if it qualifies); we don't act on it here.
                ;;
            UNKNOWN|BLOCKED|HAS_HOOKS|UNSTABLE)
                # GitHub is mid-computation or branch protection is engaged;
                # leave for the next cooldown tick.
                echo "sweeper: PR #$pr_number ($story_id) is $mergeable; deferring to next tick" >&2
                ;;
            *)
                echo "sweeper: PR #$pr_number ($story_id) has unknown mergeStateStatus '$mergeable'; skipping" >&2
                ;;
        esac
    done
}

# Loop over each non-HQ, non-suspended rig via the shared library. The
# library tolerates malformed gc output (emits nothing) and an absent
# city root (logs + emits nothing), so this loop is safe under
# `set -euo pipefail`.
while IFS=$'\t' read -r rig_name rig_path; do
    [ -z "$rig_name" ] && continue
    sweep_rig "$rig_name" "$rig_path"
done < <(bash "$RIG_LISTER" "$CITY_ROOT")

exit 0
