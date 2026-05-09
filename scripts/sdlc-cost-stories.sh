#!/usr/bin/env bash
# sdlc-cost-stories.sh [--rig <name>]
#
# Across-stories cost summary, optionally filtered by rig.
# Per-story breakdown + grand total.

set -euo pipefail

RIG_FILTER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --rig) RIG_FILTER="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 1;;
  esac
done

CITY_ROOT="${GC_CITY_ROOT:-$(pwd)}"
CSV="$CITY_ROOT/cost_history.csv"
[ ! -f "$CSV" ] && { echo "no cost_history.csv at $CSV" >&2; exit 1; }

awk -F, -v rig="$RIG_FILTER" '
NR == 1 { next }
{
  if (rig != "" && $7 != rig) next
  story_dur[$2] += $5
  story_cost[$2] += $6
  total_dur += $5
  total_cost += $6
}
END {
  printf "%-12s %-10s %-10s\n", "STORY", "DURATION", "COST_USD"
  for (s in story_dur) {
    printf "%-12s %5ds       $%.4f\n", s, story_dur[s], story_cost[s]
  }
  printf "%-12s %5ds       $%.4f\n", "TOTAL", total_dur, total_cost
}
' "$CSV"
