#!/usr/bin/env bash
# sdlc-glance-rubric.sh <story_id>
#
# Run the SDLC glance rubric against a story bead's PR. Prints a rubric report
# (one line per check) and exits 0 if all checks pass, 1 if any fail.
#
# Designed to be called by the documenter agent before deciding whether to
# auto-merge. Output is also suitable for posting as a PR comment.
#
# Checks (each binary; no partial credit):
#   R1  metadata.test_status == "green"
#   R2  metadata.review_verdict == "pass"
#   R3  All CI checks on the PR are green (auto-pass when the rig has no CI)
#   R4  Diff size <= 200 LOC across <= 10 files
#   R5  No sensitive-file edits beyond the plan's declared Sensitive files block
#   R6  All acceptance criteria addressed per reviews/<story_id>.md
#   R7  PR is mergeable: CLEAN
#
# Tunable via env vars:
#   SDLC_GLANCE_LOC_MAX     default 200
#   SDLC_GLANCE_FILES_MAX   default 10

set -uo pipefail

STORY_ID="${1:-}"
[ -z "$STORY_ID" ] && { echo "usage: sdlc-glance-rubric.sh <story_id>" >&2; exit 1; }

LOC_MAX="${SDLC_GLANCE_LOC_MAX:-200}"
FILES_MAX="${SDLC_GLANCE_FILES_MAX:-10}"

PASS=0
FAIL=0
report=()

bead=$(bd show "$STORY_ID" --json 2>/dev/null) || { echo "no such bead: $STORY_ID" >&2; exit 1; }

PR_URL=$(jq -r '.[0].metadata.pr_url // empty' <<< "$bead")
[ -z "$PR_URL" ] && { echo "no metadata.pr_url on $STORY_ID — open the PR before running rubric" >&2; exit 1; }

# Extract owner/repo/number from PR URL.
PR_NUM=$(echo "$PR_URL" | grep -oE 'pull/[0-9]+' | sed 's|pull/||')
REPO=$(echo "$PR_URL" | sed -E 's|https?://github.com/([^/]+/[^/]+)/.*|\1|')

check() {
  local id="$1" desc="$2" passed="$3" detail="${4:-}"
  if [ "$passed" = "true" ]; then
    report+=("✓ $id  $desc${detail:+  [$detail]}")
    PASS=$((PASS + 1))
  else
    report+=("✗ $id  $desc${detail:+  [$detail]}")
    FAIL=$((FAIL + 1))
  fi
}

# R1 — test_status green
test_status=$(jq -r '.[0].metadata.test_status // empty' <<< "$bead")
[ "$test_status" = "green" ] && check R1 "tests green" true || check R1 "tests green" false "got: ${test_status:-missing}"

# R2 — review_verdict pass
review_verdict=$(jq -r '.[0].metadata.review_verdict // empty' <<< "$bead")
[ "$review_verdict" = "pass" ] && check R2 "review verdict pass" true || check R2 "review verdict pass" false "got: ${review_verdict:-missing}"

# R3 — CI checks
ci_summary=$(gh pr checks "$PR_NUM" --repo "$REPO" 2>/dev/null || echo "")
if [ -z "$ci_summary" ]; then
  check R3 "CI green" true "no CI configured (auto-pass)"
elif echo "$ci_summary" | grep -qE 'fail|FAIL|cancelled|error'; then
  check R3 "CI green" false "see: gh pr checks $PR_NUM --repo $REPO"
else
  check R3 "CI green" true
fi

# R4 — diff size
stats=$(gh pr view "$PR_NUM" --repo "$REPO" --json additions,deletions,changedFiles 2>/dev/null) || stats='{}'
add=$(jq -r '.additions // 0' <<< "$stats")
del=$(jq -r '.deletions // 0' <<< "$stats")
files=$(jq -r '.changedFiles // 0' <<< "$stats")
loc=$((add + del))
if [ "$loc" -le "$LOC_MAX" ] && [ "$files" -le "$FILES_MAX" ]; then
  check R4 "size <= ${LOC_MAX} LOC, <= ${FILES_MAX} files" true "$loc LOC, $files files"
else
  check R4 "size <= ${LOC_MAX} LOC, <= ${FILES_MAX} files" false "$loc LOC, $files files"
fi

# R5 — sensitive files
plan_file=$(jq -r '.[0].metadata.plan_file // empty' <<< "$bead")
declared_sensitive=""
if [ -n "$plan_file" ] && [ -f "$plan_file" ]; then
  declared_sensitive=$(awk '/^## Sensitive files/{flag=1; next} /^## /{flag=0} flag' "$plan_file" | tr -d '\n' | grep -v '^None\.' || true)
fi
sensitive_list=".claude/rules/project/sensitive-files.md"
if [ -f "$sensitive_list" ]; then
  changed=$(gh pr view "$PR_NUM" --repo "$REPO" --json files --jq '.files[].path' 2>/dev/null || true)
  violation=""
  while IFS= read -r path; do
    [ -z "$path" ] && continue
    if grep -qF "$path" "$sensitive_list" 2>/dev/null; then
      if [ -z "$declared_sensitive" ] || ! grep -qF "$path" <<< "$declared_sensitive"; then
        violation="$path"
        break
      fi
    fi
  done <<< "$changed"
  [ -z "$violation" ] && check R5 "no undeclared sensitive-file edits" true || check R5 "no undeclared sensitive-file edits" false "$violation"
else
  check R5 "no undeclared sensitive-file edits" true "no sensitive-files.md in rig (auto-pass)"
fi

# R6 — acceptance criteria addressed
review_file=$(jq -r '.[0].metadata.review_file // empty' <<< "$bead")
if [ -n "$review_file" ] && [ -f "$review_file" ]; then
  unaddressed=$(grep -E '\[(partial|unaddressed)\]' "$review_file" || true)
  [ -z "$unaddressed" ] && check R6 "all acceptance criteria addressed" true || check R6 "all acceptance criteria addressed" false "see: $review_file"
else
  check R6 "all acceptance criteria addressed" false "review file missing"
fi

# R7 — mergeable: CLEAN
mergeable=$(gh pr view "$PR_NUM" --repo "$REPO" --json mergeable --jq '.mergeable' 2>/dev/null || echo "UNKNOWN")
[ "$mergeable" = "MERGEABLE" ] && check R7 "PR mergeable: CLEAN" true || check R7 "PR mergeable: CLEAN" false "got: $mergeable"

# Print the report.
echo "## SDLC glance rubric — story $STORY_ID"
echo "PR: $PR_URL"
echo ""
for line in "${report[@]}"; do echo "$line"; done
echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "✓ PASS — $PASS/$((PASS+FAIL)) checks"
  exit 0
else
  echo "✗ FAIL — $FAIL/$((PASS+FAIL)) failed"
  exit 1
fi
