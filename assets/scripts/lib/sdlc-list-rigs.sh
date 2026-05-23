#!/usr/bin/env bash
# sdlc-list-rigs.sh — single source of truth for "list active rigs."
#
# Subprocess-callable shell library. Standalone executable; callers
# `bash "$PACK_DIR/assets/scripts/lib/sdlc-list-rigs.sh"` and iterate
# the TSV output one line per rig:
#
#     <rig-name>\t<rig-path>
#
# Why this exists: prior to this library four scripts each rolled
# their own rig-enumeration (sdlc-stale-pr-sweeper.sh,
# sdlc-zombie-reconciler.sh, sdlc-exhausted-bead-retry.sh,
# sdlc-stall-detector.sh). One of them filtered on `is_hq` while the
# other three filtered on `hq`; gc rig list --json actually returns
# `hq`, so the stall-detector silently skipped no HQ rigs. The dual-
# shape jq filter below is robust against the next schema drift.
#
# Inputs:
#   $1               Optional. City root path. Falls back to
#                    $GC_CITY_ROOT, then $CITY_ROOT.
#   $SDLC_LIST_RIGS_GC   Optional. Override the gc binary used for
#                    enumeration. Tests substitute a fake here.
#
# Outputs:
#   stdout — one TSV line per active rig: <name>\t<path>
#   stderr — diagnostics
#   exit 0 — always (errors go to stderr; consumers tolerate empty
#            output the same way they tolerate "no rigs"). The
#            "always exit 0" contract matches how the prior inline
#            blocks behaved.
#
# Filter shape:
#   - hq != true AND is_hq != true (dual-shape; HQ rig excluded)
#   - suspended != true
#
# Both `hq` and `is_hq` are checked so the library is robust against
# the next gc schema change in either direction; jq's `!=` on a
# missing field evaluates to true, so an rig with neither field set
# is treated as non-HQ (the safe default).

set -uo pipefail

CITY_ROOT="${1:-${GC_CITY_ROOT:-${CITY_ROOT:-}}}"
GC_BIN="${SDLC_LIST_RIGS_GC:-gc}"

if [ -z "$CITY_ROOT" ]; then
    echo "sdlc-list-rigs: no city root (arg 1, GC_CITY_ROOT, or CITY_ROOT)" >&2
    exit 0
fi

if [ ! -d "$CITY_ROOT" ]; then
    echo "sdlc-list-rigs: city root not a directory: $CITY_ROOT" >&2
    exit 0
fi

RIGS_JSON=$(cd "$CITY_ROOT" && "$GC_BIN" rig list --json 2>/dev/null || echo "")
if [ -z "$RIGS_JSON" ]; then
    echo "sdlc-list-rigs: gc rig list returned nothing from $CITY_ROOT" >&2
    exit 0
fi

echo "$RIGS_JSON" | jq -r '
    .rigs[]?
    | select((.hq != true) and (.is_hq != true) and (.suspended != true))
    | [.name, .path]
    | @tsv
' 2>/dev/null || true

exit 0
