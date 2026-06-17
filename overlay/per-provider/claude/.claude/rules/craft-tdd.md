---
paths:
  - "**/*.go"
  - "**/*.sh"
  - "**/*.py"
  - "tests/**"
---

# Test-driven development and the design feedback it gives

How tests drive design, not just verify it. Source: Steve Freeman & Nat Pryce, *Growing Object-Oriented Software, Guided by Tests*. The mechanics of writing tests live in the language overlay; this rule is the cadence and the design discipline.

> Full reasoning, citations (Freeman & Pryce 2009), and worked examples: `.claude/sdlc-discipline/guides/goos-guide.md`.
> See the active language overlay (`go-testing.md`, the `python-*` set) for test mechanics, fixtures, mocking idiom, and property testing; `craft-xunit.md` for the test-double stereotypes and test-data-builder patterns (Meszaros); `craft-complexity.md` for the deep modules testable code tends toward; `craft-abstraction.md` for the small interfaces that "mock roles, not objects" produces; and `craft-refactoring.md` for the catalog the refactor step draws from.

## The cycle and its purpose

- **Write no new functionality without a failing test first** — the golden rule. The failing test says what to build and when you are done.
- **Two loops.** Outer: one acceptance test per feature, run end-to-end through the real entry point against a production-like deployment. Inner: one unit test per behavior, driving object design. The acceptance test stays red until the feature is done; the unit tests cycle green inside it.
- **Run test → make it pass with the simplest code → refactor, and repeat.** Keep each step's implementation the simplest thing that passes; clean up under the green bar.
- **Watch the test fail before you make it pass, and read the failure message.** Verify it fails *for the reason expected* — a failure for a different reason means you misunderstood the code or your test setup is wrong; fix that before writing implementation. A wrong or unclear failure means your diagnostics are weak — fix that now too.
- **Start each feature with one failing acceptance test in domain terms**, then drive the units inside it. Begin with the simplest success case (not the error cases — note those for later), and work outside-in from the inputs toward the outputs, discovering collaborators as you go.

## Watch the test fail — and prove it can fail

- Run the test before writing implementation. Confirm it fails for the right reason and the diagnostic is informative.
- The diagnostic message must be in domain language. `"order risk $250 exceeds the per-trade cap of $200"` over `"value mismatch"`. A year from now, that message is the only clue to a failure.
- For existing code where a new test already passes, *inject a fault* into the production code and confirm the test goes red. A test that won't fail when it should is not a test.

## Start with the simplest success case

- Don't start with degenerate or failure cases — they don't validate the model and they're bad for morale.
- Record failure cases on a notepad as you discover them; come back to handle them.
- A feature is complete when every recorded case is handled or explicitly deferred.

## End-to-end means deployed, not edge-to-edge

Acceptance tests drive the system through its real entry point against a production-like deployment. Tests that instantiate internal objects and assert on internal state are *edge-to-edge*, not end-to-end. Use them only when end-to-end is impossible, and document the gap.

## The walking skeleton and the layers

