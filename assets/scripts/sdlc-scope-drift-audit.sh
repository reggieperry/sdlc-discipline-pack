#!/usr/bin/env bash
# sdlc-scope-drift-audit.sh — Detect scope drift in chain PR diffs.
#
# Invoked by the finalizer phase. Compares the PR's cumulative diff
# against the plan file's `**In:**` backticked path list. Files in the
# diff that don't match the In list are reported as scope drift.
#
# Catches Category B residue regardless of which chain phase introduced
# it (worker / tester / reviewer / documenter). The finalizer runs LAST
# so the cumulative diff includes every commit on the branch.
#
# Args:
#   --plan <path>   Path to the plan file (plans/<bead-id>.md)
#   --target <ref>  Target branch (e.g., main); diff is origin/$TARGET...HEAD
#
# Exits 0 if no drift detected. Exits 1 with the offending file list on
# stdout if drift detected. Exits 0 (no audit, fail-open) if the plan
# file has no parseable `**In:**` section — better to miss drift than
# to noise-flag every PR whose plan doesn't list paths in machine-
# readable form. Exits 0 if the diff is empty.
#
# Closes pack issue #83 Prong 2.

set -uo pipefail

PLAN=""
TARGET="main"
while [ $# -gt 0 ]; do
    case "$1" in
        --plan)
            PLAN="$2"
            shift 2
            ;;
        --target)
            TARGET="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

if [ -z "$PLAN" ] || [ ! -f "$PLAN" ]; then
    echo "scope-drift: no plan file at '$PLAN'; skipping audit" >&2
    exit 0
fi

# Extract the plan's `**In:**` section. Capture everything from the
# `**In:**` line until the next blank line or the next `**` bold
# heading. Then pull every backticked token.
IN_PATHS=$(awk '
    /^\*\*In:\*\*/ {
        capture = 1
        sub(/^\*\*In:\*\*[[:space:]]*/, "")
        print
        next
    }
    capture && /^\*\*/ { exit }
    capture && /^[[:space:]]*$/ { exit }
    capture { print }
' "$PLAN" | grep -oE '`[^`]+`' | tr -d '`' || true)

if [ -z "$IN_PATHS" ]; then
    echo "scope-drift: plan has no backticked paths under '**In:**'; skipping audit" >&2
    exit 0
fi

DIFF_FILES=$(git diff --name-only "origin/${TARGET}...HEAD" 2>/dev/null || true)

if [ -z "$DIFF_FILES" ]; then
    exit 0
fi

DRIFT=""
while IFS= read -r f; do
    [ -z "$f" ] && continue
    matched=0
    while IFS= read -r in_path; do
        [ -z "$in_path" ] && continue
        # Unquoted $in_path on the right of `case` triggers glob match;
        # direct paths fall through to literal equality.
        case "$f" in
            $in_path)
                matched=1
                break
                ;;
        esac
    done <<< "$IN_PATHS"
    if [ "$matched" = "0" ]; then
        DRIFT="${DRIFT}${f}"$'\n'
    fi
done <<< "$DIFF_FILES"

if [ -n "$DRIFT" ]; then
    printf "%s" "$DRIFT"
    exit 1
fi

exit 0
