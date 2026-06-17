---
paths:
  - "**/*.py"
  - "**/*.go"
  - "**/*.sh"
  - "**/tests/**"
---

# Refactoring existing code

A controlled technique for improving the design of working code without changing what it does. The discipline is in the chain of small steps — each one preserves observable behavior and is verified by tests before the next begins. Source: Martin Fowler, *Refactoring: Improving the Design of Existing Code* (2nd ed, 2018).

> Full mechanics, citations, and worked examples: `.claude/sdlc-discipline/guides/refactoring-guide.md` (or Fowler chapters 5–12 directly). See `craft-complexity.md` for what you refactor *toward* (deep modules, less leakage), `craft-abstraction.md` for substitutability of the pieces you move, `craft-tdd.md` for the test suite that makes refactoring safe, `craft-domain-modeling.md` for the modeling several catalog moves push toward, and the active language overlay (`go-*.md`, the `python-*` set) for the idioms several catalog entries map to.

## Discipline

- **Refactor only to make code easier to understand and cheaper to change — never to alter observable behavior.** "Observable" is externally visible behavior; call stacks, performance characteristics, and internal interfaces may move freely.
- **Take the smallest steps that compose, and run the tests after every one.** The code compiles and passes after each step; it never sits broken. If someone's code was "broken for days while refactoring," they weren't refactoring.
- **Commit after each green step so you can revert to the last good state.** When a test goes red and the cause isn't immediately obvious, revert and redo in smaller pieces rather than debugging forward.
- **Rename the moment a better name appears — naming is a first-class refactoring, not cosmetic.** Leave the code healthier than you found it; aim for better, not perfect.
- **Justify it economically, not morally** — refactoring earns its place by making it faster to add features and fix bugs (the design-stamina hypothesis), never "clean code" for its own sake. Apply YAGNI: add a parameter or abstraction only when a real second case exists.
- **Don't refactor a stable API you needn't touch, and prefer rewriting code that's beyond repair** over restructuring it move-by-move.

## The most important rule — Two Hats

You wear one hat at a time: either you are **adding functionality** (feature hat) or you are **restructuring code** (refactoring hat). Never both in the same step.

Combining feature work and refactoring in one commit doubles the failure surface — when a test breaks, you can't tell which hat caused it. Keep them separate; switch hats explicitly between commits. Adding function writes new code and new tests; refactoring restructures and changes no tests except to track a moved interface.

The hat boundary is at the *behavior* level, not the *artifact* level. A feature plus its pinning test is one hat (feature) and one commit. A feature plus an unrelated rename is two hats (feature + refactor) and two commits. Bundling a test with its feature in a single commit is fine and recommended; bundling a feature with a refactor is the violation.

PR shape:

- **Pure refactoring PR:** behavior tests pass without modification; structural moves named in the description.
- **Pure feature PR:** new behavior, possibly new tests; existing function signatures preserved unless the issue's scope explicitly calls for change.
- **A PR that includes both:** split into two, or sequence as commits with `refactor:` and `feat:` prefixes that don't interleave.

Commit-level granularity:

- `feat(<area>): <behavior>` — new behavior plus its pinning test, atomic. The diff includes both production code and the test that pins it.
- `test(<area>): pin <behavior>` — a test for behavior already implemented (pin-after-implementation), or characterization tests pinning existing behavior before refactoring.
- `refactor(<area>): <move from catalog>` — pure restructuring. Always its own commit.
- `chore(<area>): <housekeeping>` — lints, suppression cleanup, tooling fixes. Always its own commit.

Splitting feat from test into separate commits buys nothing the diff doesn't already show. Splitting feat from refactor buys real clarity when a downstream test breaks. Optimize the granularity to the boundary that matters: hats, not artifacts.

## When to refactor

- **Rule of Three:** do it once, tolerate the second duplicate, refactor on the third. Two cases may be coincidental; three is a real pattern.
- **Preparatory:** before adding a feature, "make the change easy (this may be hard), then make the easy change." The most common refactoring trigger in disciplined work.
- **Comprehension:** when you have to think to understand code, move that understanding out of your head into the structure (rename to clarify, extract to give a chunk a name) before proceeding.
- **Litter-pickup and opportunistic:** fix small messes you pass through — bounded by Two Hats. Two minutes max; clearly improves clarity; doesn't grow into a refactoring sequence. Most refactoring is interwoven with feature work, not a scheduled phase.

## Tests are a precondition

> "Before you start refactoring, make sure you have a solid suite of tests. These tests must be self-checking."

Without solid, self-checking, fast tests, refactoring is hope, not discipline — the test suite is the bug detector that makes small steps safe. Never refactor on a red bar.

