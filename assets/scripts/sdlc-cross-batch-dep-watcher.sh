#!/usr/bin/env bash
# sdlc-cross-batch-dep-watcher.sh — periodic admit-watcher for cross-batch deps
#
# Fired on a cooldown (5m default; configured in
# orders/sdlc-cross-batch-dep-watcher.toml). Closes the timing gap left
# open by pack #152's `stories.py file` cross-batch dep wiring.
#
# Pack #152 added `bd dep add` calls for cross-batch dep edges so `bd ready`
# would filter out successor beads with unmerged predecessors. But `bd ready`
# unblocks on predecessor `status=closed`, and the chain's finalizer closes
# the predecessor's bead at chain-end with `final_state=pr_open_for_human` —
# hours before the human merges the PR in the review_encouraged or
# human_required tier. Without this watcher, the successor's worker spawns
# against `origin/main` before the predecessor's code lands.
#
# Defense (pack #154): stories.py defers each successor bead with
# `bd update --defer 2099-01-01 --set-metadata cross_batch_dep_predecessors=<bead>[,<bead>...]`.
# `bd ready` excludes deferred issues by default. This watcher observes the
# predecessors' `final_state`, and once all reach terminal merge-equivalent
# state, clears the defer + unsets the marker — admitting the bead to the
# pool reconciler.
#
# Promotion rules per predecessor:
#   final_state ∈ {merged, merged_delayed, branch_ready_no_pr}  → terminal
#   final_state = pr_open_for_human AND PR state = CLOSED       → escalate (mail operator; leave deferred)
#   anything else                                                → continue, check next tick
#
# Tunable: SDLC_CROSS_BATCH_DEP_WATCHER_ENABLED (default "true").
# When "false", exits immediately without scanning.
#
# Operator notify on escalation: uses SDLC_NOTIFY_RECIPIENT via the
# existing sdlc-notify.sh wrapper. Mail-send failure is non-blocking
# (the deferred bead stays deferred; the operator notices on next
# status check); the failure is logged to stderr.

set -uo pipefail

# Feature gate.
if [ "${SDLC_CROSS_BATCH_DEP_WATCHER_ENABLED:-true}" != "true" ]; then
    exit 0
fi

CITY_ROOT="${GC_CITY_ROOT:-}"
if [ -z "$CITY_ROOT" ] || [ ! -d "$CITY_ROOT" ]; then
    echo "cross-batch-dep-watcher: GC_CITY_ROOT not set or missing; cannot enumerate rigs" >&2
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RIG_LISTER="${SDLC_CROSS_BATCH_DEP_WATCHER_RIG_LISTER:-$SCRIPT_DIR/lib/sdlc-list-rigs.sh}"
NOTIFY="${SDLC_CROSS_BATCH_DEP_WATCHER_NOTIFY:-$SCRIPT_DIR/sdlc-notify.sh}"

if [ ! -x "$RIG_LISTER" ]; then
    echo "cross-batch-dep-watcher: rig-lister missing at $RIG_LISTER" >&2
    exit 0
fi

# Terminal merge-equivalent states. Any predecessor in one of these states
# unblocks its successor for that edge. The successor admits to the
# reconciler only when ALL predecessors are terminal.
_is_terminal_state() {
    case "$1" in
        merged|merged_delayed|branch_ready_no_pr) return 0 ;;
        *) return 1 ;;
    esac
}

