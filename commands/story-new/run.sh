#!/usr/bin/env bash
# sdlc-story-new.sh "<story title>"
#
# Scaffold a story bead with the SDLC entry-point format. Opens $EDITOR with a
# prefilled markdown template, then asks for chain-config metadata (open_pr,
# base_branch), then runs bd create.
#
# Run from inside the rig directory (so bd create files the bead in the
# correct rig's beads database).

set -euo pipefail

TITLE="${1:-}"
[ -z "$TITLE" ] && { echo "usage: sdlc-story-new \"<title>\"" >&2; exit 1; }

EDITOR_CMD="${EDITOR:-${VISUAL:-vi}}"
TEMP_FILE=$(mktemp -t story-XXXXXX.md)
trap 'rm -f "$TEMP_FILE"' EXIT

cat > "$TEMP_FILE" <<EOF
## Outcome

<One-line, user-observable. "Users can <X>" not "refactor <Y>".>

## Acceptance criteria

- <Each criterion is a check the test suite can run, not a vibe.>
- <Three minimum.>
- <Existing behavior preserved unless story explicitly changes it.>

## Scope

**In:** <files / modules the work touches>
**Out:** <explicit exclusions; what this story deliberately does NOT do>

## Sensitive files

<List paths from the rig's sensitive-files list, or "None.">

## Notes

<Optional. Context the planner needs that isn't in Outcome / Acceptance / Scope.>
EOF

echo "Opening editor: $EDITOR_CMD $TEMP_FILE"
"$EDITOR_CMD" "$TEMP_FILE"

# Strip the prefilled template if the user didn't change it.
if grep -q '^<One-line, user-observable.' "$TEMP_FILE"; then
  echo "story template was not edited; aborting." >&2
  exit 1
fi

# Default open_pr from rig env (passed into agents via city.toml patches).
DEFAULT_OPEN_PR="${SDLC_OPEN_PR_DEFAULT:-false}"
read -r -p "open_pr (Y/n) [default: $DEFAULT_OPEN_PR]: " OPEN_PR
OPEN_PR="${OPEN_PR:-$DEFAULT_OPEN_PR}"
case "$OPEN_PR" in
  Y|y|true|TRUE) OPEN_PR="true";;
  *)             OPEN_PR="false";;
esac

read -r -p "base_branch [default: main]: " BASE_BRANCH
BASE_BRANCH="${BASE_BRANCH:-main}"

# Optional glance-merge override (only relevant when open_pr=true).
GLANCE_PROMPT=""
if [ "$OPEN_PR" = "true" ]; then
  DEFAULT_GLANCE="${SDLC_GLANCE_MERGE_DEFAULT:-false}"
  read -r -p "glance_merge (Y/n) [default: $DEFAULT_GLANCE]: " GLANCE_MERGE
  GLANCE_MERGE="${GLANCE_MERGE:-$DEFAULT_GLANCE}"
  case "$GLANCE_MERGE" in
    Y|y|true|TRUE) GLANCE_MERGE="true";;
    *)             GLANCE_MERGE="false";;
  esac
fi

DESCRIPTION=$(cat "$TEMP_FILE")

# bd create the story with metadata.
BEAD_ID=$(bd create "$TITLE" \
  --description "$DESCRIPTION" \
  --set-metadata "open_pr=$OPEN_PR" \
  --set-metadata "base_branch=$BASE_BRANCH" \
  ${GLANCE_MERGE:+--set-metadata "glance_merge=$GLANCE_MERGE"} \
  2>&1 | grep -oE '[a-z]{2}-[a-z0-9]+' | head -1)

[ -z "$BEAD_ID" ] && { echo "bd create failed; check rig dir + beads provider" >&2; exit 1; }

echo ""
echo "✓ Story created: $BEAD_ID"
echo "  open_pr=$OPEN_PR base_branch=$BASE_BRANCH ${GLANCE_MERGE:+glance_merge=$GLANCE_MERGE}"
echo ""
echo "Next: gc sling <rig>/<provider> mol-sdlc --formula --var story_id=$BEAD_ID"
echo "  (or run gc sling <rig>/sdlc-discipline.planner $BEAD_ID for plan-only)"
