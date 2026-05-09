#!/usr/bin/env bash
# sdlc-watch.sh <story_id> — colorized one-line-per-phase monitor.
#
# Polls a story bead and emits a single line for each meaningful state
# transition. Designed for demos and live debugging. Exits when the story
# closes or after 30 minutes (whichever comes first).
#
# Usage:
#   sdlc-watch.sh cs-2se
#
# Output format:
#   HH:MM:SS  ▶  PHASE   short summary

set -euo pipefail

STORY_ID="${1:-}"
[ -z "$STORY_ID" ] && { echo "usage: sdlc-watch.sh <story_id>" >&2; exit 1; }

# ANSI color codes (skip when stdout isn't a TTY)
if [ -t 1 ]; then
  CYAN='\033[36m'; GREEN='\033[32m'; YELLOW='\033[33m'; MAGENTA='\033[35m'; RED='\033[31m'; BOLD='\033[1m'; RESET='\033[0m'
else
  CYAN=''; GREEN=''; YELLOW=''; MAGENTA=''; RED=''; BOLD=''; RESET=''
fi

# Map phase → emoji + color
phase_decoration() {
  case "$1" in
    planner)     printf '%b📋 PLAN     %b' "$CYAN" "$RESET" ;;
    implementor) printf '%b🔨 BUILD    %b' "$YELLOW" "$RESET" ;;
    tester)      printf '%b🧪 TEST     %b' "$GREEN" "$RESET" ;;
    reviewer)    printf '%b👀 REVIEW   %b' "$MAGENTA" "$RESET" ;;
    documenter)  printf '%b📝 DOCUMENT %b' "$CYAN" "$RESET" ;;
    *)           printf '%b▶  %-9s%b' "$BOLD" "${1^^}" "$RESET" ;;
  esac
}

# Strip prefix sdlc-discipline. for printable agent names.
short_assignee() { echo "$1" | sed 's|.*/sdlc-discipline\.||'; }

prev_state=""
deadline=$(($(date +%s) + 1800))   # 30 minutes

while [ $(date +%s) -lt $deadline ]; do
  json=$(bd show "$STORY_ID" --json 2>/dev/null) || { sleep 3; continue; }
  status=$(jq -r '.[0].status' <<< "$json")
  assignee=$(jq -r '.[0].assignee // "—"' <<< "$json")
  short=$(short_assignee "$assignee")
  plan=$(jq -r '.[0].metadata.plan_file // "—"' <<< "$json")
  branch=$(jq -r '.[0].metadata.branch // "—"' <<< "$json")
  test=$(jq -r '.[0].metadata.test_status // "—"' <<< "$json")
  review=$(jq -r '.[0].metadata.review_verdict // "—"' <<< "$json")
  doc=$(jq -r '.[0].metadata.feature_doc // "—"' <<< "$json")

  cur_state="$status|$short|$plan|$branch|$test|$review|$doc"

  if [ "$cur_state" != "$prev_state" ]; then
    ts=$(date +%H:%M:%S)
    decoration=$(phase_decoration "$short")

    # Compose a single-line summary based on what just changed
    if [ "$prev_state" = "" ]; then
      msg="$decoration  story=$STORY_ID  status=$status  assignee=$short"
    else
      # Identify what's new vs prev
      changed=()
      [ "$plan" != "—" ] && [ "$prev_state" != *"|$plan|"* ] && changed+=("plan=$plan")
      [ "$branch" != "—" ] && [ "$prev_state" != *"|$branch|"* ] && changed+=("branch=$branch")
      [ "$test" != "—" ] && [ "$prev_state" != *"|$test|"* ] && changed+=("test=$test")
      [ "$review" != "—" ] && [ "$prev_state" != *"|$review|"* ] && changed+=("review=$review")
      [ "$doc" != "—" ] && [ "$prev_state" != *"|$doc|"* ] && changed+=("doc=$doc")

      if [ ${#changed[@]} -gt 0 ]; then
        msg="$decoration  ${changed[*]}  → next: $short"
      else
        msg="$decoration  → $short"
      fi
    fi
    printf '%b%s%b  %b\n' "$BOLD" "$ts" "$RESET" "$msg"

    prev_state="$cur_state"
  fi

  if [ "$status" = "closed" ]; then
    ts=$(date +%H:%M:%S)
    printf '%b%s%b  %b%s%b  %s\n' "$BOLD" "$ts" "$RESET" "$BOLD$GREEN" "✓ STORY CLOSED" "$RESET" "chain complete"
    break
  fi
  sleep 4
done
