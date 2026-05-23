#!/usr/bin/env bash
# sdlc-slop-skip-trivial.sh — gate the slop-reviewer phase (pack #78).
#
# Skips the slop-reviewer when the PR's diff is mechanically trivial.
# Exits 0 → skip; exits 1 → run the slop pass.
#
# Skip conditions (ANY one is sufficient):
#
#   - Diff touches ONLY files matching the trivial allowlist:
#       stories/*.md          (story frontmatter flips)
#       stories/_archive/**   (archive moves)
#       pyproject.toml        (dep version bump alongside uv.lock)
#       uv.lock
#       *.md (root-level)     (README / CONTRIBUTING / etc.)
#
#   - Diff is small AND adds no non-comment code lines:
#       total +/- < SDLC_SLOP_SKIP_LOC_THRESHOLD (default 10)
#       AND no added line under *.py / *.go / *.ts / etc. is a
#       non-blank non-comment statement.
#
# Args:
#   --target REF   Target branch (default main). Diff is origin/REF...HEAD.
#
# Closes pack issue #78 v1 (skip-trivial gate half).

set -uo pipefail

TARGET="main"
while [ $# -gt 0 ]; do
    case "$1" in
        --target)
            TARGET="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

DIFF_FILES=$(git diff --name-only "origin/${TARGET}...HEAD" 2>/dev/null || true)

if [ -z "$DIFF_FILES" ]; then
    # No diff at all — trivial.
    exit 0
fi

# Condition 1: trivial-allowlist match.
all_trivial=1
while IFS= read -r f; do
    [ -z "$f" ] && continue
    case "$f" in
        stories/*.md|stories/_archive/*|pyproject.toml|uv.lock)
            ;;
        *.md)
            # Root-level .md only; nested paths don't match this case
            # (would have matched a more specific pattern above).
            case "$f" in
                */*) all_trivial=0; break;;
            esac
            ;;
        *)
            all_trivial=0
            break
            ;;
    esac
done <<< "$DIFF_FILES"

if [ "$all_trivial" = "1" ]; then
    exit 0
fi

# Condition 2: small diff with no real code lines added.
THRESHOLD="${SDLC_SLOP_SKIP_LOC_THRESHOLD:-10}"
STATS=$(git diff --shortstat "origin/${TARGET}...HEAD" 2>/dev/null || true)
# git diff --shortstat: "5 files changed, 12 insertions(+), 3 deletions(-)"
ADDED=$(echo "$STATS" | grep -oE '[0-9]+ insertion' | head -1 | awk '{print $1}')
DELETED=$(echo "$STATS" | grep -oE '[0-9]+ deletion' | head -1 | awk '{print $1}')
ADDED="${ADDED:-0}"
DELETED="${DELETED:-0}"
TOTAL=$((ADDED + DELETED))

if [ "$TOTAL" -ge "$THRESHOLD" ]; then
    exit 1
fi

# Small enough — check whether added lines under code-extension files
# are anything more than blank / comment.
ADDED_REAL=$(git diff "origin/${TARGET}...HEAD" -- \
    '*.py' '*.go' '*.ts' '*.tsx' '*.js' '*.jsx' '*.rs' '*.java' '*.kt' '*.rb' \
    2>/dev/null \
    | grep -E '^\+' \
    | grep -vE '^\+\+\+' \
    | grep -vE '^\+\s*$' \
    | grep -vE '^\+\s*(#|//|--)' \
    || true)

if [ -z "$ADDED_REAL" ]; then
    # No non-trivial code lines added — skip.
    exit 0
fi

exit 1
