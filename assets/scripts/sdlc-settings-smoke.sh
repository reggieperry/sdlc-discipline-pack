#!/usr/bin/env bash
# sdlc-settings-smoke.sh — release gate for overlay settings.json deny/allow patterns.
#
# Asserts two invariants over the pack's overlay settings.json:
#
#   1. Every command in the MUST_ALLOW catalog (commands the chain phases
#      actually emit during normal operation) is NOT caught by any deny
#      pattern. A miss here means the chain stalls.
#
#   2. Every command in the MUST_DENY catalog (destructive commands the
#      chain must never run) IS caught by at least one deny pattern.
#      A miss here means the safety net has a hole.
#
# Pattern semantics: Claude Code's Bash(<pattern>) matches the command string
# by glob, where `*` matches any sequence of characters. We model the same
# semantics with a glob→regex conversion (escape regex metacharacters,
# replace `*` with `.*`, anchor with ^...$).
#
# Exit status: 0 if all checks pass, 1 if any fail.
#
# Invoked from:
#   - Pack CI / pre-release gate (run before tagging a release).
#   - Operator on demand: `bash assets/scripts/sdlc-settings-smoke.sh`.
#
# History: v2.6.2 fixed an awaySummaryEnabled stall; v2.7.5 removed an
# overly-broad deny pattern (`Bash(git push --force *)` was catching
# `--force-with-lease`). Both bugs were caught in production after hours of
# wall-clock loss. The catalogs below encode the lesson: explicit
# enumeration is cheaper than another regression.

set -uo pipefail

SETTINGS="${1:-}"
if [ -z "$SETTINGS" ]; then
    # Default location relative to the pack root.
    SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
    PACK_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
    SETTINGS="$PACK_ROOT/overlay/per-provider/claude/.claude/settings.json"
fi

if [ ! -f "$SETTINGS" ]; then
    echo "smoke: settings.json not found at $SETTINGS" >&2
    exit 1
fi

# Commands the chain phases emit. Adding to this list is the right response
# whenever a chain stall is traced to a missing allow pattern.
MUST_ALLOW=(
  # worker
  "git push origin feature/x"
  "git push --force-with-lease origin feature/x"
  "git fetch origin"
  "git rebase origin/main"
  "git rebase --continue"
  "git rev-parse HEAD"
  "git merge-base HEAD origin/main"
  "uv run pytest tests/ -v --no-cov"
  "uv run ruff check ."
  "uv run mypy ."
  "uv run lint-imports"
  "python3 /path/to/pack/assets/scripts/sdlc-architectural-signals.py base head --rig-config x.toml"
  "bash /path/to/pack/assets/scripts/sdlc-glance-rubric.sh STORY-1"
  # all phases
  "bd update story-1 --set-metadata key=value"
  "bd update story-1 --status=open --assignee \"\""
  "bd list --status=closed --json"
  "bd show story-1 --json"
  "bd close story-1 --reason done"
  "gc runtime drain-ack"
  "gc mail send witness -s subject -m message"
  "jq -r .metadata.foo"
  # finalizer
  "gh pr create --title x --body y"
  "gh pr merge 42 --squash --delete-branch"
  "gh pr comment 42 --body-file out.txt"
  "gh pr view 42 --json state,mergeable"
)

# Commands that must never run. Adding to this list is the right response
# whenever a destructive command-shape is identified that's not yet caught.
MUST_DENY=(
  "git push -f origin main"
  "git push --force origin main"
  "git push --force HEAD"
  "git push --force HEAD:main"
  "rm -rf /"
  "rm -rf /home/user"
  "git reset --hard origin/main"
  "git reset --hard main"
)

# Convert a Claude Code Bash pattern (without the Bash(...) wrapper) to a
# bash regex. Models Claude Code's actual matching semantics, which are
# more aggressive than strict glob:
#
#   - `*` matches any sequence of characters (including none)
#   - A space immediately before `*` is consumed by the wildcard — so
#     `git push --force *` matches `git push --force-with-lease ...`
#     even though there's no literal space between `--force` and the
#     next char. This is what bit v2.7.5 (PR #14).
#
# Implementation: collapse ` *` (space-asterisk) to `.*` first so the
# space requirement is dropped, then escape regex metas and convert any
# remaining `*` to `.*`.
pattern_to_regex() {
    local pat="$1"
    # Step 1: escape regex metas in the user-supplied pattern. Must come BEFORE
    # wildcard substitution; otherwise the `.` we introduce in step 2 gets
    # treated as a literal and re-escaped.
    pat=$(printf '%s' "$pat" | sed -e 's/[][\.|^$+?(){}]/\\&/g')
    # Step 2: collapse ` *` to `.*`.
    pat=$(printf '%s' "$pat" | sed -e 's/ \*/.*/g')
    # Step 3: convert any remaining standalone `*` to `.*`.
    pat=$(printf '%s' "$pat" | sed -e 's/\*/.*/g')
    printf '^%s$' "$pat"
}

# Extract Bash(...) pattern bodies from a JSON array path.
extract_bash_patterns() {
    local path="$1"
    jq -r ".permissions.${path}[]? // empty" "$SETTINGS" \
        | sed -nE 's/^Bash\((.*)\)$/\1/p'
}

ALLOWS=$(extract_bash_patterns allow)
DENIES=$(extract_bash_patterns deny)

if [ -z "$DENIES" ]; then
    echo "smoke: WARNING — no Bash() deny patterns in $SETTINGS" >&2
fi

FAIL=0
PASS=0

# Check 1: every must-allow command must NOT be caught by any deny pattern.
while IFS= read -r cmd; do
    [ -z "$cmd" ] && continue
    while IFS= read -r deny_pat; do
        [ -z "$deny_pat" ] && continue
        regex=$(pattern_to_regex "$deny_pat")
        if [[ "$cmd" =~ $regex ]]; then
            echo "✗ FAIL must-allow caught by deny: '$cmd' ~ Bash($deny_pat)"
            FAIL=$((FAIL + 1))
            continue 2
        fi
    done <<< "$DENIES"
    PASS=$((PASS + 1))
done < <(printf '%s\n' "${MUST_ALLOW[@]}")

# Check 2: every must-deny command must match at least one deny pattern.
while IFS= read -r cmd; do
    [ -z "$cmd" ] && continue
    matched=0
    while IFS= read -r deny_pat; do
        [ -z "$deny_pat" ] && continue
        regex=$(pattern_to_regex "$deny_pat")
        if [[ "$cmd" =~ $regex ]]; then
            matched=1
            break
        fi
    done <<< "$DENIES"
    if [ "$matched" -eq 1 ]; then
        PASS=$((PASS + 1))
    else
        echo "✗ FAIL must-deny not caught: '$cmd'"
        FAIL=$((FAIL + 1))
    fi
done < <(printf '%s\n' "${MUST_DENY[@]}")

echo ""
TOTAL=$((PASS + FAIL))
if [ "$FAIL" -eq 0 ]; then
    echo "✓ PASS — settings.json smoke test: $PASS/$TOTAL checks"
    exit 0
else
    echo "✗ FAIL — settings.json smoke test: $FAIL/$TOTAL failed"
    exit 1
fi
