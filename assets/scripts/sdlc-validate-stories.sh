#!/usr/bin/env bash
# sdlc-validate-stories.sh — schema-enforce VALID_STATUSES on story specs.
#
# Wraps `python3 <pack>/.claude/sdlc-discipline/stories.py validate` and
# propagates the exit code. Two invocation modes:
#
#   1. Pre-commit hook (rig-side install). Runs only when the staged diff
#      touches stories/*.md. Skips otherwise so unrelated commits aren't gated.
#
#      Install:
#          ln -sf <rig>/<pack>/assets/scripts/sdlc-validate-stories.sh \
#                 .git/hooks/pre-commit
#
#   2. Chain self-audit (finalizer prompt). Run unconditionally — the
#      finalizer touches the spec immediately before merge and must verify
#      no out-of-schema status values landed during the chain.
#
# The validator at stories.py:237 enforces:
#   - status: in VALID_STATUSES = {draft, ready, filed, in-flight, merged, closed}
#   - story_id, title, status frontmatter fields present
#   - dependency story_ids resolve
#   - sensitive_files entries match the rig's .claude/rules/project/sensitive-files.md
#     (NOT architecture.toml — that's a separate, opt-in tech-debt-automation config)
#
# Closes pack issue #90.

set -uo pipefail

# Locate the rig + the stories.py bridge. Walk up from cwd looking for the
# pack-overlay materialization at .claude/sdlc-discipline/stories.py.
BRIDGE=""
HERE="$(pwd)"
while [ "$HERE" != "/" ] && [ -n "$HERE" ]; do
    if [ -f "$HERE/.claude/sdlc-discipline/stories.py" ]; then
        BRIDGE="$HERE/.claude/sdlc-discipline/stories.py"
        RIG_ROOT="$HERE"
        break
    fi
    HERE="$(dirname "$HERE")"
done

if [ -z "$BRIDGE" ]; then
    # Bridge not materialized — rig may not use the pack, or the overlay
    # hasn't been synced. Exit clean rather than fail unrelated commits.
    exit 0
fi

# Pre-commit mode: only run when stories/*.md is in the staged set.
# Detect pre-commit context by checking if STDIN is a TTY (interactive run)
# vs invocation from git (no TTY). When SDLC_VALIDATE_STORIES_FORCE=1, skip
# the gate and always run (used by the chain self-audit).
if [ "${SDLC_VALIDATE_STORIES_FORCE:-0}" != "1" ]; then
    # Pre-commit gate: skip when no stories/ change is staged.
    if ! git -C "$RIG_ROOT" diff --cached --name-only 2>/dev/null | grep -q "^stories/"; then
        exit 0
    fi
fi

cd "$RIG_ROOT"
exec python3 "$BRIDGE" validate
