#!/usr/bin/env bash
# sdlc-cost-session.sh [--since <iso-or-relative>] [--until <iso-or-relative>]
#
# Time-window cost summary: per-story breakdown + grand total for the window.
# "Session" here = an operator-defined time window, not a Gas City session.
#
# Defaults: --since 24h ago, --until now.

set -euo pipefail

SINCE_ARG=""
UNTIL_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --since) SINCE_ARG="$2"; shift 2;;
    --until) UNTIL_ARG="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 1;;
  esac
done

[ -z "$SINCE_ARG" ] && SINCE_ARG="24 hours ago"
[ -z "$UNTIL_ARG" ] && UNTIL_ARG="now"

SINCE_TS=$(date -d "$SINCE_ARG" +%s)
UNTIL_TS=$(date -d "$UNTIL_ARG" +%s)

CITY_ROOT="${GC_CITY_ROOT:-$(pwd)}"
CSV="$CITY_ROOT/cost_history.csv"
[ ! -f "$CSV" ] && { echo "no cost_history.csv at $CSV" >&2; exit 1; }

awk -F, -v since="$SINCE_TS" -v until="$UNTIL_TS" '
NR == 1 { next }
{
  cmd = "date -d \"" $1 "\" +%s"; cmd | getline ts; close(cmd)
  if (ts >= since && ts <= until) {
    story_dur[$2] += $5
    story_cost[$2] += $6
    total_dur += $5
    total_cost += $6
  }
}
END {
  printf "%-12s %-10s %-10s\n", "STORY", "DURATION", "COST_USD"
  for (s in story_dur) {
    printf "%-12s %5ds       $%.4f\n", s, story_dur[s], story_cost[s]
  }
  printf "%-12s %5ds       $%.4f\n", "TOTAL", total_dur, total_cost
}
' "$CSV"
