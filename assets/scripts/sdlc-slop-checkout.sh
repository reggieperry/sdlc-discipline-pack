#!/usr/bin/env bash
# sdlc-slop-checkout.sh — check out the story's branch for the slop-reviewer
# phase (G0 Arm-C: extracted from slop-reviewer prompt step 3).
#
# Reads the branch and target from the story bead's metadata, fetches origin,
# and checks out the branch (creating/resetting the local tracking branch from
# origin, falling back to a plain checkout of an already-local branch).
#
# Echoes the resolved branch and target on stdout so the formula can reuse them:
#   BRANCH=<branch>
#   TARGET=<target>   (defaults to "main" when metadata.target is unset)
#
# Usage:
#   sdlc-slop-checkout.sh <STORY_ID>
#
# Exits non-zero (fail-closed) on a missing STORY_ID or a branch that can't be
# resolved from the bead.
set -euo pipefail

STORY_ID="${1:-}"
if [ -z "$STORY_ID" ]; then
    echo "sdlc-slop-checkout.sh: missing STORY_ID argument" >&2
    exit 2
fi

BRANCH="$(bd show "$STORY_ID" --json | jq -r '.[0].metadata.branch')"
TARGET="$(bd show "$STORY_ID" --json | jq -r '.[0].metadata.target // "main"')"

if [ -z "$BRANCH" ] || [ "$BRANCH" = "null" ]; then
    echo "sdlc-slop-checkout.sh: could not resolve branch for ${STORY_ID}" >&2
    exit 3
fi

git fetch origin
git checkout --track -B "$BRANCH" "origin/$BRANCH" 2>/dev/null || git checkout "$BRANCH"

echo "BRANCH=${BRANCH}"
echo "TARGET=${TARGET}"
