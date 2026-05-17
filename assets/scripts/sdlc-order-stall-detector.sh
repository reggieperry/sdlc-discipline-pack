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
if [ -z "$CITY_ROOT" ] || [ ! -d "$CITY_ROOT" ]; then
    echo "sdlc-order-stall-detector: GC_CITY_ROOT not set or missing" >&2
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DETECTOR_PY="$SCRIPT_DIR/sdlc-order-stall-detector.py"
if [ ! -f "$DETECTOR_PY" ]; then
    echo "sdlc-order-stall-detector: detector module not found at $DETECTOR_PY" >&2
    exit 0
fi

cd "$CITY_ROOT"
python3 "$DETECTOR_PY" || true
