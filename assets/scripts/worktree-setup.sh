#!/bin/sh
# worktree-setup.sh — idempotent git worktree creation for sdlc-discipline pool agents.
#
# Usage: worktree-setup.sh <rig-root> <target-dir> <agent-name>
#
# Adapted from gastown's canonical worktree-setup.sh (examples/gastown/packs/
# gastown/assets/scripts/worktree-setup.sh in gastownhall/gascity). Handles
# the case where Gas City's supervisor pre-creates the target directory and
# populates it with .gc/ runtime files before pre_start runs — those files
# are staged aside, the worktree is created at the (now-empty) target path,
# and the staged content is merged back on top.
#
# Called from pre_start in agent.toml. Runs before the session is created
# so the agent starts IN the worktree directory.

set -eu

RIG_ROOT="${1:?usage: worktree-setup.sh <rig-root> <target-dir> <agent-name>}"
WT="${2:?missing target-dir}"
AGENT="${3:?missing agent-name}"

branch_name() {
    # Namespace worktree branches by target path so multiple cities or rigs
    # can share one underlying repo without colliding on global refs.
    HASH=$(printf '%s' "$WT" | git -C "$RIG_ROOT" hash-object --stdin | cut -c1-12)
    printf 'gc-%s-%s' "$AGENT" "$HASH"
}

# Idempotent: skip if worktree already exists.
if [ -d "$WT/.git" ] || [ -f "$WT/.git" ]; then
    exit 0
fi

mkdir -p "$(dirname "$WT")"

STAGE=""

merge_stage_entry() (
    SRC="$1"
    DST="$2"

    if [ -d "$SRC" ]; then
        mkdir -p "$DST"
        for ENTRY in "$SRC"/.[!.]* "$SRC"/..?* "$SRC"/*; do
            [ -e "$ENTRY" ] || continue
            merge_stage_entry "$ENTRY" "$DST/$(basename "$ENTRY")"
        done
        rmdir "$SRC" 2>/dev/null || true
        exit 0
    fi

    if [ -e "$DST" ]; then
        exit 0
    fi
    mv "$SRC" "$DST"
)

restore_stage() {
    [ -n "$STAGE" ] || return 0
    mkdir -p "$WT"
    for ENTRY in "$STAGE"/.[!.]* "$STAGE"/..?* "$STAGE"/*; do
        [ -e "$ENTRY" ] || continue
        merge_stage_entry "$ENTRY" "$WT/$(basename "$ENTRY")"
    done
    rmdir "$STAGE" 2>/dev/null || true
    STAGE=""
}

# If the target directory already exists with content (Gas City supervisor
# pre-created it and dropped .gc/ runtime files inside), stage that content
# aside so git worktree add has a clean target.
if [ -d "$WT" ] && [ "$(find "$WT" -mindepth 1 -maxdepth 1 | head -n 1)" ]; then
    STAGE=$(mktemp -d "$(dirname "$WT")/.gascity-worktree-stage.XXXXXX")
    find "$WT" -mindepth 1 -maxdepth 1 -exec mv {} "$STAGE"/ \;
    trap 'restore_stage' EXIT HUP INT TERM
fi

rmdir "$WT" 2>/dev/null || true
git -C "$RIG_ROOT" worktree prune >/dev/null 2>&1 || true

BRANCH=$(branch_name)

# Determine the upstream default branch ref and refresh it so the worktree
# branch is created from the remote tip when origin is configured. Without
# origin, fall back to local HEAD.
DEFAULT_REF=$(git -C "$RIG_ROOT" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null || true)
if [ -n "$DEFAULT_REF" ]; then
    DEFAULT_BRANCH=${DEFAULT_REF#refs/remotes/origin/}
    git -C "$RIG_ROOT" fetch origin "$DEFAULT_BRANCH" >/dev/null 2>&1 || true
fi

if git -C "$RIG_ROOT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    if ! GIT_LFS_SKIP_SMUDGE=1 git -C "$RIG_ROOT" worktree add "$WT" "$BRANCH"; then
        echo "worktree-setup: failed to create worktree at $WT from $RIG_ROOT (branch $BRANCH)" >&2
        restore_stage
        exit 1
    fi
else
    if [ -n "$DEFAULT_REF" ]; then
        WORKTREE_ADD_OK=0
        GIT_LFS_SKIP_SMUDGE=1 git -C "$RIG_ROOT" worktree add "$WT" -b "$BRANCH" "$DEFAULT_REF" && WORKTREE_ADD_OK=1
    else
        # No origin configured. Create from rig's current HEAD.
        WORKTREE_ADD_OK=0
        GIT_LFS_SKIP_SMUDGE=1 git -C "$RIG_ROOT" worktree add "$WT" -b "$BRANCH" && WORKTREE_ADD_OK=1
    fi
    if [ "$WORKTREE_ADD_OK" -ne 1 ]; then
        echo "worktree-setup: failed to create worktree at $WT from $RIG_ROOT (branch $BRANCH)" >&2
        restore_stage
        exit 1
    fi
fi

# Restore any staged content (the supervisor's .gc/ runtime files etc.) on top.
if [ -n "$STAGE" ]; then
    for ENTRY in "$STAGE"/.[!.]* "$STAGE"/..?* "$STAGE"/*; do
        [ -e "$ENTRY" ] || continue
        merge_stage_entry "$ENTRY" "$WT/$(basename "$ENTRY")"
    done
    rm -rf "$STAGE"
    STAGE=""
fi
trap - EXIT HUP INT TERM

# Bead redirect: the worker's bead operations resolve against the rig's bead store.
mkdir -p "$WT/.beads"
echo "$RIG_ROOT/.beads" > "$WT/.beads/redirect"

# Keep runtime files out of git status without mutating the tracked .gitignore.
EXCLUDE=$(git -C "$WT" rev-parse --git-path info/exclude)
case "$EXCLUDE" in
    /*) ;;
    *) EXCLUDE="$WT/$EXCLUDE" ;;
esac
mkdir -p "$(dirname "$EXCLUDE")"
touch "$EXCLUDE"

MARKER="# Gas City worktree infrastructure (local excludes)"
if ! grep -qF "$MARKER" "$EXCLUDE" 2>/dev/null; then
    if [ -s "$EXCLUDE" ] && [ "$(tail -c 1 "$EXCLUDE" 2>/dev/null || true)" != "" ]; then
        printf '\n' >> "$EXCLUDE"
    fi
    printf '%s\n' "$MARKER" >> "$EXCLUDE"
fi

append_exclude() {
    PATTERN="$1"
    grep -qxF "$PATTERN" "$EXCLUDE" 2>/dev/null || printf '%s\n' "$PATTERN" >> "$EXCLUDE"
}

append_exclude ".beads/redirect"
append_exclude ".beads/hooks/"
append_exclude ".beads/formulas/"
append_exclude ".runtime/"
append_exclude ".logs/"
append_exclude "worktrees/"
append_exclude "__pycache__/"
append_exclude ".claude/"
append_exclude ".codex/"
append_exclude ".gemini/"
append_exclude ".opencode/"
append_exclude ".gc/"

exit 0