- **Build a walking skeleton first** — the thinnest slice you can automatically build, deploy, and test through the *whole* architecture, with minimal real behavior, the real entry point, and real third-party integrations (or carefully chosen fakes). It flushes out integration and process risk while there is still time to act.
- **Don't defer the integration to "when the components are ready"** — that is the late-integration trap. Establish the deploy/test pipeline first, then grow features on the skeleton.
- **Layer the tests: acceptance** (does the whole deployed system do the job?), **integration** (does our code work against code we can't change?), and **unit** (do our objects do the right thing and compose conveniently?). Keep most tests fast in-memory unit tests, fewer integration, fewest end-to-end.
- **Separate tests that measure progress** (new, expected to fail) **from tests that catch regressions** (must always stay green); never commit a failing unit test to the shared branch.

## The refactor step is mandatory — local and global

Red-green-refactor has three beats. The third is non-negotiable and has two scopes.

**Local refactor** — clean up the immediate change under green tests. Apply only named refactorings from the catalog (`craft-refactoring.md`). If you cannot name the move, you are rewriting; stop.

**Global refactor (required once a unit of work's steps are complete)** — take a wide view of every file your diff touched. For each:

- If the file now exceeds the module's public-surface cap (see `craft-complexity.md` / `craft-abstraction.md`), the thing you added probably belongs elsewhere. Move it now.
- Ask where a future engineer would expect to find each new type/function. If your answer differs from where you put it, move it. The scope a task statement names is a guideline, not a constraint.
- Look at nearby modules (imports, callers, callees). Did your addition reveal a smell from the refactoring catalog (Duplicated Code, Divergent Change, Shotgun Surgery, Large Class)? Name the smell, refactor it.

Cross-module moves under the refactor hat are explicitly allowed — even on modules outside the unit of work's nominal scope — as long as the move is from the catalog and behavior is preserved. Skipping the global pass produces god-modules and shared-file merge conflicts across parallel work. The composition discipline binds at the codebase level, not just at the function level. Record evidence of the global pass: either applied refactor commits or an explicit "no opportunity, justification: …" note.

## Listen to the tests

When a test is hard to write, treat the difficulty as a design defect and fix the design — not the test. The structure that resists testing will resist change. Hard setup, the urge to mock internals, or class-loader tricks are all the code telling you something.

- **Pass dependencies in explicitly; never reach for globals, singletons, a package clock, or hidden statics.** An implicit dependency is still a dependency — making it explicit is what makes the unit testable and honest. A bloated constructor is a smell: extract the arguments that travel together into a named concept.
- **Keep expectations few.** Many expectations per test means the unit is too big or you are over-specifying its interactions.

Common test pains and the design problem each one names (idiom for the fix is in the language overlay):

| Test pain | Design problem | Refactor toward |
| --------- | -------------- | --------------- |
| Need to mock the wall clock or environment | Hidden time/env dependency | Inject a clock; read env once at startup into immutable config |
| Many lines of setup before the action | Bloated SUT or implicit dependencies | Split the SUT, or use test-data builders |
| Need to mock several collaborators | Test boundary too coarse | Break out functionality; narrow the boundary |
| Asserting on internal/private state | Encapsulation leaking | Add events or query methods that describe state in domain terms |
| Need to assert order across many mocks | SUT coordinates too much | Move logic to receivers; use a state machine if order is meaningful |
| Mocking a third-party library hurts | Mocking something you don't own | Wrap the library in your own interface; mock the interface |
| Want to assert on exact log lines | Logging mixed with domain logic | Domain notification interface (e.g. a `cost_ledger.record(...)` port); the logger is one impl |
| Adding setters to the SUT just for testing | Constructor injection missing | Inject through the constructor; remove the setters |

## Claims in prose need tests behind them

Words like *idempotent*, *no-op on retry*, *cancellation-safe*, *bounded*, *degraded mode*, *graceful fallback*, *safe to re-run* are specifications, not adjectives. If the test that exercises the path doesn't exist, the claim is aspirational and probably wrong. The same listening that applies to a hard-to-write test applies to documentation.

- When a docstring or comment says the code handles a fallback / degraded mode / failure case, immediately ask: does a test exercise that path? If yes, the prose should align with the test. If no, either write the test or soften the prose ("intended to" / "TODO: verify under X").
- A "no-op" claim that hasn't been triggered is asserted, not proven. Degraded-mode paths are the ones most likely to run unexpectedly in production — exactly the ones that need to actually work.
- The opposite trap: don't put aspirational behavior in a docstring as if it's how the code works. Aspirational text belongs in TODOs or design notes, not in the function's description of itself.
- A test proving cancellation-safety must actually invoke cancellation and inspect the durable record afterward. A test proving a cap must drive the function past the cap and assert the no-op. A test proving idempotency must invoke twice and inspect the resulting state.

## Mocks, used well

- **Mock roles, not objects.** Focus on the messages between collaborators — the relationships — not the classes. This is the discipline's central correction to itself.
- **Only mock types you own.** Wrap a third-party API (the model SDK, a CLI like `git` or a linter, the data store, the filesystem) in a thin adapter defined in your own terms, and verify that adapter with focused integration tests. You get no design feedback from mocking code you can't change, and the stub can lie about behavior the real thing doesn't have. Break-glass exception only for legacy code or third-party APIs with no exit ramp; document the exception.
- **Mock an object's peers, never its internals.** Don't mock concrete classes (subclass-and-override hides the relationship) — mock interfaces/roles.
- **Don't mock values** — construct them (use a test data builder if construction is painful). A value type is something to build, not to fake.
- **Allow queries, expect commands.** Queries are side-effect-free and may be called any number of times — set them up as *allowances* (supporting infrastructure). Commands change the world, so their occurrence is the assertion the test is making — set them up as *expectations*. A test where everything is an expectation reads as if everything is equally important; distinguishing the two makes the actual assertion legible.

### Object peer stereotypes

Each peer the SUT depends on is one of three kinds; bloated constructors usually conflate them, so re-categorize before splitting:

- **Dependency** — a required service the object cannot work without; a constructor parameter, no default.
- **Notification** — a fire-and-forget listener; one-way; defaults to a no-op.
- **Adjustment** — a strategy or policy that tunes behavior; defaults to a sensible value.

See `craft-xunit.md` for the full test-double taxonomy (dummy / stub / spy / mock / fake) and the test-data-builder pattern these stereotypes feed.

## The object style TDD pushes you toward

- **Tell, don't ask** — state what you want in the collaborator's terms and let it decide how, rather than pulling its data out and deciding for it. Ask only when querying a value, a collection, or a factory.
- **Give each object one responsibility you can state without "and", "or", or "but".** Keep objects context-independent — whatever an object needs about its environment is passed in, not built in, which also makes every unit test just another context.
- **Identify roles as narrow, client-driven interfaces, and introduce value types for domain concepts even when they do little** — specific types localize change and attract behavior. (The language overlay gives the idiom.)

## Test quality

- **Test behavior, not methods; name each test as a sentence about what the object does in a scenario.** `rejects_order_exceeding_per_trade_cap` — yes. `test_evaluate_proposal_invalid` (method-shaped) and `test_1` / `test_basic` — never. The name should let a reader diagnose a failure without reading the body.
- **Use a canonical arrange-act-assert shape, one coherent behavior per test.** If the act phase is more than one line, the test exercises too much or the SUT has a workflow that should be its own method.
- **Fresh fixture per test.** Default to building the fixture in each test; don't share mutable state across tests — non-determinism from shared state is one of the most common sources of flake. Share only when construction is genuinely expensive and the tests are verified non-mutating.
- **Few assertions per test.** One verify per test as the rule of thumb: when the first assertion fails, later ones don't run and useful information hides. Tightly related assertions on the same fixture can share a test; independent ones split. The same rule applies to mock expectations.
- **Make failures informative and assertions specific.** Diagnostics are a first-class feature — you should never need a debugger to understand a failure. Assertion messages name the domain expectation. Specify precisely what should happen and no more; over-specification makes brittle tests.

## What to test, and where the risk lives

For each feature, cover at least:

1. The happy path.
2. Authorization or validation rejecting the operation.
3. Missing or malformed input.
4. An edge case at a boundary the domain cares about.

Then **probe the boundaries deliberately** — empty collections, zero, negatives, a collection where a scalar was expected, a string where a number was expected. Adopt an adversarial mindset: "how would I break this if I were trying to?" That surfaces cases the happy-path mindset misses.

Write tests **where the risk lives**. Don't test getters and setters with no logic; do test the calculation, the state transition, the failure mode, the boundary. *"It is better to write and run incomplete tests than not to run complete tests"* (Fowler).

## Async and concurrency test discipline

The neutral discipline (idiom — `asyncio`, goroutines, channels, executors — is in the language overlay):

- **Listen, don't sleep.** Subscribe to events from the system and block until the expected event arrives or a timeout fires. When you must poll, sample observable state on a short interval with a bounded timeout, and succeed fast — return as soon as the expected state is observed. Never sleep a fixed duration and then assert.
- **Test that nothing happens by driving a probe.** After the action under test, drive an unrelated event and assert it was processed. The probe proves the system has had time to do whatever it would have done.
- **Externalize event sources.** No internal timers; pass in a scheduler the test can drive.
- **Pass the executor/scheduler in.** Tests use a synchronous runner; production uses the real concurrency primitive.
- **Two test types per concurrent object.** A functional unit test (synchronous, verifies logic) and a stress test (real concurrency, verifies invariants under load).
- **Don't let task or process boundaries swallow exceptions.** Wrap task bodies and publish failure events explicitly; always await the future or attach a callback. Dropped futures lose exceptions.

Flickering tests are real signals — investigate every flicker as a synchronization bug or a real race, never tolerate it.

## Failure handling is a domain decision

- State the failure policy explicitly: "On X, mark Y as failed and stop; do not attempt recovery."
- Catch broadly only at the message-translator boundary (parse-or-don't is binary).
- Compose the failure handling: a separate listener handles cleanup, not the SUT.
- Logging-as-feature: route failures through a domain notification interface (e.g. `failure_reporter.cannot_translate(message, error)`), not a raw logger call.

## Migration testing (schema-evolving stores)

When a change touches a versioned schema (relational DB, search index, on-disk binary format):

- Apply the migration to a clean store from empty → success.
- Apply it to a populated copy when the migration touches existing data — verify data is preserved correctly.
- Apply the downgrade and re-apply the upgrade. A downgrade that has never been exercised is a stub.
- Exercise the new schema with the new code path that consumes it (write a row / index a document / round-trip a record) — proves the migration produces what callers expect.
- Don't mark migration work done until upgrade + downgrade + upgrade has been verified end-to-end against a real store, not an in-memory shortcut that builds the schema from the application model.

## Antipatterns (refuse on sight)

- Edge-to-edge tests claiming end-to-end.
- Skipping the walking skeleton.
- Starting with failure cases.
- Method-shaped test names.
- Mocking concrete classes, values, or third-party types.
- A bloated constructor with five dependencies.
- Dozens of lines of test setup.
- Asserting on internal state.
- All-expectations mock setup.
- Order-dependent expectations across many mocks.
- Logging mixed with domain logic.
- Hidden time, environment, or concurrency.
- Flickering tests tolerated.
- Fixed-duration sleeps for test synchronization.
- Roll-back-the-test-transaction isolation.
- A `*Repository` / `*DAO` for every entity.
- Object Mother for test data (use chainable builders — see `craft-xunit.md`).

## A choice, not a dogma — reconciling with design-first

GOOS is the canonical **London-school (mockist)** position: drive design outside-in and specify interactions with mocks. The **classicist** position (Beck; Fowler's "Mockist vs Classicist") tests state through real collaborators and reserves doubles for awkward seams. And note the honest tension with `craft-complexity.md`: Ousterhout warns that strict test-first can be too incremental and tactical, and argues you should design the *abstraction* deliberately (design it twice) before chasing features.

Reconcile them: **design the deep abstraction first, then build it and pin its edges test-first.** Default to **classicist** — assert on returned values and state where collaborators are pure and fast — and reach for **hand-written test doubles at the true seams** (the data store, the model call, the filesystem, the clock), which is exactly where "only mock what you own" applies. Use mockist interaction-specification only where the *protocol itself* is under test (for example, that a component emits the right outbound calls in the right order). The language overlay gives the test mechanics.

## Self-audit (binary; partial credit does not exist)

1. The test was watched failing before code was written; the diagnostic message is informative.
2. The test name describes a behavior, not a method.
3. The test reads in domain language.
4. Acceptance tests drive the real entry point and assert on user-visible state.
5. Test data uses builders, not Object Mother.
6. Mocks are peers, not internals or values or third-party types.
7. Queries are allowances; commands are expectations.
8. No hidden time / env / concurrency dependencies.
9. Async tests listen for events or sample with a bounded timeout; no fixed-duration sleeps.
10. The failure policy is explicit; a coarse catch appears only at translator boundaries.
11. No flickering — the test runs ten times in a row without an intermittent failure.
12. The diagnostic message is in the project's domain vocabulary, not generic.
13. The global refactor pass was taken across every file the diff touched, with recorded evidence.

A change failing any item is not finished, no matter how green its tests are.