If the code lacks tests, write **characterization tests** first — tests that capture current behavior (whatever it is, including bugs) as a baseline. Find seams in untested legacy and pin behavior there before touching structure. Once the characterization tests pass on the original code, refactoring is safe; if they later expose a bug, that's a separate fix under the feature hat. See `craft-tdd.md` and the language overlay's testing rules (`go-testing.md`, `python-testing.md`).

## The mechanics — compile-test-commit

Each refactoring move follows the same rhythm:

1. **Identify the target.** A specific code location, not "the whole module."
2. **Name the move.** From the catalog (Extract Function, Rename Variable, Move Field, …). The name commits you to a documented recipe.
3. **Apply the recipe.** Small steps. Don't shortcut the catalog's mechanics.
4. **Compile / type-check.** Make the type checker happy on the touched files (`go build` / the active type checker for the language overlay).
5. **Test.** Run the relevant test suite. Tests must pass before proceeding.
6. **Commit.** A local commit naming the move.

```text
# For each refactoring move:
identify-target → name-move → apply-recipe → compile → test → commit
```

Never proceed to the next move while a test is failing. Never modify a test to make a refactoring "pass." If a test breaks and the cause isn't obvious, revert the move and redo it smaller; if it's a real defect, fix it under the feature hat first. Each commit message names its move, for example:

- `refactor: extract calculate_order_size from AccountService.evaluate`
- `refactor: rename Order.amt to Order.notional_value`
- `refactor: move Status enum from service/handler to domain/order`

Commit before the next move. Always. For a sequence of moves on a branch, keep ~3 moves or fewer per commit and preserve the per-move commits on the working branch as the safety net; squash before merge if they would clutter the main line.

## Smell → refactoring

Code smells are diagnostic vocabulary. Each suggests one or more refactoring responses. Not every smell warrants immediate fixing — track and prioritize, but be able to name the smell that triggered any refactoring you do.

| Smell | Tell | Refactoring response |
|---|---|---|
| Mysterious Name | you puzzle out a name | Rename Variable / Function / Field (Change Function Declaration) |
| Duplicated Code | same structure in 2+ places | Extract Function (call from both sites); Pull Up Method |
| Long Function | you want to comment a block | Extract Function; Replace Temp with Query; Decompose Conditional; Split Loop; Replace Loop with Pipeline |
| Long Parameter List | many or derivable params | Preserve Whole Object; Introduce Parameter Object; Replace Parameter with Query; Remove Flag Argument |
| Global Data | state writable from anywhere; spooky action | Encapsulate Variable; refactor to dependency injection |
| Mutable Data | value changes unexpectedly | Encapsulate Variable; Split Variable; Replace Derived Variable with Query; Separate Query from Modifier |
| Divergent Change | one module changes for many reasons | Split Phase; Extract Class/Function; Move Function |
| Shotgun Surgery | one change, many little edits | Move Function; Move Field; Combine Functions into Class |
| Feature Envy | a function talks to another module's data | Move Function (to the module whose data it accesses); Extract then move |
| Data Clumps | same items travel together | Extract Class; Introduce Parameter Object |
| Primitive Obsession | domain modeled as strings/ints | Replace Primitive with Object; Replace Type Code with Subclasses |
| Repeated Switches | same type-switch in many places | Replace Conditional with Polymorphism |
| Loops mixing concerns | loop obscures select/transform | Replace Loop with Pipeline; Split Loop |
| Lazy Element | element that earns nothing | Inline Function; Inline Class |
| Speculative Generality | hooks only the tests use | Collapse Hierarchy; Inline; Remove Dead Code |
| Temporary Field | field set only sometimes | Extract Class; Introduce Special Case |
| Message Chains | `a.b().c().d()` | Hide Delegate; Extract then move |
| Middle Man | class that only delegates | Remove Middle Man; Inline Function |
| Large Class | too many fields/methods | Extract Class; Extract Subclass; Extract Interface |
| Data Class (fields, no behavior) | record with no methods | Move Function (push behavior in); Encapsulate Record |
| Refused Bequest | subclass ignores inherited API | Replace Subclass with Delegate |
| Comments (as deodorant) | comment hides bad code | Extract Function (comment becomes the name); Rename; Introduce Assertion — keep *why* comments |

## Refactoring catalog (most-used moves)

Each move has explicit mechanics in Fowler's catalog. Use the move name verbatim in commits and PR descriptions — the name commits you to the documented recipe. Change Function Declaration for many callers uses migration mechanics: extract, inline, rename incrementally rather than a big-bang signature change.

**The basics:**

