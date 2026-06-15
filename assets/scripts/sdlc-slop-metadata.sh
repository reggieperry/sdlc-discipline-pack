#!/usr/bin/env bash
# sdlc-slop-metadata.sh — record the slop-reviewer phase's start metadata on the
# story bead (G0 Arm-C: extracted from slop-reviewer prompt step 2).
#
# Sets two metadata keys on the story so the chain and the operator can see the
# phase ran and when:
#   slop_reviewer.session_id  = $GC_SESSION_ID  (or "unknown" if unset/empty)
#   slop_reviewer.started_at  = ISO-8601 timestamp
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

# Metadata-key namespace — underscore form, because bd rejects keys with a hyphen
# (must match [a-zA-Z_][a-zA-Z0-9_.]*). The hyphenated "slop-reviewer" is the pool
# name (a routing value), not a valid metadata key; the incumbent prompt used it
# here, so its writes silently failed in production.
MKEY="slop_reviewer"
bd update "$STORY_ID" \
    --set-metadata "${MKEY}.session_id=${GC_SESSION_ID:-unknown}" \
    --set-metadata "${MKEY}.started_at=$(date -Iseconds)"
