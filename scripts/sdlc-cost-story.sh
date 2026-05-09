#!/usr/bin/env bash
# sdlc-cost-story.sh <story_id> — show per-phase cost breakdown for one story.
#
# Reads <city>/cost_history.csv. Filters to rows for the story; sums by phase
# and total. Today, cost_usd is always 0 (Gas City v1.1.1 hasn't exposed per-
# session token usage yet). The script prints duration alongside so the row
# is still useful as audit trail.

set -euo pipefail

STORY_ID="${1:-}"
[ -z "$STORY_ID" ] && { echo "usage: sdlc-cost-story <story_id>" >&2; exit 1; }

CITY_ROOT="${GC_CITY_ROOT:-$(pwd)}"
CSV="$CITY_ROOT/cost_history.csv"

[ ! -f "$CSV" ] && { echo "no cost_history.csv at $CSV" >&2; exit 1; }

awk -F, -v sid="$STORY_ID" '
NR == 1 { next }
$2 == sid {
  phase = $3
  duration[phase] += $5
  cost[phase] += $6
  total_dur += $5
  total_cost += $6
  rows++
}
END {
  if (rows == 0) { print "no rows for story " sid; exit 1 }
  printf "%-14s %-12s %-10s\n", "PHASE", "DURATION", "COST_USD"
  for (p in duration) {
    printf "%-14s %5ds       $%.4f\n", p, duration[p], cost[p]
  }
  printf "%-14s %5ds       $%.4f\n", "TOTAL", total_dur, total_cost
}
' "$CSV"