- **Extract Function** — turn a code fragment into a named function. The most-used refactoring.
- **Inline Function** — replace a trivial function call with its body.
- **Extract Variable** — assign an expression to a name.
- **Inline Variable** — replace a variable with its expression when the variable doesn't add clarity.
- **Change Function Declaration** — add, remove, rename, or reorder parameters.
- **Encapsulate Variable** — wrap direct access behind a getter/setter.
- **Rename Variable / Function / Field** — change a name for clarity.
- **Introduce Parameter Object** — replace recurring parameter groups with a type.
- **Combine Functions into Class** — when several functions share data, package them.

**Encapsulation:**

- **Encapsulate Record** — replace a raw map/struct with a type that controls field access.
- **Encapsulate Collection** — don't expose a collection; provide methods.
- **Replace Primitive with Object** — lift a domain primitive (string, int) into a type.
- **Replace Temp with Query** — replace a local temporary variable with a function call.
- **Extract Class** — pull related fields and methods into a smaller type.
- **Hide Delegate** — collapse `a.getB().doX()` chains into `a.doXViaB()`.

**Moving features:**

- **Move Function** — move a function to a different module/type.
- **Move Field** — move a field to a different type.
- **Move Statements into Function** — fold setup/teardown into the called function.
- **Slide Statements** — reorder statements so related code is together (foundation for Extract Function).
- **Split Loop** — when a loop does two things, split into two loops.
- **Replace Loop with Pipeline** — replace an imperative loop with chained collection operations.

**Simplifying conditionals:**

- **Decompose Conditional** — extract the condition, then-branch, and else-branch into named functions.
- **Consolidate Conditional Expression** — combine related checks into one predicate.
- **Replace Nested Conditional with Guard Clauses** — flatten nested ifs with early returns.
- **Replace Conditional with Polymorphism** — replace switch-on-type with subclasses or a strategy.
- **Introduce Special Case** — replace pervasive null-checks with a Null Object.

**APIs:**

- **Separate Query from Modifier** — a function returns OR mutates, not both.
- **Parameterize Function** — collapse two near-identical functions into one with a parameter.
- **Remove Flag Argument** — split a function whose boolean flag switches behavior.
- **Preserve Whole Object** — pass an object instead of pulling fields out.
- **Replace Magic Literal** — replace a bare literal with a named constant.
- **Replace Constructor with Factory Function** — wrap construction in a factory when it's complex or polymorphic.

## Translating to the target language

Several catalog entries assume class-based OO; the per-language overlay carries the translation. Where a language has no implementation inheritance, *Replace Conditional with Polymorphism* becomes small interfaces plus one concrete type per case; where error returns are idiomatic, *Replace Error Code with Exception* inverts (return errors, don't reach for panic or exceptions); guard clauses may be the default control-flow style rather than an occasional cleanup; and immutable result records may need no getters/setters. Apply the smell→refactoring catalog as written, and let the active language rules (`go-style.md`, `go-errors.md`, `go-types.md`; the `python-style.md`, `python-types.md`, `python-errors.md` set) specialize the mechanics.

## Self-audit (binary; partial credit does not exist)

Run before declaring refactoring done.

1. Behavior is preserved. Tests that passed before pass after; no test was modified to make broken behavior pass.
2. The change is pure refactoring. No feature work mixed in.
3. The moves are named. The PR description lists them; each commit message names its move.
4. Tests existed before refactoring started, or characterization tests were written first.
5. Tests are self-checking. Binary pass/fail; no operator inspection.
6. Each commit applies ~3 moves or fewer. Larger commits should have been split.
7. Tests pass at each commit, not just at the end.
8. The smell that triggered the work is named. "Refactoring this Long Function" or "These three sites are Duplicated Code." If you can't name the smell, you're rewriting.
9. The catalog name matches the actual move. Extract Function is an extraction, not a rewrite.
10. No speculative generality. Every abstraction has a current consumer.
11. The code is at least as understandable as before.
12. Comments tell the *why* or are removed.

A change failing any item is not finished.

## Antipatterns (refuse on sight)

- Refactoring while changing behavior. The most common Two-Hats violation.
- Skipping tests. Refactoring without solid self-checking tests is hope.
- Skipping the per-move commit. Stacked moves in one commit lose the safety net.
- Premature abstraction (Rule of Three violation).
- Speculative generality (extension points no one needs).
- Renaming under the feature hat (mid-feature renames conflate diffs).
- Mocking the system under test to make a refactor "feel safe."
- Stopping mid-sequence (a half-done Extract Function leaves a worse state than starting).
- Mixing two refactorings in one commit (even pure-refactoring ones).
- Making changes whose moves cannot be named from the catalog.
