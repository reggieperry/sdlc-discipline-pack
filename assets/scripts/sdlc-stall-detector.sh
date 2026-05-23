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

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CITY_ROOT=$(bash "$SCRIPT_DIR/lib/sdlc-find-city-root.sh" 2>/dev/null) || CITY_ROOT=""
if [ -z "$CITY_ROOT" ] || [ ! -d "$CITY_ROOT" ]; then
    echo "sdlc-stall-detector: cannot resolve city root (GC_CITY_ROOT='${GC_CITY_ROOT:-}' PWD='$PWD'); aborting" >&2
    exit 1
fi
DETECTOR_PY="$SCRIPT_DIR/sdlc-stall-detector.py"
if [ ! -f "$DETECTOR_PY" ]; then
    echo "sdlc-stall-detector: detector module not found at $DETECTOR_PY" >&2
    exit 0
fi

# Enumerate non-HQ, non-suspended rigs via the shared library. The
# library handles dual-shape filtering (hq OR is_hq) — prior to its
# extraction this script filtered on `is_hq` while the other three
# rig-enumerating scripts used `hq`; gc actually returns `hq`, so this
# script's HQ filter silently no-op'd (no rigs were ever excluded as
# HQ). The library closes that drift.
RIG_LISTER="$SCRIPT_DIR/lib/sdlc-list-rigs.sh"
while IFS=$'\t' read -r rig_name rig_path; do
    [ -z "$rig_name" ] && continue
    if [ ! -d "$rig_path" ]; then
        continue
    fi
    (
        cd "$rig_path"
        GC_RIG="$rig_name" python3 "$DETECTOR_PY" || true
    )
done < <(bash "$RIG_LISTER" "$CITY_ROOT")
