#!/usr/bin/env bash
# sdlc-delayed-merge.sh — delayed-merge delayed-merge scanner for the v2.10.0 protocol.
#
# Fired on a cooldown (30m default; configured in orders/sdlc-delayed-merge.toml).
# Scans chain beads that the finalizer parked under delayed-merge — those carrying
# both final_state=pr_open_for_human AND review_recommendation=review_encouraged
# — and decides three outcomes per PR:
#
#   1. APPROVE-COMMENT  — a PR comment matches SDLC_DELAYED_MERGE_APPROVE_PATTERN.
#                          Merge immediately, regardless of delay window.
#
#   2. OBJECTION-COMMENT — a PR comment matches SDLC_DELAYED_MERGE_OBJECTION_PATTERN.
#                          Skip; the operator has signaled "hold for review."
#
#   3. DELAY-EXPIRED    — no override comments, PR age >= SDLC_REVIEW_ENCOURAGED_DELAY_HOURS.
#                          Merge.
#
# Anything else (no override, still in delay window) → skip, try next tick.
#
# After a merge, update the bead metadata with the merge mechanism + timestamp so
# the audit trail captures which gate fired. The bead is already closed by the
# finalizer at this point; bd update works on closed beads.
#
# Tunables:
#   SDLC_DELAYED_MERGE_ENABLED            default "false" since issue #191 (the
#                                          review_encouraged tier it served was
#                                          removed). Set "true" to revive.
#   SDLC_REVIEW_ENCOURAGED_DELAY_HOURS    default "24". Delay before auto-merge.
#   SDLC_DELAYED_MERGE_OBJECTION_PATTERN  default "NACK|HOLD|VETO". egrep alternation.
#   SDLC_DELAYED_MERGE_APPROVE_PATTERN    default "LGTM-AUTO|MERGE-NOW". egrep alternation.
#
# Override-comment matching is anchored: the patterns are matched against the
# *first non-whitespace token* of each PR comment body, so a comment that
# mentions "NACK" in prose (e.g., "this isn't a NACK") does not fire the
# objection path. To object, lead with the keyword.

set -euo pipefail

# Dormant by default since issue #191 removed the review_encouraged tier that
# this scanner serviced. The reviewer no longer emits review_encouraged, so the
# scan below would match zero beads regardless; the default-off flag makes the
# dormancy explicit and cheap. A rig that re-introduces a middle tier via
# override sets SDLC_DELAYED_MERGE_ENABLED=true to revive it.
if [ "${SDLC_DELAYED_MERGE_ENABLED:-false}" != "true" ]; then
    exit 0
fi

RIG="${GC_RIG:-}"
if [ -z "$RIG" ]; then
    exit 0
fi

RIG_ROOT="${GC_RIG_ROOT:-}"
if [ -z "$RIG_ROOT" ] || [ ! -d "$RIG_ROOT" ]; then
    echo "delayed-merge: GC_RIG_ROOT not set or missing; skipping rig $RIG" >&2
    exit 0
fi

DELAY_HOURS="${SDLC_REVIEW_ENCOURAGED_DELAY_HOURS:-24}"
OBJECTION_PATTERN="${SDLC_DELAYED_MERGE_OBJECTION_PATTERN:-NACK|HOLD|VETO}"
APPROVE_PATTERN="${SDLC_DELAYED_MERGE_APPROVE_PATTERN:-LGTM-AUTO|MERGE-NOW}"

DELAY_SECONDS=$((DELAY_HOURS * 3600))
NOW_EPOCH=$(date +%s)

