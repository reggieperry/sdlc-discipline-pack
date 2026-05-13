#!/usr/bin/env bash
# sdlc-cost-rollup.sh — observer hook fired by orders/sdlc-cost-rollup.toml
#
# Triggered on bead.closed. If the closed bead is a phase bead from an SDLC
# chain, append a row to <city>/cost_history.csv. cost_usd is computed by
# scanning the agent session's Claude Code JSONL (see sdlc-cost-helper.py),
# summing tokens by model, and applying Anthropic's published pricing. A
# bead closed before any session JSONL existed (e.g., reaped early) will
# still get a row with cost_usd=0; the row is preserved as an audit trail.
#
# Environment provided by the order trigger:
#   GC_EVENT_TYPE       e.g. "bead.closed"
#   GC_EVENT_SUBJECT    the closed bead ID
#   GC_EVENT_PAYLOAD    JSON payload of the event
#   GC_CITY_ROOT        absolute path to the city root (where cost_history.csv lives)
#
# Schema (cost_history.csv):
#   timestamp, story_id, phase, session_id, duration_seconds, cost_usd, rig
#
# Phase metadata fields read from the closed bead (set by each agent prompt):
#   metadata.<phase>.session_id     (e.g. metadata.implementor.session_id)
#   metadata.<phase>.started_at     ISO 8601 timestamp
#   metadata.<phase>.completed_at   ISO 8601 timestamp
#   metadata.<phase>.cost_usd       optional; 0 if absent (today: always 0)

set -euo pipefail

BEAD_ID="${GC_EVENT_SUBJECT:-}"
CITY_ROOT="${GC_CITY_ROOT:-$(pwd)}"
CSV="$CITY_ROOT/cost_history.csv"

# No bead ID → nothing to do. Order may fire for non-bead events.
[ -z "$BEAD_ID" ] && exit 0

# Read the closed bead. Beads live in per-rig bead stores; plain `bd show`
# from the city root will fail with an error-shape JSON for rig beads. When
# the order trigger sets GC_RIG, route through `gc bd --rig <rig>` so we
# read the right store; otherwise fall back to plain `bd` for city-level
# beads (cooldown trackers, etc.).
if [ -n "${GC_RIG:-}" ] && [ "$GC_RIG" != "-" ]; then
  BEAD_JSON=$(gc bd --rig "$GC_RIG" show "$BEAD_ID" --json 2>/dev/null || true)
else
  BEAD_JSON=$(bd show "$BEAD_ID" --json 2>/dev/null || true)
fi
[ -z "$BEAD_JSON" ] && exit 0

# Defend against bd returning an error-shape object ({"error": "..."}) rather
# than the expected array of bead records. Without this guard, the next jq
# `.[0]` index would crash with "Cannot index object with number" and the
# script would exit nonzero before any row was written.
if ! echo "$BEAD_JSON" | jq -e 'type == "array"' >/dev/null 2>&1; then
  exit 0
fi

# Extract metadata. We're interested in phase beads — beads whose metadata
# names a phase (planner, implementor, tester, reviewer, documenter). For
# story beads themselves (closed by the documenter at end of chain), we
# emit a "story_close" row marking the chain's terminal close time.
METADATA=$(echo "$BEAD_JSON" | jq -r '.[0].metadata // {}')
TITLE=$(echo "$BEAD_JSON" | jq -r '.[0].title // ""')
# Prefer the bead's recorded close time over `now` so live rollup and replay
# both produce timestamps anchored to the event the row represents. Falls
# back to `date -Iseconds` when the field is missing.
BEAD_CLOSED_AT=$(echo "$BEAD_JSON" | jq -r '.[0].closed_at // .[0].updated_at // empty')

