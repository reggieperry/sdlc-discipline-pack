---
paths:
  - "tests/**"
  - "**/test_*.py"
  - "**/*_test.py"
---

> Full reasoning, taxonomy, and worked examples: `.claude/sdlc-discipline/guides/xunit-test-patterns-guide.md`.
> See `tdd.md` for the TDD discipline these patterns live inside; `testing.md` for the structural rules; `goos-guide.md` for the Freeman/Pryce design lens.
> The spine of this rule is Gerard Meszaros, *xUnit Test Patterns: Refactoring Test Code* (Addison-Wesley, 2007). It supplies the vocabulary the other rules use implicitly.

# xUnit test patterns

## Why this rule exists

The other testing rules (`tdd.md`, `testing.md`) say what to do; Meszaros's vocabulary lets reviewers and authors name *why* something is wrong precisely. "Fragile Test from Context Sensitivity," "Assertion Roulette from missing assertion messages," "Eager Test verifying two concerns" — each maps to a named fix in the catalog. Without the vocabulary, the same conversations recur as ad-hoc "this feels off" exchanges.

## Test Double taxonomy — pick the right one

Five distinct kinds. Don't say "mock" generically; name the kind:

| Type | Job | Use when |
| ---- | --- | -------- |
| **Dummy Object** | Placeholder argument; never used. | The SUT's signature requires it; the test doesn't exercise it. Often `None` or `object()`. |
| **Test Stub** | Controls *indirect inputs*. Returns canned responses. Sub-types: **Responder** (valid/invalid values), **Saboteur** (raises). | The SUT calls into a peer whose return value must vary across cases. |
| **Test Spy** | Stub that *records* calls; test asserts on calls after exercise. | The SUT's outgoing calls matter, and you can assert after the exercise phase. |
| **Mock Object** | Loaded with *expectations* up front; fails immediately on wrong call. | The exact call sequence is part of the invariant (e.g., security, transaction ordering). Heavier; couples test to sequence. |
| **Fake Object** | Working alternative implementation, lighter than the real DOC (in-memory db, in-process queue). | The real DOC is slow/unavailable, and the test cares about the DOC's semantics, not just its calls. |

A **Hard-Coded Test Double** bakes responses into the class; a **Configurable Test Double** accepts setup that feeds values. Default to configurable.

The "mock peers not internals" rule in `tdd.md` applies to *all* Test Doubles. The vocabulary refines the rule but does not change it.

## Indirect inputs vs indirect outputs

- **Direct input** — argument passed to the SUT by the test. Controlled by the test trivially.
- **Indirect input** — value received from a peer the SUT calls into. Controlled via a Test Stub or Fake.
- **Direct output** — return value from the SUT to the test. Verified with assertions on the return.
- **Indirect output** — method call the SUT makes to a peer. Verified via Test Spy (post-exercise assertion) or Mock Object (pre-exercise expectation).

State Verification asserts on post-exercise state; Behavior Verification asserts on the calls made during exercise. **Prefer State Verification.** Reach for Behavior Verification only when State Verification cannot express the invariant.

## The four-phase test (Arrange/Act/Assert is this same anatomy)

```
setup    # build fixture + Test Doubles, install Doubles into SUT
exercise # one call to the SUT
verify   # assert direct outputs, post-test state, indirect outputs
teardown # implicit via yield fixtures; in-line only if implicit teardown isn't possible
```

The four phases must be visible at a glance. Alternating exercise/verify calls is an **Eager Test** smell.

## Named test smells — cite by name in reviews

| Smell | Symptom | Sub-causes | Fix |
| ----- | ------- | ---------- | --- |
| **Obscure Test** | Can't grasp the behavior at a glance. | Mystery Guest, Eager Test, Irrelevant Information. | Creation Methods + Custom Assertions + Test Utility Methods; reduce body to "given/when/then" in one screen. |
| **Conditional Test Logic** | `if`/`for`/`try` in the test body. | Logic the test takes makes the test itself testable. | Parameterize via pytest; split into separate methods; loops → Parameterized Test. |
| **Fragile Test** | Passes today, fails on unrelated change tomorrow. | Interface, Behavior, Data, or Context Sensitivity. | Fresh Fixture per test; replace Shared Fixture with Fake Object; inject ambient deps. |
| **Erratic Test** | Sometimes-passes, sometimes-fails on same code. | Interacting Tests, Test Run Wars, Unrepeatable Tests. | Per-test isolation; deterministic clock via DI; seeded RNG. |
| **Assertion Roulette** | Many assertions, no messages, failure log doesn't name which. | Missing Assertion Messages. | Assertion Messages everywhere, OR split into Single-Condition Tests, OR Custom Assertion that reports first mismatch. |
| **Slow Tests** | > 30s — developer stops running per change. | Real DB / network / heavy fixture. | Fake Object for slow DOCs; Minimal Fixture; in-memory store. |
| **Frequent Debugging** | Need debugger to diagnose failures. | Eager Test or coverage gap. | Smaller exercise phase; one concern per test. |

