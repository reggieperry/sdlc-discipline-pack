#!/usr/bin/env bash
# sdlc-slop-skip-emit.sh — emit the slop-reviewer skip trailer and route to the
# documenter (G0 Arm-C: extracted from slop-reviewer prompt step 4, skip branch).
#
# Runs only when the skip-trivial gate (sdlc-slop-skip-trivial.sh) decided to
# skip the slop pass. It:
#   1. resolves the bead's review_file from metadata (bd show <id> --json | jq),
#   2. appends a JSON "## Slop trailer" recording {"skipped": true, "reason": ...},
#   3. git add / commit / push the review file to origin/<BRANCH>,
#   4. bd update the story to route it onward — status=open, assignee cleared,
#      slop-reviewer.completed_at, slop-reviewer.skipped=true, and
#      gc.routed_to=${GC_RIG}/sdlc-discipline.documenter,
#   5. gc runtime drain-ack.
#
# Usage:
#   sdlc-slop-skip-emit.sh <STORY_ID> <BRANCH>
#
# Exits non-zero (fail-closed) on a missing STORY_ID or BRANCH, or on any
# failed step (git/bd/gc). No generative logic — orchestration only.
set -euo pipefail

STORY_ID="${1:-}"
if [ -z "$STORY_ID" ]; then
    echo "sdlc-slop-skip-emit.sh: missing STORY_ID argument" >&2
    exit 2
fi

BRANCH="${2:-}"
if [ -z "$BRANCH" ]; then
    echo "sdlc-slop-skip-emit.sh: missing BRANCH argument" >&2
    exit 2
fi

PHASE="slop-reviewer"
RIG="${GC_RIG:-unknown}"

REVIEW_FILE=$(bd show "$STORY_ID" --json | jq -r '.[0].metadata.review_file')
if [ -z "$REVIEW_FILE" ] || [ "$REVIEW_FILE" = "null" ]; then
    echo "sdlc-slop-skip-emit.sh: no review_file metadata on $STORY_ID" >&2
    exit 3
fi

cat >> "$REVIEW_FILE" <<'EOF'

## Slop trailer
{"skipped": true, "reason": "trivial diff (frontmatter / docstring-only / dep-bump / archive-move)"}
EOF

git add "$REVIEW_FILE"
git commit -q -m "slop-review: skipped (trivial diff) for $STORY_ID"
git push origin "$BRANCH"

bd update "$STORY_ID" \
    --status=open \
    --assignee "" \
    --set-metadata "${PHASE}.completed_at=$(date -Iseconds)" \
    --set-metadata "${PHASE}.skipped=true" \
    --set-metadata "gc.routed_to=${RIG}/sdlc-discipline.documenter"

gc runtime drain-ack
