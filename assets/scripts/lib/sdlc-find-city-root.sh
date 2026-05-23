#!/usr/bin/env bash
# sdlc-find-city-root.sh — resolve a gascity city root via three
# sources, in priority order:
#
#   1. $GC_CITY_ROOT environment variable
#   2. Walk up from $PWD looking for a marker file
#   3. `gc cities` first row (the supervisor's source of truth)
#
# Subprocess-callable shell library. Standalone executable; callers
# `bash "$PACK_DIR/assets/scripts/lib/sdlc-find-city-root.sh"` and
# capture the resolved path on stdout:
#
#     CITY_ROOT=$(bash "$LIB" 2>/dev/null) || exit 1
#
# Inputs:
#   $1                    Optional. Marker file relative to the city
#                         root. Defaults to `city.toml`. Pass
#                         `.gc/events.jsonl` to match the alive-idle
#                         detector's resolution shape.
#   $SDLC_FIND_CITY_GC    Optional. Override the gc binary used for
#                         the `gc cities` fallback. Tests substitute
#                         a fake here.
#
# Outputs:
#   stdout — resolved city root path (one line, no trailing newline
#            via the printf below), OR empty if resolution failed
#   stderr — diagnostics on resolution failure
#   exit 0 — resolved successfully
#   exit 1 — could not resolve through any of the three sources
#
# Why this exists: prior to extraction, three scripts duplicated the
# walk-up resolver (sdlc-stall-detector.sh, sdlc-order-stall-detector.sh,
# sdlc-alive-idle-detector.sh). The first two used `city.toml` as the
# marker; the third used `.gc/events.jsonl`. Marker-as-arg consolidates
# both into one source of truth without forcing a single criterion.

set -uo pipefail

MARKER="${1:-city.toml}"
GC_BIN="${SDLC_FIND_CITY_GC:-gc}"

# Source 1: GC_CITY_ROOT env var (set by gascity when invoking an
# order's exec). Validated against the marker.
CITY_ROOT="${GC_CITY_ROOT:-}"
if [ -n "$CITY_ROOT" ] && [ -e "$CITY_ROOT/$MARKER" ]; then
    printf '%s' "$CITY_ROOT"
    exit 0
fi

# Source 2: walk up from $PWD looking for the marker. Works when the
# script fires from inside the city.
probe="$PWD"
while [ "$probe" != "/" ] && [ -n "$probe" ]; do
    if [ -e "$probe/$MARKER" ]; then
        printf '%s' "$probe"
        exit 0
    fi
    probe="$(dirname "$probe")"
done

# Source 3: `gc cities` first row. Works when the script fires from
# a rig dir (sibling of the city) and walk-up misses.
GC_CITY_FALLBACK=$("$GC_BIN" cities 2>/dev/null | awk 'NR>1 {print $2; exit}')
if [ -n "$GC_CITY_FALLBACK" ] && [ -e "$GC_CITY_FALLBACK/$MARKER" ]; then
    printf '%s' "$GC_CITY_FALLBACK"
    exit 0
fi

echo "sdlc-find-city-root: could not resolve city root (marker='$MARKER', GC_CITY_ROOT='${GC_CITY_ROOT:-}', PWD='$PWD')" >&2
exit 1
