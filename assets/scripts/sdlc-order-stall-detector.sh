#!/usr/bin/env bash
# sdlc-order-stall-detector.sh — periodic check for cron-order missed fires
#
# Fired on a cooldown (15m default; configured in
# orders/sdlc-order-stall-detector.toml). Reads `gc order list` for the
# set of cooldown-trigger orders, reads `gc order history <name>` for
# each, and alerts via sdlc-notify.sh when an order's last fire is
# older than `interval × 2`.
#
# Companion to sdlc-stall-detector.sh (bead-phase stalls). This one
# catches order-side silent failures — the motivating case is the
# rebase-watcher non-fire from earlier in May 2026 (silent for ~9 hours
# overnight).
#
# Tunable: SDLC_ORDER_STALL_DETECTOR_ENABLED (default "true"). When
# "false", exits at line one without scanning.

set -euo pipefail

if [ "${SDLC_ORDER_STALL_DETECTOR_ENABLED:-true}" != "true" ]; then
    exit 0
fi

CITY_ROOT="${GC_CITY_ROOT:-}"
if [ -z "$CITY_ROOT" ] || [ ! -f "$CITY_ROOT/city.toml" ]; then
    # Walk up from PWD first — works if the controller fires from inside the city.
    probe="$PWD"
    while [ "$probe" != "/" ] && [ -n "$probe" ]; do
        if [ -f "$probe/city.toml" ]; then
            CITY_ROOT="$probe"
            break
        fi
        probe="$(dirname "$probe")"
    done
fi
if [ -z "$CITY_ROOT" ] || [ ! -f "$CITY_ROOT/city.toml" ]; then
    # Fall back to asking the supervisor for registered cities. When the
    # controller fires from a rig dir (sibling of the city), walk-up
    # misses; `gc cities` always knows the answer.
    CITY_ROOT="$(gc cities 2>/dev/null | awk 'NR>1 {print $2; exit}')"
fi
if [ -z "$CITY_ROOT" ] || [ ! -d "$CITY_ROOT" ] || [ ! -f "$CITY_ROOT/city.toml" ]; then
    echo "sdlc-order-stall-detector: cannot resolve city root (GC_CITY_ROOT='${GC_CITY_ROOT:-}' PWD='$PWD'); aborting" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DETECTOR_PY="$SCRIPT_DIR/sdlc-order-stall-detector.py"
if [ ! -f "$DETECTOR_PY" ]; then
    echo "sdlc-order-stall-detector: detector module not found at $DETECTOR_PY" >&2
    exit 0
fi

cd "$CITY_ROOT"
python3 "$DETECTOR_PY" || true