# Find chain beads parked under delayed-merge. We look at closed beads (the
# finalizer's terminal state for review_encouraged) and filter by
# final_state + review_recommendation. delayed_merge_completed gates the
# already-merged from the still-pending.
CANDIDATES=$(bd list --status=closed --limit 5000 --json 2>/dev/null \
    | jq -r --arg rig "$RIG" '
        .[] | select(
            (.metadata.rig // "") == $rig
            and (.metadata.final_state // "") == "pr_open_for_human"
            and (.metadata.review_recommendation // "") == "review_encouraged"
            and (.metadata.delayed_merge_completed // "") != "true"
        ) | .id
    ' 2>/dev/null || true)

[ -z "$CANDIDATES" ] && exit 0

echo "delayed-merge: scanning rig=$RIG (delay=${DELAY_HOURS}h)" >&2

echo "$CANDIDATES" | while IFS= read -r bead_id; do
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

    PR_VIEW=$(cd "$RIG_ROOT" && gh pr view "$PR_NUMBER" --json state,mergeable,createdAt,comments 2>/dev/null || echo "")
    [ -z "$PR_VIEW" ] && continue

    PR_STATE=$(echo "$PR_VIEW" | jq -r '.state // empty')
    [ "$PR_STATE" != "OPEN" ] && continue

    MERGEABLE=$(echo "$PR_VIEW" | jq -r '.mergeable // empty')
    if [ "$MERGEABLE" != "MERGEABLE" ]; then
        # CONFLICTING / BEHIND / DIRTY / UNKNOWN — let the rebase watcher
        # handle these. Skip this tick.
        continue
    fi

    # Scan PR comments. We compare each comment's first non-whitespace token
    # against the configured patterns. Reviews (gh pr review --approve etc.)
    # are not scanned — only issue comments (gh pr comment).
    FIRST_TOKENS=$(echo "$PR_VIEW" | jq -r '.comments[]?.body // empty' \
        | awk 'NF { for (i=1; i<=NF; i++) { if ($i != "") { print $i; break } } }')

    OBJECTION_FOUND=$(echo "$FIRST_TOKENS" | grep -E "^(${OBJECTION_PATTERN})\$" | head -1 || true)
    APPROVE_FOUND=$(echo "$FIRST_TOKENS" | grep -E "^(${APPROVE_PATTERN})\$" | head -1 || true)

    if [ -n "$APPROVE_FOUND" ]; then
        REASON="approve-comment ($APPROVE_FOUND)"
    elif [ -n "$OBJECTION_FOUND" ]; then
        echo "delayed-merge: PR #$PR_NUMBER ($STORY_ID) has objection comment '$OBJECTION_FOUND'; skipping" >&2
        continue
    else
        # No override; check the delay window.
        PR_CREATED_AT=$(echo "$PR_VIEW" | jq -r '.createdAt // empty')
        [ -z "$PR_CREATED_AT" ] && continue
        PR_CREATED_EPOCH=$(date -d "$PR_CREATED_AT" +%s 2>/dev/null || echo 0)
        AGE_SECONDS=$((NOW_EPOCH - PR_CREATED_EPOCH))
        if [ "$AGE_SECONDS" -lt "$DELAY_SECONDS" ]; then
            # Still inside the delay window; try next tick.
            continue
        fi
        REASON="delay-expired (age ${AGE_SECONDS}s >= ${DELAY_SECONDS}s)"
    fi

    echo "delayed-merge: merging PR #$PR_NUMBER ($STORY_ID) — $REASON" >&2

    MERGE_OUT=$(cd "$RIG_ROOT" && gh pr merge "$PR_NUMBER" --squash --delete-branch 2>&1 || true)
    MERGED_AT=$(date -Iseconds)

    if cd "$RIG_ROOT" && gh pr view "$PR_NUMBER" --json state --jq '.state' 2>/dev/null | grep -q MERGED; then
        bd update "$bead_id" \
          --set-metadata delayed_merge_completed="true" \
          --set-metadata delayed_merge_at="$MERGED_AT" \
          --set-metadata delayed_merge_reason="$REASON" \
          --set-metadata final_state="merged_delayed" 2>/dev/null || true
        echo "delayed-merge: PR #$PR_NUMBER merged ($REASON)" >&2
    else
        echo "delayed-merge: PR #$PR_NUMBER merge failed; output: $MERGE_OUT" >&2
        bd update "$bead_id" \
          --set-metadata delayed_merge_last_attempt="$MERGED_AT" \
          --set-metadata delayed_merge_last_error="$(echo "$MERGE_OUT" | head -3 | tr '\n' '|')" 2>/dev/null || true
    fi
done

exit 0
