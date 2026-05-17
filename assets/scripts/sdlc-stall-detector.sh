#!/usr/bin/env bash
# sdlc-stall-detector.sh — periodic check for bead-phase SLO violations
#
# Fired on a cooldown (15m default; configured in orders/sdlc-stall-detector.toml).
# For each registered, non-suspended rig, runs sdlc-stall-detector.py against
# the rig's bd database. Per-rig invocation gives the Python script the
# rig context for the alert subject lines.
#
# Why this exists: chains can stall silently mid-phase — claude crashes
# after partial progress, supervisor reconciler misses a tick, an order
# doesn't fire. Pack #44 sub-stories 1-3 ship success-side notifications
# (chain completes / parks for review) but not the silent-failure case.
# This script is the silent-failure visibility layer.
#
# Tunable: SDLC_STALL_DETECTOR_ENABLED (default "true"). When "false",
# exits at line one without scanning. SDLC_STALL_SLO_OVERRIDE
# (comma-separated `phase=minutes` pairs) tunes individual phase SLOs
# per rig.

set -euo pipefail

if [ "${SDLC_STALL_DETECTOR_ENABLED:-true}" != "true" ]; then
    exit 0
fi

CITY_ROOT="${GC_CITY_ROOT:-}"
if [ -z "$CITY_ROOT" ] || [ ! -d "$CITY_ROOT" ]; then
    echo "sdlc-stall-detector: GC_CITY_ROOT not set or missing; cannot enumerate rigs" >&2
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DETECTOR_PY="$SCRIPT_DIR/sdlc-stall-detector.py"
if [ ! -f "$DETECTOR_PY" ]; then
    echo "sdlc-stall-detector: detector module not found at $DETECTOR_PY" >&2
    exit 0
fi

# Enumerate rigs. Skip the HQ rig (city's own beads database; chain
# stories don't live there) and any suspended rig.
RIGS_JSON=$(cd "$CITY_ROOT" && gc rig list --json 2>/dev/null || echo "")
if [ -z "$RIGS_JSON" ]; then
    echo "sdlc-stall-detector: gc rig list returned empty; nothing to scan" >&2
    exit 0
fi

echo "$RIGS_JSON" | jq -c '.rigs[]? | select(.is_hq != true and .suspended != true) | {name, path}' | \
while read -r rig_entry; do
    rig_name=$(echo "$rig_entry" | jq -r '.name')
    rig_path=$(echo "$rig_entry" | jq -r '.path')
    if [ ! -d "$rig_path" ]; then
        continue
    fi
    (
        cd "$rig_path"
        GC_RIG="$rig_name" python3 "$DETECTOR_PY" || true
    )
done
