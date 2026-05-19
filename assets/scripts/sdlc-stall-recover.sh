#!/usr/bin/env bash
# sdlc-stall-recover.sh — operator-invokable stall-recovery checkpoint commit.
#
# Replaces the manual `git add -A && git commit -m "chore(stall-recovery): wip..."`
# pattern that the operator runs during chain-takeover. Stages everything
# EXCEPT a default exclusion list of permission-config files (`.claude/settings.json`
# and adjacent), then commits authored as
# `SDLC Recovery <sdlc-recovery@example.com>` so chain-takeover provenance shows
# in `git log`.
#
# Why this exists: pack #79 traced a recurring drift pattern. Operator-led
# stall recovery was tangling worktree-local `.claude/settings.json` reshapes
# with legitimate phase work, slipping past the reviewer because the per-phase
# stash-on-checkout discipline doesn't fire on the recovery path. The 219-line
# reshape in PR #263 (EL-033) is the canonical evidence. This script makes the
# discipline mechanical instead of memorized.
#
# Usage:
#   sdlc-stall-recover.sh --phase <phase> [--bead-id <id>] [--note <text>] [--dry-run]
#
# Phase names (the chain phase the worker was in when it stalled):
#   load-context plan workspace-setup implement self-audit submit-and-exit
#   tester reviewer documenter finalizer
#
# Default exclusions:
#   .claude/settings.json
#   .claude/settings.local.json
#   .claude/rules/project/architecture.toml
#   .claude/rules/project/sensitive-files.md
#
# Extend exclusions via SDLC_STALL_RECOVERY_EXCLUDES (colon-separated paths).
# Override the git binary via SDLC_STALL_RECOVERY_GIT (default: `git`); used
# by tests.
#
# Exit codes:
#   0   commit created, or dry-run printed cleanly
#   2   argument parse error
#   3   nothing to commit after exclusions
#   4   git command failure

set -u

DEFAULT_EXCLUDES=(
    ".claude/settings.json"
    ".claude/settings.local.json"
    ".claude/rules/project/architecture.toml"
    ".claude/rules/project/sensitive-files.md"
)

PHASE=""
BEAD_ID=""
NOTE=""
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --phase)
            PHASE="$2"
            shift 2
            ;;
        --bead-id)
            BEAD_ID="$2"
            shift 2
            ;;
        --note)
            NOTE="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --help|-h)
            sed -n '2,40p' "$0"
            exit 0
            ;;
        *)
            echo "sdlc-stall-recover: unknown arg '$1'" >&2
            exit 2
            ;;
    esac
done

if [ -z "$PHASE" ]; then
    echo "sdlc-stall-recover: --phase <name> is required" >&2
    exit 2
fi

GIT_BIN="${SDLC_STALL_RECOVERY_GIT:-git}"

# Build the effective exclusion list (defaults plus env overrides).
EXCLUDES=("${DEFAULT_EXCLUDES[@]}")
if [ -n "${SDLC_STALL_RECOVERY_EXCLUDES:-}" ]; then
    IFS=':' read -ra EXTRA <<< "$SDLC_STALL_RECOVERY_EXCLUDES"
    for p in "${EXTRA[@]}"; do
        [ -n "$p" ] && EXCLUDES+=("$p")
    done
fi

# Stage everything, then unstage the excludes. `git reset HEAD <path>` is a
# no-op when the path has no staged change, so iterating the full exclusion
# list per run is safe.
if ! "$GIT_BIN" add -A >/dev/null 2>&1; then
    echo "sdlc-stall-recover: git add -A failed" >&2
    exit 4
fi

EXCLUDED_THAT_HAD_CHANGES=()
for path in "${EXCLUDES[@]}"; do
    # Was this path staged (has any change in the index)? Capture before reset.
    if ! "$GIT_BIN" diff --cached --quiet -- "$path" 2>/dev/null; then
        EXCLUDED_THAT_HAD_CHANGES+=("$path")
    fi
    "$GIT_BIN" reset HEAD -- "$path" >/dev/null 2>&1 || true
done

# What remains staged?
STAGED_FILES="$("$GIT_BIN" diff --cached --name-only 2>/dev/null || true)"
if [ -z "$STAGED_FILES" ]; then
    echo "sdlc-stall-recover: nothing to commit after exclusions" >&2
    if [ "${#EXCLUDED_THAT_HAD_CHANGES[@]}" -gt 0 ]; then
        echo "  excluded (had changes): ${EXCLUDED_THAT_HAD_CHANGES[*]}" >&2
    fi
    exit 3
fi

# Compose the commit message.
SUBJECT="chore(stall-recovery): wip ${PHASE} checkpoint"
BODY=""
if [ -n "$BEAD_ID" ]; then
    BODY="${BODY}Bead: ${BEAD_ID}"$'\n'
fi
if [ -n "$NOTE" ]; then
    [ -n "$BODY" ] && BODY="${BODY}"$'\n'
    BODY="${BODY}${NOTE}"$'\n'
fi
if [ "${#EXCLUDED_THAT_HAD_CHANGES[@]}" -gt 0 ]; then
    [ -n "$BODY" ] && BODY="${BODY}"$'\n'
    BODY="${BODY}Worktree-local changes left out of this checkpoint: ${EXCLUDED_THAT_HAD_CHANGES[*]}"$'\n'
fi

if [ "$DRY_RUN" -eq 1 ]; then
    echo "sdlc-stall-recover: dry-run"
    echo "  phase: $PHASE"
    echo "  subject: $SUBJECT"
    echo "  staged files:"
    echo "$STAGED_FILES" | sed 's/^/    /'
    if [ "${#EXCLUDED_THAT_HAD_CHANGES[@]}" -gt 0 ]; then
        echo "  excluded (had changes):"
        for p in "${EXCLUDED_THAT_HAD_CHANGES[@]}"; do
            echo "    $p"
        done
    fi
    exit 0
fi

# Commit. --no-verify because a stalled chain-recovery checkpoint should not
# block on pre-commit hooks that need a clean tree to function.
if [ -n "$BODY" ]; then
    "$GIT_BIN" -c user.name="SDLC Recovery" -c user.email="sdlc-recovery@example.com" \
        commit -m "$SUBJECT" -m "$BODY" --no-verify
else
    "$GIT_BIN" -c user.name="SDLC Recovery" -c user.email="sdlc-recovery@example.com" \
        commit -m "$SUBJECT" --no-verify
fi
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "sdlc-stall-recover: git commit failed (exit $RC)" >&2
    exit 4
fi

exit 0
