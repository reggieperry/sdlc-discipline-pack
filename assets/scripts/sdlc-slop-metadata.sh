#!/usr/bin/env bash
# sdlc-slop-metadata.sh — record the slop-reviewer phase's start metadata on the
# story bead (G0 Arm-C: extracted from slop-reviewer prompt step 2).
#
# Sets two metadata keys on the story so the chain and the operator can see the
# phase ran and when:
#   slop-reviewer.session_id  = $GC_SESSION_ID  (or "unknown" if unset/empty)
#   slop-reviewer.started_at  = ISO-8601 timestamp
#
# Usage:
#   sdlc-slop-metadata.sh <STORY_ID>
#
# Exits non-zero (fail-closed) on a missing STORY_ID or a failed bd update.
set -euo pipefail

STORY_ID="${1:-}"
if [ -z "$STORY_ID" ]; then
    echo "sdlc-slop-metadata.sh: missing STORY_ID argument" >&2
    exit 2
fi

PHASE="slop-reviewer"
bd update "$STORY_ID" \
    --set-metadata "${PHASE}.session_id=${GC_SESSION_ID:-unknown}" \
    --set-metadata "${PHASE}.started_at=$(date -Iseconds)"
