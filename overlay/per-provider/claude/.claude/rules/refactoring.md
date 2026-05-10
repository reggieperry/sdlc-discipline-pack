---
paths:
  - "**/*.py"
---

> Full reasoning, citations (Fowler 2018, 2nd ed), and the project mapping: `docs/refactoring-guide.md`.

# Refactoring

A controlled technique for improving the design of existing code without changing its observable behavior. The discipline is in the chain of small steps, each preserving behavior, each verified by tests.

## The most important rule — Two Hats

You wear one hat at a time. Either you are **adding functionality** (feature hat) or you are **restructuring code** (refactoring hat). Never both at once.

Combining feature work and refactoring in one commit doubles the failure surface — when a test breaks, you can't tell which hat caused it. Keep them separate; switch hats explicitly between commits.

The hat boundary is at the *behavior* level, not the *artifact* level. A feature plus its pinning test is one hat (feature) and one commit. A feature plus an unrelated rename is two hats (feature + refactor) and two commits. Bundling test with feature in a single commit is fine and recommended; bundling feature with refactor is the violation.

PR shape:

- Pure refactoring PR: behavior tests pass without modification; structural moves named in description.
- Pure feature PR: new behavior, possibly new tests; existing function signatures preserved unless the issue's scope explicitly calls for change.
- A PR that includes both: split into two, or sequence as commits with `refactor:` and `feat:` prefixes that don't interleave.

Commit-level granularity:

- `feat(<area>): <behavior>` — new behavior plus its pinning test, atomic. The diff includes both production code and the test that pins it.
- `test(<area>): pin <behavior>` — a test for behavior already implemented (pin-after-implementation), or characterization tests pinning existing behavior before refactoring.
- `refactor(<area>): <move from catalog>` — pure restructuring. Always its own commit.
- `chore(<area>): <housekeeping>` — lints, type-noqa, tooling fixes. Always its own commit.

Splitting feat from test into separate commits buys nothing the diff doesn't already show. Splitting feat from refactor buys real clarity when a downstream test breaks. Optimize the granularity to the boundary that matters: hats, not artifacts.

## When to refactor

Four modes:

- **Rule of Three** — refactor on the third occurrence of duplication, not the first or second. Two cases may be coincidental; three is a real pattern.
- **Preparatory** — restructure before adding a feature, to make the feature easy to add. The most common refactoring trigger in disciplined work.
- **Comprehension** — restructure to understand code; persist the understanding in the structure (rename to clarify, extract to give a chunk a name).
- **Litter-pickup** — small fixes during normal work, bounded by Two Hats. Two minutes max; clearly improves clarity; doesn't grow into a refactoring sequence.

## Tests are a precondition

> "Before you start refactoring, make sure you have a solid suite of tests. These tests must be self-checking."

Without solid self-checking tests, refactoring is hope, not discipline.

If the code lacks tests, write **characterization tests** first — tests that capture current behavior (whatever it is, including bugs) as a baseline. Once they pass on the original code, refactoring is safe; if they later expose a bug, that's a separate fix under the feature hat.

## The mechanics

Each refactoring move follows compile-test-commit:

1. **Identify the target.** A specific code location, not "the whole module."
2. **Name the move.** From the catalog (Extract Function, Rename Variable, Move Field, …). The name commits you to a documented recipe.
3. **Apply the recipe.** Small steps. Don't shortcut the catalog's mechanics.
4. **Compile.** Make the type checker happy. For Elder, `uv run mypy <touched-files>` if the file is in the strict-mode set.
5. **Test.** Run the relevant test suite (`uv run pytest tests/<area> -v`). Tests must pass before proceeding.
6. **Commit.** A local commit naming the move. Example: `refactor: extract calculate_position_size from RiskAgent.evaluate`.

Never proceed to the next move while a test is failing. Never modify a test to make a refactoring "pass." If a test breaks, either revert the move or fix the test under the feature hat first.

For a sequence of moves on a branch, squash before merge if the per-move commits would clutter main; preserve them on the working branch as the safety net.

## Bad smells (recognize-and-fix table)

Code smells are diagnostic vocabulary. Each suggests one or more refactoring responses. Not every smell warrants immediate fixing; track and prioritize.

| Smell | Typical refactoring response |
| ----- | ---------------------------- |
| Mysterious Name | Rename Variable / Function / Field |
| Duplicated Code | Extract Function (call from both sites); Pull Up Method |
| Long Function | Extract Function; Replace Temp with Query; Decompose Conditional; Replace Loop with Pipeline |
| Long Parameter List | Preserve Whole Object; Introduce Parameter Object; Replace Parameter with Query |
| Global Data | Encapsulate Variable; refactor to dependency injection |
| Mutable Data | Encapsulate Variable; Split Variable; Replace Derived Variable with Query |
| Divergent Change | Split Phase; Extract Class; Move Function |
| Shotgun Surgery | Move Function; Move Field; Combine Functions into Class |
| Feature Envy | Move Function (to the module whose data it accesses) |
| Data Clumps | Extract Class; Introduce Parameter Object |
| Primitive Obsession | Replace Primitive with Object; Replace Type Code with Subclasses |
| Repeated Switches | Replace Conditional with Polymorphism |
| Loops mixing concerns | Replace Loop with Pipeline; Split Loop |
| Lazy Element | Inline Function; Inline Class |
| Speculative Generality | Collapse Hierarchy; Inline; Remove Dead Code |
| Temporary Field | Extract Class; Introduce Special Case |
| Message Chains | Hide Delegate; Extract Function |
| Middle Man | Remove Middle Man; Inline Function |
| Large Class | Extract Class; Extract Subclass; Extract Interface |
| Data Class (only fields, no behavior) | Move Function (push behavior in); Encapsulate Record |
| Refused Bequest | Replace Subclass with Delegate |
| Comments-as-deodorant | Extract Function (the comment becomes the function name); Rename Function; Introduce Assertion |

