#!/usr/bin/env bash
# sdlc-slop-inputs.sh — assemble the slop-reviewer's audit inputs for the
# generative call (G0 Arm-C: extracted from slop-reviewer prompt step 5).
#
# Emits to STDOUT the payload the formula hands to `claude -p` for the audit:
#   === DIFF ===              the cumulative diff (git diff origin/<TARGET>...HEAD)
#   === REVIEWER VERDICT ===  the review file's contents (reviewer's prior verdict)
#   === STORY SPEC ===        the story file's contents, IF the bead carries one
#
# The review file and story file paths come from the story bead's metadata:
#   review_file = .[0].metadata.review_file
#   story_file  = .[0].metadata.story_file   (optional; section omitted if absent)
#
# Usage:
#   sdlc-slop-inputs.sh <STORY_ID> <TARGET>
#
# Exits non-zero (fail-closed) on a missing STORY_ID or TARGET, or when the
# bead carries no review_file.
set -euo pipefail

STORY_ID="${1:-}"
if [ -z "$STORY_ID" ]; then
    echo "sdlc-slop-inputs.sh: missing STORY_ID argument" >&2
    exit 2
fi

TARGET="${2:-}"
if [ -z "$TARGET" ]; then
    echo "sdlc-slop-inputs.sh: missing TARGET argument" >&2
    exit 2
fi

BEAD_JSON=$(bd show "$STORY_ID" --json)
REVIEW_FILE=$(printf '%s' "$BEAD_JSON" | jq -r '.[0].metadata.review_file')
STORY_FILE=$(printf '%s' "$BEAD_JSON" | jq -r '.[0].metadata.story_file // empty')

if [ -z "$REVIEW_FILE" ] || [ "$REVIEW_FILE" = "null" ]; then
    echo "sdlc-slop-inputs.sh: bead $STORY_ID has no review_file metadata" >&2
    exit 3
fi

echo "=== DIFF ==="
git diff "origin/${TARGET}...HEAD"

echo "=== REVIEWER VERDICT ==="
cat "$REVIEW_FILE"

if [ -n "$STORY_FILE" ]; then
    echo "=== STORY SPEC ==="
    cat "$STORY_FILE"
fi