# Try each phase name; emit one row if a phase's session_id is present.
emit_row() {
  local phase="$1"
  local session_id="$2"
  local started_at="$3"
  local completed_at="$4"
  local cost_usd="$5"
  local story_id="$6"
  local rig="$7"

  local duration_seconds=0
  if [ -n "$started_at" ] && [ -n "$completed_at" ]; then
    local s e
    s=$(date -d "$started_at" +%s 2>/dev/null || echo 0)
    e=$(date -d "$completed_at" +%s 2>/dev/null || echo 0)
    [ "$e" -gt "$s" ] && duration_seconds=$((e - s))
  fi

  # Initialize the CSV with a header row if it doesn't exist yet.
  if [ ! -f "$CSV" ]; then
    echo "timestamp,story_id,phase,session_id,duration_seconds,cost_usd,rig" > "$CSV"
  fi

  local row_timestamp="${BEAD_CLOSED_AT:-$(date -Iseconds)}"
  echo "$row_timestamp,$story_id,$phase,$session_id,$duration_seconds,${cost_usd:-0},$rig" >> "$CSV"
}

STORY_ID=$(echo "$METADATA" | jq -r '.story_id // empty')
# Fallback: when the closed bead IS the story (documenter's terminal close),
# there's no separate story_id field. Use the bead's own ID.
[ -z "$STORY_ID" ] && STORY_ID="$BEAD_ID"
RIG=$(echo "$METADATA" | jq -r '.rig // empty')
[ -z "$RIG" ] && RIG="${GC_RIG:-unknown}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COST_HELPER="$SCRIPT_DIR/sdlc-cost-helper.py"

# Map each SDLC chain phase to its pool name (the segment under
# .gc/worktrees/<rig>/ where the agent's per-instance worktree lives).
# Must stay in sync with each phase agent's `work_dir` template in
# agents/<phase>/agent.toml.
pool_for_phase() {
  case "$1" in
    worker)     echo "sdlc" ;;
    tester)     echo "sdlc-testers" ;;
    reviewer)   echo "sdlc-reviewers" ;;
    documenter) echo "sdlc-documenters" ;;
    finalizer)  echo "sdlc-finalizers" ;;
    *)          echo "" ;;
  esac
}

# Sum cost_usd across all agent-instance worktrees under <city>/.gc/worktrees/<rig>/<pool>/
# whose Claude Code JSONLs fall in the phase's time window. Only one instance
# typically has matching JSONLs (the one that ran the session), but multiple
# are tolerated for restart cases.
compute_phase_cost() {
  local phase="$1" started="$2" completed="$3" rig="$4"
  local pool total instance_cost
  pool=$(pool_for_phase "$phase")
  [ -z "$pool" ] && { echo "0"; return; }
  local pool_dir="$CITY_ROOT/.gc/worktrees/$rig/$pool"
  if [ ! -d "$pool_dir" ] || [ -z "$started" ] || [ -z "$completed" ] || [ ! -f "$COST_HELPER" ]; then
    echo "0"; return
  fi
  total="0"
  for instance_dir in "$pool_dir"/*/; do
    [ -d "$instance_dir" ] || continue
    instance_cost=$(python3 "$COST_HELPER" \
      --worktree "$instance_dir" \
      --started-at "$started" \
      --completed-at "$completed" 2>/dev/null || echo "0")
    total=$(awk -v a="$total" -v b="$instance_cost" 'BEGIN{ printf "%.4f", a+b }')
  done
  echo "$total"
}

for phase in worker tester reviewer documenter finalizer; do
  SESSION=$(echo "$METADATA" | jq -r --arg p "$phase" '.["\($p).session_id"] // empty')
  if [ -n "$SESSION" ] && [ "$SESSION" != "null" ]; then
    STARTED=$(echo "$METADATA" | jq -r --arg p "$phase" '.["\($p).started_at"] // empty')
    COMPLETED=$(echo "$METADATA" | jq -r --arg p "$phase" '.["\($p).completed_at"] // empty')
    COST=$(compute_phase_cost "$phase" "$STARTED" "$COMPLETED" "$RIG")
    emit_row "$phase" "$SESSION" "$STARTED" "$COMPLETED" "$COST" "$STORY_ID" "$RIG"
  fi
done

exit 0