Other smells worth knowing but lower frequency in this rig: **Mystery Guest** (fixture data appears from nowhere — name the source), **Eager Test** (multiple concerns in one method — split), **Manual Intervention** (test requires human action — fully automate or delete), **Test Code Duplication** (extract Test Utility Methods).

## Fixture strategies

Default chain of choices, in order of preference:

1. **Fresh Fixture, Transient** — built per test, garbage-collected after. v1 default.
2. **Fresh Fixture, Persistent** — per-test fixture in a persistent store; needs explicit teardown.
3. **Minimal Fixture** — smallest fixture that exercises the concern. If the test only needs one row, don't build the customer + account + 10 orders.
4. **Shared Fixture** — multiple tests reuse one. **Avoid unless read-only.** Primary cause of Interacting Tests + Fragile Fixture.

Setup styles, in order of clarity:

- **In-line Setup** — fixture built inside the test. Most explicit; use for unusual fixtures.
- **Delegated Setup** — test calls a Creation Method (`_a_proposal(...)`). Default for shared shapes — pairs with the Test Data Builder pattern in `tdd.md`.
- **Implicit Setup** — fixture built in pytest's `fixture` decorator. Use when identical across many tests in one class.
- **Lazy Setup** — fixture built in first test that needs it. `scope="session"` fixtures.

## Principles — Meszaros's 13 (in priority order)

The TDD rule names the first two via different terminology. The rest are vocabulary the long-form guide expands. In review situations, cite the principle by number or name:

1. **Write the Tests First** — TDD; production code falls out of tests.
2. **Design for Testability** — load-bearing when TDD is skipped.
3. **Use the Front Door First** — public interface + State Verification; Back Door Manipulation is last resort.
4. **Communicate Intent** — Single-Glance Readable; tests > ~10 lines are a smell.
5. **Don't Modify the SUT** — no `if testing then ...` branches; no Test Hooks in production.
6. **Keep Tests Independent** — Fresh Fixture per test.
7. **Isolate the SUT** — Test Doubles for DOCs the test isn't verifying; Dependency Injection.
8. **Minimize Test Overlap** — each test condition covered by exactly one test.
9. **Minimize Untestable Code** — refactor untestable code into a Humble Object.
10. **Keep Test Logic Out of Production Code.**
11. **Verify One Condition per Test** — Single-Condition Test; multiple assertions on one logical condition is fine, multiple distinct behaviors is a split.
12. **Test Concerns Separately** — when a method handles multiple concerns, test each in its own test.
13. **Ensure Commensurate Effort and Responsibility** — test effort ≤ SUT effort; if it's higher, the SUT needs testability work.

## The five-step roadmap

When designing the test surface for a new module, walk these steps in order:

1. **Exercise the happy path** — one Simple Success Test; runs without crashing.
2. **Verify direct outputs of the happy path** — assertions on return values and post-test state.
3. **Verify alternative paths** — vary args + pre-test state + indirect inputs via Test Stubs (Responder or Saboteur).
4. **Verify indirect output behavior** — Test Spies or Mock Objects when State Verification cannot express the invariant.
5. **Optimize execution and maintainability** — Fake Object for slow DOCs; Custom Assertion for repeated patterns; reduce Test Code Duplication.

## Anti-patterns (refuse on sight)

- Using "mock" generically when Stub or Spy is what's actually wired.
- Shared Fixture across mutating tests (cause of Interacting Tests).
- Standard Fixture forced onto a test that only needs a slice (cause of Fragile Fixture).
- Behavior Verification when State Verification would work (over-specifies the SUT).
- Mock Object with many expectations on the same DOC (treat each expectation as a separate test or step back to Test Spy).
- Multiple-assertion test with no Assertion Messages (Assertion Roulette).
- `if`/`for`/`try` in test body (Conditional Test Logic).
- Test name in method-shape rather than behavior-shape (see `tdd.md`).
- Reusing test ending state as next test starting state (Interacting Tests; same as principle #6 violation).

## Self-audit checklist (pair with `tdd.md`'s)

Before declaring a test PR done:

1. Each Test Double is named precisely (Dummy / Stub / Spy / Mock / Fake) in code comments or variable names.
2. Indirect inputs are controlled via Stubs; indirect outputs are verified via Spies or Mocks, not via the SUT's direct return.
3. Four phases are visible at a glance — single Setup, single Exercise, single Verify, implicit Teardown.
4. No Shared Fixture across mutating tests.
5. No Conditional Test Logic.
6. No Eager Test — one concern per Test Method.
7. Assertion Messages on every assert that compares values; absence is documented if intentional.
8. Test Utility Methods used wherever the same fixture-building or verification logic appears in 2+ tests.
9. State Verification preferred; Behavior Verification only where State cannot express the invariant.
10. Fresh Fixture per test unless verified non-mutating.
