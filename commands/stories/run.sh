#!/usr/bin/env bash
# sdlc-stories.sh <subcommand> [args...]
#
# Human-facing wrapper around the stories.py bridge that ships in the pack
# overlay. Dispatches to:
#   stories validate                Validate the stories/ directory
#   stories file [IDs...]           File ready stories into bd as beads
#   stories file --phase N          File all status=ready stories in phase N
#   stories file --dry-run [...]    Print the graph plan JSON; no bd call
#   stories ready                   Show ready set joined with story-file paths
#   stories archive <ID> [--pr ...] [--sha ...]   Move a closed story file
#   stories graph [--id ID] [--output PATH]       Render dep graph HTML
#
# Run from anywhere within a rig that has stories/.

set -euo pipefail

# Find the bridge script. In a chain-agent worktree it's at
# .claude/sdlc-discipline/stories.py (materialized by the overlay). For
# interactive use from the rig root it lives in the same place.
BRIDGE=""
HERE="$(pwd)"
while [ "$HERE" != "/" ]; do
  if [ -f "$HERE/.claude/sdlc-discipline/stories.py" ]; then
    BRIDGE="$HERE/.claude/sdlc-discipline/stories.py"
    break
  fi
  HERE="$(dirname "$HERE")"
done

if [ -z "$BRIDGE" ]; then
  echo "stories: bridge script not found." >&2
  echo "Expected at <rig-root>/.claude/sdlc-discipline/stories.py" >&2
  echo "(materialized by the pack overlay at session-spawn)." >&2
  exit 1
fi

exec python3 "$BRIDGE" "$@"