## Refactoring catalog (most-used moves)

Each move has explicit mechanics in Fowler's catalog. Use the move name verbatim in commits and PR descriptions; the name commits to the documented recipe.

**The basics:**

- **Extract Function** — turn a code fragment into a named function. The most-used refactoring.
- **Inline Function** — replace a trivial function call with the body.
- **Extract Variable** — assign an expression to a name.
- **Inline Variable** — replace a variable with its expression when the variable doesn't add clarity.
- **Change Function Declaration** — add, remove, rename, reorder parameters.
- **Encapsulate Variable** — wrap direct access behind a getter/setter.
- **Rename Variable / Function / Field** — change a name for clarity.
- **Introduce Parameter Object** — replace recurring parameter groups with a class.
- **Combine Functions into Class** — when several functions share data, package them.

**Encapsulation:**

- **Encapsulate Record** — replace a dict/struct with a class that controls field access.
- **Encapsulate Collection** — don't expose a collection; provide methods.
- **Replace Primitive with Object** — lift a domain primitive (string, int) into a class.
- **Replace Temp with Query** — replace a local temporary variable with a function call.
- **Extract Class** — pull related fields and methods into a smaller class.
- **Hide Delegate** — collapse `a.getB().doX()` chains into `a.doXViaB()`.

**Moving features:**

- **Move Function** — move a function to a different module/class.
- **Move Field** — move a field to a different class.
- **Move Statements into Function** — fold setup/teardown into the called function.
- **Slide Statements** — reorder statements so related code is together (foundation for Extract Function).
- **Split Loop** — when a loop does two things, split into two loops.
- **Replace Loop with Pipeline** — replace imperative loop with chained collection operations.

**Simplifying conditionals:**

- **Decompose Conditional** — extract conditional, then-branch, else-branch into named functions.
- **Consolidate Conditional Expression** — combine related checks into one predicate.
- **Replace Nested Conditional with Guard Clauses** — flatten nested ifs with early returns.
- **Replace Conditional with Polymorphism** — replace switch-on-type with subclasses or strategy.
- **Introduce Special Case** — replace pervasive null-checks with a Null Object.

**APIs:**

- **Separate Query from Modifier** — a function returns OR mutates, not both.
- **Parameterize Function** — collapse two near-identical functions into one with a parameter.
- **Remove Flag Argument** — split a function whose boolean flag switches behavior.
- **Preserve Whole Object** — pass an object instead of pulling fields out.
- **Replace Constructor with Factory Function** — wrap construction in a factory when it's complex or polymorphic.

For full mechanics on any move, consult `docs/refactoring-guide.md` or Fowler chapters 5–12 directly.

## The compile-test-commit rhythm

For multi-move refactoring sequences:

```text
# For each refactoring move:
identify-target → name-move → apply-recipe → compile → test → commit
```

Each commit message names its move:

- `refactor: extract calculate_position_size from RiskAgent.evaluate`
- `refactor: rename Trade.amt to Trade.notional_value`
- `refactor: move ImpulseColor enum from agents/scanner_agent.py to indicators/elder.py`

Commit before the next move. Always.

## Self-audit (binary; partial credit does not exist)

Run before declaring refactoring done.

1. Behavior is preserved. Tests that passed before pass after; no test was modified to make broken behavior pass.
2. The change is pure refactoring. No feature work mixed in.
3. The moves are named. PR description lists them; each commit message names its move.
4. Tests existed before refactoring started, or characterization tests were written first.
5. Tests are self-checking. Binary pass/fail; no operator inspection.
6. Each commit applies ~3 moves or fewer. Larger commits should have been split.
7. Tests pass at each commit, not just at the end.
8. The smell that triggered the work is named. "Refactoring this Long Function" or "These three sites are Duplicated Code." If you can't name the smell, you're rewriting.
9. The catalog name matches the actual move. Extract Function is an extraction, not a rewrite.
10. No speculative generality. Every abstraction has a current consumer.
11. The code is at least as understandable as before.
12. Comments tell the story or are removed.

A change failing any item is not finished.

## Antipatterns (refuse on sight)

- Refactoring while changing behavior. The most common Two-Hats violation.
- Skipping tests. Refactoring without solid self-checking tests is hope.
- Skipping the per-move commit. Stacked moves in one commit lose the safety net.
- Premature abstraction (Rule of Three violation).
- Speculative generality (extension points no one needs).
- Renaming under the feature hat (mid-feature renames conflate diffs).
- Mocking the SUT to make a refactor "feel safe."
- Stopping mid-sequence (half-done Extract Function leaves a worse state).
- Mixing two refactorings in one commit (even pure-refactoring ones).
- Making changes whose moves cannot be named from the catalog.