# Per-bead check. Returns 0 if the bead was admitted (defer cleared);
# returns 1 if left deferred; logs decisions to stdout.
process_bead() {
    local rig="$1"
    local bead_id="$2"
    local preds_csv="$3"

    local all_terminal=1
    local any_rejected=0
    local rejected_pred=""
    local rejected_pr_url=""

    IFS=',' read -ra preds <<<"$preds_csv"
    for pred in "${preds[@]}"; do
        [ -z "$pred" ] && continue
        local pred_json pred_state pred_pr_url
        pred_json=$(gc bd --rig "$rig" show "$pred" --json 2>/dev/null || echo "[]")
        if ! echo "$pred_json" | jq -e 'type == "array" and length > 0' >/dev/null 2>&1; then
            echo "cross-batch-dep-watcher: $bead_id predecessor $pred not found; leaving deferred" >&2
            all_terminal=0
            continue
        fi
        pred_state=$(echo "$pred_json" | jq -r '.[0].metadata.final_state // ""')
        pred_pr_url=$(echo "$pred_json" | jq -r '.[0].metadata.pr_url // ""')

        if _is_terminal_state "$pred_state"; then
            continue
        fi

        # Detect rejected-PR case: pr_open_for_human + GitHub state CLOSED-not-merged.
        if [ "$pred_state" = "pr_open_for_human" ] && [ -n "$pred_pr_url" ]; then
            local pr_state pr_merged_at
            local pr_view
            pr_view=$(gh pr view "$pred_pr_url" --json state,mergedAt 2>/dev/null || echo "{}")
            pr_state=$(echo "$pr_view" | jq -r '.state // ""')
            pr_merged_at=$(echo "$pr_view" | jq -r '.mergedAt // ""')
            if [ "$pr_state" = "CLOSED" ] && [ -z "$pr_merged_at" ]; then
                any_rejected=1
                rejected_pred="$pred"
                rejected_pr_url="$pred_pr_url"
            fi
        fi

        all_terminal=0
    done

    if [ "$any_rejected" = "1" ]; then
        # Escalation path: mail the operator. Failure to mail is non-blocking
        # — the deferred bead stays deferred for operator review.
        if [ -n "${SDLC_NOTIFY_RECIPIENT:-}" ] && [ -x "$NOTIFY" ]; then
            SDLC_NOTIFY_RECIPIENT="$SDLC_NOTIFY_RECIPIENT" \
                "$NOTIFY" --subject "[$rig] cross-batch successor stuck: predecessor PR rejected" <<EOF
Bead $bead_id (rig $rig) is deferred pending merge of cross-batch
predecessor $rejected_pred.

The predecessor's PR ($rejected_pr_url) is CLOSED without merge — operator
action required.

Options:
- Reopen and merge the predecessor's PR; this watcher will admit the
  successor on the next tick.
- Or remove the deferral and re-route the successor:
    gc bd --rig $rig update $bead_id --defer "" \\
        --unset-metadata cross_batch_dep_predecessors

Per pack #154.
EOF
            local notify_rc=$?
            if [ "$notify_rc" != "0" ]; then
                echo "cross-batch-dep-watcher: notify failed (rc=$notify_rc) for $bead_id" >&2
            fi
        else
            echo "cross-batch-dep-watcher: $bead_id needs operator review (predecessor PR rejected) — SDLC_NOTIFY_RECIPIENT not set" >&2
        fi
        echo "cross-batch-dep-watcher: $bead_id rejected-PR escalation for predecessor $rejected_pred"
        return 1
    fi

    if [ "$all_terminal" = "1" ]; then
        # Admit: clear defer + unset marker.
        gc bd --rig "$rig" update "$bead_id" \
            --defer "" \
            --unset-metadata "cross_batch_dep_predecessors" \
            >/dev/null 2>&1
        echo "cross-batch-dep-watcher: $bead_id admitted (all predecessors terminal)"
        return 0
    fi

    return 1
}

# Per-rig walk: find beads with the marker, dispatch process_bead for each.
walk_rig() {
    local rig="$1"

    local beads_json
    beads_json=$(gc bd --rig "$rig" list --status=open --limit 5000 --json 2>/dev/null || echo "[]")
    if ! echo "$beads_json" | jq -e 'type == "array"' >/dev/null 2>&1; then
        return 0
    fi

    # Filter beads with the cross_batch_dep_predecessors metadata field set.
    # Output: tab-separated bead_id <TAB> predecessors_csv.
    echo "$beads_json" | jq -r '
        .[]
        | select(.metadata.cross_batch_dep_predecessors // "" | length > 0)
        | "\(.id)\t\(.metadata.cross_batch_dep_predecessors)"
    ' | while IFS=$'\t' read -r bead_id preds_csv; do
        [ -z "$bead_id" ] && continue
        process_bead "$rig" "$bead_id" "$preds_csv" || true
    done
}

# Walk every registered rig. The rig-lister emits TSV (name\tpath); empty
# output is tolerated (no rigs registered yet).
while IFS=$'\t' read -r rig_name _rig_path; do
    [ -z "$rig_name" ] && continue
    walk_rig "$rig_name"
done < <(bash "$RIG_LISTER" "$CITY_ROOT" 2>/dev/null)

exit 0
