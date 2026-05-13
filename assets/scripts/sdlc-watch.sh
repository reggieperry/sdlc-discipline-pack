#!/bin/sh
# sdlc-watch.sh — print phase transitions for a chained SDLC bead as they happen.
#
# Usage: sdlc-watch.sh <bead-id> [poll-seconds]
#
# Polls `bd show <bead-id> --json` every <poll-seconds> (default 30) and
# prints a one-line update whenever the bead's current_step changes or it
# closes. Exits when status reaches closed or escalated, or on EOT (Ctrl-C).
#
# Run from inside the rig directory (where `bd` resolves to the rig's bead
# store). The script otherwise is rig-agnostic — useful from any of the
# pack's chain rigs.

set -eu

BEAD="${1:?usage: sdlc-watch.sh <bead-id> [poll-seconds]}"
INTERVAL="${2:-30}"

if ! command -v bd >/dev/null 2>&1; then
    echo "sdlc-watch: bd not on PATH" >&2
    exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
    echo "sdlc-watch: jq not on PATH" >&2
    exit 1
fi

echo "sdlc-watch: $BEAD (polling every ${INTERVAL}s; Ctrl-C to stop)"

# Use a sentinel value that can't be a real step or status so the first
# iteration always prints. POSIX-portable.
LAST_STEP=""
LAST_STATUS=""

while true; do
    JSON=$(bd show "$BEAD" --json 2>/dev/null || true)
    if [ -z "$JSON" ]; then
        echo "$(date +%H:%M:%S)  bd show returned empty — retrying"
        sleep "$INTERVAL"
        continue
    fi

    STATUS=$(printf '%s' "$JSON" | jq -r '.[0].status // "unknown"')
    STEP=$(printf '%s' "$JSON" | jq -r '.[0].metadata.current_step // "-"')
    VERDICT=$(printf '%s' "$JSON" | jq -r '.[0].metadata."gate.verdict" // "-"')
    PR=$(printf '%s' "$JSON" | jq -r '.[0].metadata.pr_url // "-"')
    FINAL=$(printf '%s' "$JSON" | jq -r '.[0].metadata.final_state // "-"')

    if [ "$STEP" != "$LAST_STEP" ] || [ "$STATUS" != "$LAST_STATUS" ]; then
        printf '%s  status=%s  step=%s  gate=%s  final=%s  pr=%s\n' \
            "$(date +%H:%M:%S)" "$STATUS" "$STEP" "$VERDICT" "$FINAL" "$PR"
        LAST_STEP="$STEP"
        LAST_STATUS="$STATUS"
    fi

    case "$STATUS" in
        closed|escalated)
            echo "sdlc-watch: $BEAD reached terminal status '$STATUS' — exiting"
            exit 0
            ;;
    esac

    sleep "$INTERVAL"
done
