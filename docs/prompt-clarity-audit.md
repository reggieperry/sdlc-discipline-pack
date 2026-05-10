# Prompt clarity audit — v2.1

Background: the v2.0 Phase 4 concurrent test stalled on two gh-stats stories at the finalizer step because the finalizer prompt assumed an `origin` remote existed. The agent correctly diagnosed the no-remote case and produced a four-option clarification chooser; `--dangerously-skip-permissions` does not bypass the model's own clarification questions, only tool-permission prompts. The chain stalled until manually nudged. v2.0.1 fixed the finalizer; this audit looks for the same pattern across the four other v2.1 pool prompts.

## Method

Read each pool prompt looking for branches where the agent might face deterministic but unspecified situations — the kind that produce a chooser when an unattended chain hits them. Categorize by severity:

- **High** — chain-stalling on a realistic edge case
- **Medium** — likely to produce a clarification question or sub-optimal behavior, but recoverable
- **Low** — cosmetic; metadata gaps, formatting drift, judgment calls

## Findings

### Worker (`agents/worker/prompt.template.md` + formula `mol-sdlc-work.toml`)

| ID | Section | Severity | Finding |
|---|---|---|---|
| W-1 | submit-and-exit step 3 | **High** | Bare `git push -u origin "$(git branch --show-current)"` fails on no-remote rigs. Phase 4 LLM handled it gracefully (set `worker.push_skipped: no_remote_configured`), but the prompt does not direct that handling. Future runs may stall. |
| W-2 | submit-and-exit step 3 (PR open) | Medium | `gh pr create` invocation fires only when `SDLC_OPEN_PR_DEFAULT=true`, but the env-var read is inside the worker's submit. v2.0 moved PR creation to the finalizer; the worker's branch path here may now be stale. |

### Tester (`agents/tester/prompt.template.md`)

| ID | Section | Severity | Finding |
|---|---|---|---|
| T-1 | Get to the worker's branch | **High** | `git fetch origin` and `git show-ref --verify ... origin/$BRANCH` both fail when no `origin` remote exists. The else-branch routes back to the worker, which would then loop (worker can't push, tester can't fetch). |
| T-2 | Bounce to worker | Medium | `test_failure_category` accepts only one of `pytest|ruff|mypy`. If two are red simultaneously, the LLM picks one — choice is non-deterministic. |
| T-3 | Capture summary | Low | `TEST_SUMMARY=$(tail -1 "$PYTEST_LOG")` can be empty if pytest produces unexpected output shape. Empty `test_summary` metadata is benign downstream. |

### Reviewer (`agents/reviewer/prompt.template.md`)

| ID | Section | Severity | Finding |
|---|---|---|---|
| R-1 | Get to the worker's branch | **High** | Same as T-1: `git fetch origin` and `git show-ref ... origin/$BRANCH` fail on no-remote rigs. The else-branch routes back to the worker pool with `review_verdict=fail`, which mis-attributes a routing failure as a code-quality failure. |

### Documenter (`agents/documenter/prompt.template.md`)

| ID | Section | Severity | Finding |
|---|---|---|---|
| D-1 | Get to the reviewer's branch | **High** | Same as T-1/R-1: `git fetch origin` and `git show-ref ... origin/$BRANCH` fail on no-remote rigs. The else-branch escalates the bead, blocking the chain. |
| D-2 | When you're done | **High** | `git push origin HEAD` fails on no-remote rigs. Phase 4 LLM handled it gracefully (set `documenter.push_skipped: no_remote_configured`), but the prompt does not direct that handling. |
| D-3 | Trivial-change short-circuit | Medium | The block sets metadata and a comment says "Skip writing files; jump to finalizer handoff below," but the bash control flow does not actually `goto` — the LLM has to interpret the comment's intent. A literal interpretation may run the doc-writing block anyway. |
| D-4 | SLUG generation | Low | If the bead title is all punctuation, the slug becomes empty and the file path becomes `feature-<id>-.md`. Cosmetic only. |

### Finalizer (`agents/finalizer/prompt.template.md`)

| ID | Section | Severity | Finding |
|---|---|---|---|
| F-1 | Get to the documenter's branch | — | **Already fixed in v2.0.1.** No-remote graceful close added. |
| F-2 | Refresh against origin/main | Medium | If origin exists but `git rebase origin/$TARGET` produces conflicts, the prompt escalates correctly. No ambiguity surfaced in Phase 4. |
| F-3 | Open the PR | Medium | If `gh pr create` fails (rate limit, auth expired), the prompt does not direct fallback behavior. The LLM may surface a chooser. |
| F-4 | Auto-merge gate | Low | When `SDLC_GLANCE_MERGE_DEFAULT=true` but the rubric script is missing, the LLM may stall. Prompt assumes script exists. |

## Disposition

Five **High**-severity findings (W-1, T-1, R-1, D-1, D-2) all derive from the same root cause: prompts assume `origin` exists and don't handle the no-remote case deterministically. The worker and documenter Phase 4 stories happened to land on an LLM that produced graceful behavior, but that's a fragile guarantee.

**Fix in v2.1.1**: add a short no-remote check at the top of every section that touches `origin`, mirroring the v2.0.1 finalizer pattern. The check sets the appropriate `<phase>.push_skipped` or routing-skip metadata and either continues (for read-only operations) or skips the push (for write operations).

**Defer** (medium and low):
- T-2 (multi-category failure): low practical impact; reviewer catches material issues. Track as v2.2.
- D-3 (trivial-change short-circuit control flow): refactor the section to use early-return semantics rather than relying on a comment. v2.2.
- F-3 / F-4 (gh pr create / rubric script failure): error-handling work, not ambiguity-resolution. v2.2.
- D-4, T-3 (cosmetic metadata gaps): no fix needed.

## Generalizable lesson

`--dangerously-skip-permissions` is a tool-permission flag, not an autonomy flag. Every prompt section that depends on an environmental precondition (origin remote present, network reachable, lockfile valid, plan file exists) needs an explicit deterministic branch for the missing-precondition case. The pattern:

```bash
if ! <precondition-check>; then
    bd update $STORY_ID --set-metadata <phase>.<skip-reason>="<short-reason>"
    # If skipping is safe: continue with degraded behavior
    # If skipping is unsafe: route appropriately and exit
fi
```

This shifts the agent from "model judgment" to "follow the script" for the deterministic edge cases, and reserves model judgment for the genuinely ambiguous cases that warrant escalation.
