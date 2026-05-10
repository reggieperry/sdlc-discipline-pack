#!/usr/bin/env bash
# sdlc-cost-rollup.sh — observer hook fired by orders/sdlc-cost-rollup.toml
#
# Triggered on bead.closed. If the closed bead is a phase bead from an SDLC
# chain, append a row to <city>/cost_history.csv. Captures what we can today;
# cost_usd is left as 0 because Gas City v1.1.1 doesn't expose per-session
# token usage in a queryable form. When that lands, update this script to
# fill cost_usd from the right source — schema stays the same.
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

# Read the closed bead. If bd can't find it, skip silently — the bead may
# already be reaped or the event subject may be a non-bead identifier.
BEAD_JSON=$(bd show "$BEAD_ID" --json 2>/dev/null || true)
[ -z "$BEAD_JSON" ] && exit 0

# Extract metadata. We're interested in phase beads — beads whose metadata
# names a phase (planner, implementor, tester, reviewer, documenter). For
# story beads themselves (closed by the documenter at end of chain), we
# emit a "story_close" row marking the chain's terminal close time.
METADATA=$(echo "$BEAD_JSON" | jq -r '.[0].metadata // {}')
TITLE=$(echo "$BEAD_JSON" | jq -r '.[0].title // ""')

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

  echo "$(date -Iseconds),$story_id,$phase,$session_id,$duration_seconds,${cost_usd:-0},$rig" >> "$CSV"
}

STORY_ID=$(echo "$METADATA" | jq -r '.story_id // empty')
# Fallback: when the closed bead IS the story (documenter's terminal close),
# there's no separate story_id field. Use the bead's own ID.
[ -z "$STORY_ID" ] && STORY_ID="$BEAD_ID"
RIG=$(echo "$METADATA" | jq -r '.rig // empty')
[ -z "$RIG" ] && RIG="${GC_RIG:-unknown}"

for phase in planner implementor tester reviewer documenter kickoff; do
  SESSION=$(echo "$METADATA" | jq -r --arg p "$phase" '.["\($p).session_id"] // empty')
  if [ -n "$SESSION" ] && [ "$SESSION" != "null" ]; then
    STARTED=$(echo "$METADATA" | jq -r --arg p "$phase" '.["\($p).started_at"] // empty')
    COMPLETED=$(echo "$METADATA" | jq -r --arg p "$phase" '.["\($p).completed_at"] // empty')
    COST=$(echo "$METADATA" | jq -r --arg p "$phase" '.["\($p).cost_usd"] // "0"')
    emit_row "$phase" "$SESSION" "$STARTED" "$COMPLETED" "$COST" "$STORY_ID" "$RIG"
  fi
done

exit 0
