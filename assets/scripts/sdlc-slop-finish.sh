#!/usr/bin/env bash
# sdlc-slop-finish.sh — emit the slop_trailer and hand off to the documenter
# (G0 Arm-C: extracted from slop-reviewer prompt steps 7 + 8).
#
# Closes out a slop-review pass that produced a real trailer (the non-skip
# path; the skip path is sdlc-slop-skip-trivial.sh's caller). Resolves the
# story's review file, appends a fenced "## Slop trailer" json block, commits
# and pushes it, then routes the story to the documenter and drains:
#
#   1. read the trailer JSON from TRAILER_JSON_FILE (the step-6 claude -p
#      output, passed via file because it is multi-line),
#   2. resolve review_file from the bead's metadata,
#   3. append a fenced "## Slop trailer\n```json\n<TRAILER>\n```" block,
#   4. git add / commit / push origin BRANCH,
#   5. bd update STORY_ID --status=open --assignee "" with
#        slop-reviewer.completed_at, slop-reviewer.findings_count, and
#        gc.routed_to=<rig>/sdlc-discipline.documenter,
#   6. gc runtime drain-ack.
#
# Usage:
#   sdlc-slop-finish.sh <STORY_ID> <BRANCH> <TRAILER_JSON_FILE> <FINDINGS_COUNT>
#
# Exits non-zero (fail-closed) on a missing argument, an unreadable trailer
# file, an empty/absent review_file, or any failed git/bd/gc step.
set -euo pipefail

STORY_ID="${1:-}"
BRANCH="${2:-}"
TRAILER_JSON_FILE="${3:-}"
FINDINGS_COUNT="${4:-}"

if [ -z "$STORY_ID" ] || [ -z "$BRANCH" ] || [ -z "$TRAILER_JSON_FILE" ] || [ -z "$FINDINGS_COUNT" ]; then
    echo "sdlc-slop-finish.sh: usage: sdlc-slop-finish.sh <STORY_ID> <BRANCH> <TRAILER_JSON_FILE> <FINDINGS_COUNT>" >&2
    exit 2
fi

if [ ! -r "$TRAILER_JSON_FILE" ]; then
    echo "sdlc-slop-finish.sh: trailer file not readable: '$TRAILER_JSON_FILE'" >&2
    exit 2
fi

PHASE="slop-reviewer"
RIG="${GC_RIG:-unknown}"

TRAILER_JSON=$(cat "$TRAILER_JSON_FILE")

REVIEW_FILE=$(bd show "$STORY_ID" --json | jq -r '.[0].metadata.review_file')
if [ -z "$REVIEW_FILE" ] || [ "$REVIEW_FILE" = "null" ]; then
    echo "sdlc-slop-finish.sh: no review_file in metadata for '$STORY_ID'" >&2
    exit 3
fi

# Append the fenced trailer block to the review file.
{
    printf '\n## Slop trailer\n'
    printf '```json\n'
    printf '%s\n' "$TRAILER_JSON"
    printf '```\n'
} >> "$REVIEW_FILE"

git add "$REVIEW_FILE"
git commit -q -m "slop-review: appended trailer for $STORY_ID"
git push origin "$BRANCH"

bd update "$STORY_ID" \
    --status=open \
    --assignee "" \
    --set-metadata "${PHASE}.completed_at=$(date -Iseconds)" \
    --set-metadata "${PHASE}.findings_count=${FINDINGS_COUNT}" \
    --set-metadata "gc.routed_to=${RIG}/sdlc-discipline.documenter"

gc runtime drain-ack
