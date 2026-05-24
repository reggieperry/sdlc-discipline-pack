#!/usr/bin/env bash
# sdlc-cost-rollup-sweep.sh — periodic mop-up replacing the event-triggered
# orders/sdlc-cost-rollup.toml event handler. Workaround for gastownhall/
# gascity#2546: gc supervisor's bead.closed event has not fired for non-wisp
# beads since approximately 2026-05-17, so the event-triggered cost rollup
# silently misses every story chain that ships.
#
# This script:
#   1. Enumerates registered rigs via the shared rig-lister library.
#   2. For each rig, lists closed beads via `gc bd --rig <rig> list --status=closed --json`.
#   3. Reads the cost-history CSV to build a set of (story_id, rig) pairs
#      already recorded.
#   4. For each closed bead whose (story_id, rig) is NOT in that set, invokes
#      the existing per-bead writer (sdlc-cost-rollup.sh) with the right
#      GC_EVENT_SUBJECT + GC_RIG env vars to capture its phase cost rows.
#
# Wisp beads (`*-wisp-*`) are skipped — they are gas-city internal tracking
# beads and never carry phase session metadata.
#
# Idempotency is per (story_id, rig). Once any row for a given pair exists
# in the CSV, the sweep treats the bead as already-processed. Partial
# captures (chain crashed mid-write) are NOT auto-healed by this script —
# they need a manual re-invocation with the affected bead's IDs.
#
# Environment:
#   GC_CITY_ROOT       absolute path to the city root (where cost_history.csv lives).
#                      Provided by the order trigger.
#   SDLC_COST_ROLLUP_SINCE  Optional. ISO date (YYYY-MM-DD). When set, only
#                      beads closed on or after this date are processed.
#                      Default: unset — process all unrecorded closed beads.
#   SDLC_COST_ROLLUP_ENABLED  Optional. Set to "false" to disable the sweep.
#                      Default: "true". The per-bead writer
#                      (sdlc-cost-rollup.sh) stays available for manual use.
#
# When gastownhall/gascity#2546 is fixed and the pack is upgraded past it,
# the event-triggered shape can be restored by reverting
# orders/sdlc-cost-rollup.toml to `trigger = "event"` + `on = "bead.closed"`.
# This sweep can stay as a periodic safety net or be removed; operator's call.

set -euo pipefail

# Feature gate — operator override.
if [ "${SDLC_COST_ROLLUP_ENABLED:-true}" != "true" ]; then
    exit 0
fi

CITY_ROOT="${GC_CITY_ROOT:-}"
if [ -z "$CITY_ROOT" ] || [ ! -d "$CITY_ROOT" ]; then
    echo "cost-rollup-sweep: GC_CITY_ROOT not set or missing; cannot enumerate rigs" >&2
    exit 0
fi

CSV="$CITY_ROOT/cost_history.csv"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RIG_LISTER="${SDLC_COST_ROLLUP_RIG_LISTER:-$SCRIPT_DIR/lib/sdlc-list-rigs.sh}"
PER_BEAD="${SDLC_COST_ROLLUP_PER_BEAD_PATH:-$SCRIPT_DIR/sdlc-cost-rollup.sh}"

if [ ! -x "$PER_BEAD" ]; then
    echo "cost-rollup-sweep: per-bead writer not executable at $PER_BEAD" >&2
    exit 0
fi

# Build the set of (story_id, rig) pairs already recorded in the CSV. The
# sweep uses this to skip beads whose phase rows are already present.
declare -A KNOWN_PAIRS=()
if [ -f "$CSV" ]; then
    while IFS=',' read -r _ts story _phase _sess _dur _cost rig; do
        # Skip header row (story=="story_id") and any empty/malformed rows.
        if [ -n "$story" ] && [ "$story" != "story_id" ] && [ -n "$rig" ]; then
            KNOWN_PAIRS["$story|$rig"]=1
        fi
    done < "$CSV"
fi

SINCE="${SDLC_COST_ROLLUP_SINCE:-}"

sweep_rig() {
    local rig="$1"
    local rig_path="$2"

    # List closed beads in this rig. Tolerate missing/empty output (e.g.,
    # rig has no closed beads yet). The `gc bd --rig` invocation is the
    # canonical way to read rig-scoped bead stores from outside a rig.
    local beads_json
    beads_json=$(gc bd --rig "$rig" list --status=closed --json 2>/dev/null || echo "[]")
    if ! echo "$beads_json" | jq -e 'type == "array"' >/dev/null 2>&1; then
        return 0
    fi

    # Filter:
    #   - exclude wisp beads (gas-city internal tracking)
    #   - apply SDLC_COST_ROLLUP_SINCE date filter if set
    local jq_filter='.[] | select((.id // "") | contains("-wisp-") | not)'
    if [ -n "$SINCE" ]; then
        jq_filter="$jq_filter | select((.closed_at // .updated_at // \"\") >= \"$SINCE\")"
    fi
    jq_filter="$jq_filter | .id"

    local bead_ids
    bead_ids=$(echo "$beads_json" | jq -r "$jq_filter")
    [ -z "$bead_ids" ] && return 0

    while IFS= read -r bid; do
        [ -z "$bid" ] && continue

        # Look up this bead's story_id from its metadata. A bead with no
        # story_id is either the story bead itself (its own ID is the
        # story-id fallback in the per-bead writer) or a non-chain bead.
        local bead_meta story_id
        bead_meta=$(gc bd --rig "$rig" show "$bid" --json 2>/dev/null | jq -r '.[0].metadata // {}' || echo "{}")
        story_id=$(echo "$bead_meta" | jq -r '.story_id // empty')
        [ -z "$story_id" ] && story_id="$bid"

        # Skip if (story_id, rig) already recorded.
        if [ -n "${KNOWN_PAIRS[$story_id|$rig]:-}" ]; then
            continue
        fi

        # Invoke the per-bead writer with the right env. The writer will
        # append rows for every phase session_id it finds in the bead's
        # metadata. After the writer returns, mark the pair recorded so
        # subsequent beads in this loop don't redundantly process.
        GC_EVENT_SUBJECT="$bid" \
        GC_EVENT_TYPE="bead.closed" \
        GC_CITY_ROOT="$CITY_ROOT" \
        GC_RIG="$rig" \
        bash "$PER_BEAD" 2>&1 | sed "s|^|  [$rig:$bid] |" || true

        KNOWN_PAIRS["$story_id|$rig"]=1
    done <<< "$bead_ids"
}

# Walk every registered rig. The rig-lister emits TSV lines (name\tpath);
# tolerate empty output the same way the other periodic sweepers do.
if [ ! -x "$RIG_LISTER" ]; then
    echo "cost-rollup-sweep: rig-lister missing at $RIG_LISTER" >&2
    exit 0
fi

while IFS=$'\t' read -r rig_name rig_path; do
    [ -z "$rig_name" ] && continue
    sweep_rig "$rig_name" "$rig_path"
done < <(bash "$RIG_LISTER" "$CITY_ROOT" 2>/dev/null)

exit 0
