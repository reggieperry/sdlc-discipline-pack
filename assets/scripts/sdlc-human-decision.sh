#!/bin/sh
# sdlc-human-decision.sh — recorded exit lever for a bead parked under
# requires_human_decision (pack #198; pairs with the #197 kickoff guard).
#
# When a chain phase escalates a decision it cannot make (e.g. a gate block
# with no chain path), it sets requires_human_decision=true and the kickoff
# guard refuses to re-route the bead. This helper is how a human un-parks
# it through a recorded action instead of an ad-hoc `bd update`, so the
# audit trail shows who decided what, when, and why.
#
# Usage:
#   sdlc-human-decision.sh resolve <bead-id> --action merge|rescope|waive [--reason "..."]
#
# Actions (all clear the park and record action + timestamp + optional reason):
#   merge    operator accepts the branch as-is (e.g. a verified gate
#            false-positive) and performs the merge out of band. Status
#            unchanged.
#   rescope  re-opens the bead (--status=open) so a fresh kickoff re-routes
#            it for re-work against an amended spec.
#   waive    records an accepted gate exception (use --reason). Status
#            unchanged.
#
# The helper never performs the git merge itself — that stays the
# operator's action; this records the decision and releases the park.

set -eu

usage() {
    echo "usage: sdlc-human-decision.sh resolve <bead-id> --action merge|rescope|waive [--reason \"...\"]" >&2
    exit 2
}

[ "${1:-}" = "resolve" ] || usage
shift
BEAD="${1:-}"
[ -n "$BEAD" ] || usage
shift

ACTION=""
REASON=""
while [ $# -gt 0 ]; do
    case "$1" in
        --action) ACTION="${2:-}"; shift 2 ;;
        --reason) REASON="${2:-}"; shift 2 ;;
        *) echo "sdlc-human-decision: unknown argument '$1'" >&2; usage ;;
    esac
done

case "$ACTION" in
    merge | rescope | waive) ;;
    *)
        echo "sdlc-human-decision: --action must be one of merge|rescope|waive (got '${ACTION:-}')" >&2
        exit 2
        ;;
esac

# Verify the bead exists before touching it.
if ! bd show "$BEAD" --json >/dev/null 2>&1; then
    echo "sdlc-human-decision: bead '$BEAD' not found" >&2
    exit 1
fi

NOW=$(date -Iseconds)

# Build the bd update argv. All actions clear the park and record the
# decision; rescope additionally re-opens the bead for re-routing.
set -- "$BEAD" \
    --set-metadata requires_human_decision=resolved \
    --set-metadata "human_decision_action=$ACTION" \
    --set-metadata "human_decision_at=$NOW"
if [ -n "$REASON" ]; then
    set -- "$@" --set-metadata "human_decision_reason=$REASON"
fi
if [ "$ACTION" = "rescope" ]; then
    set -- "$@" --status=open
fi

bd update "$@" >/dev/null

echo "sdlc-human-decision: $BEAD resolved (action=$ACTION). requires_human_decision cleared."
case "$ACTION" in
    rescope) echo "  Bead re-opened. Re-route with the kickoff script to re-spawn the chain." ;;
    merge) echo "  Perform the merge out of band, then reconcile the bead (final_state=merged, merged_pr) + spec." ;;
    waive) echo "  Gate exception recorded${REASON:+ — $REASON}." ;;
esac
