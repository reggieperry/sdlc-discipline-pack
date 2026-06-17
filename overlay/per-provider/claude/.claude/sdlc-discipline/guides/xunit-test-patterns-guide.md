# xUnit Test Patterns — guide

A principal-engineer reference for the vocabulary and design language of test code. The spine of this guide is Gerard Meszaros, *xUnit Test Patterns: Refactoring Test Code* (Addison-Wesley Signature Series, 2007), with citations by page where the position depends on the primary text. Where the GOOS guide (`goos-guide.md`) already covers a point, this guide cites it rather than restating; Meszaros and Freeman/Pryce overlap substantially on the TDD spine but Meszaros uniquely supplies the **pattern language** — the named smells and named patterns that the team uses when talking about test code.

The terse rule that loads on test paths is `rules/craft-xunit.md`. This guide is the reasoning, the citations, and the worked examples.

A vocabulary note. **Test Double** is the umbrella; **mock** is one of five distinct subtypes. Saying "I added a mock" when what was added is a Stub or a Spy is one of the most common sources of confusion in test-code review. The taxonomy in §3 below is the load-bearing piece of this guide; everything else extends or interprets it.

---

## 1. The thesis

Meszaros opens with three claims that everything else rests on (Preface, pp. xxi–xxv):

1. **Test code is production code.** It runs, it has invariants, it has maintenance cost. Refactor it.
2. **Test pain is design feedback** — same thesis as GOOS, arrived at independently. A hard-to-write test is a design problem in the SUT, not a test-author problem.
3. **There is a pattern language for test code** that, once shared, makes test design discussable. The same way Fowler's *Refactoring* gave names to production-code smells (Long Method, Feature Envy, Shotgun Surgery), Meszaros names test-code smells (Fragile Test, Eager Test, Mystery Guest) and the patterns that resolve them (Test Stub, Custom Assertion, Test Utility Method).

> "Test code is just as important as production code, and it needs to be refactored just as often." (p. 187)

The pattern language is not bureaucratic vocabulary. It's the operative tool: when a reviewer says "this is an Eager Test" rather than "this test looks like it does too much," the conversation has a name, a sub-cause catalog, and a known fix.

## 2. The four-phase test

Every Fully Automated Test has four sequential phases (Four-Phase Test, p. 358):

1. **Setup** — build the fixture: test data, the SUT, any Test Doubles, install the Doubles into the SUT.
2. **Exercise** — call the SUT with the inputs the test cares about. *One call.* If the test needs a sequence, it's verifying multiple concerns.
3. **Verify** — assert on direct outputs (return values, post-test state) and indirect outputs (Spy / Mock assertions).
4. **Teardown** — tear down the fixture. Prefer **Implicit Teardown** (pytest `yield` with `try/finally`) over in-line teardown. Best is **Fresh Fixture** so teardown is just garbage collection.

The phases must be visible at a glance. Blank lines separating them; comments only if the structure isn't already obvious. Alternating exercise/verify calls inside a Test Method is an **Eager Test** (p. 192) — the surest sign a Test Method is testing more than one concern.

The "Arrange-Act-Assert" naming popular in many xUnit-derived frameworks (and used in `rules/craft-tdd.md`) is the same four-phase anatomy with Teardown collapsed into the framework's implicit-teardown lifecycle.

## 3. The Test Double taxonomy

The most cited piece of Meszaros's work. Five distinct kinds of Test Double (Chapter 11, pp. 125–151; pattern definitions in Chapter 23, p. 521 onward):

### 3.1 Dummy Object (p. 728)

Passed as an argument to satisfy a signature; never used by the test. In dynamic Python, often `None` or `object()`. In statically typed code, may need a real type-compatible instance.

```python
def test_invoice_addLineItem(self) -> None:
    invoice = Invoice(customer=DummyCustomer())  # Customer parameter is required, but irrelevant here
    invoice.add_line_item(product, quantity=1)
    assert invoice.line_items == [LineItem(product, 1)]
```

Not the same as a Null Object pattern (which has real behavior of doing nothing). A Dummy Object's behavior is irrelevant because the test never reaches a call that would invoke it.

### 3.2 Test Stub (p. 529)

Replaces a depended-on component (DOC) to control the SUT's **indirect inputs**. Returns canned responses.

Two sub-types:

- **Responder** — returns valid or invalid values to drive normal-path branches of the SUT.
- **Saboteur** — raises exceptions to drive error-handling branches.

```python
class _ClockStub:
    """Responder: returns the configured 'now'."""
    def __init__(self, now: datetime) -> None:
        self._now = now
    def now(self) -> datetime:
        return self._now


class _IBConnectionSaboteur:
    """Saboteur: raises on every call."""
    def place_orders(self, specs: list[OrderSpec]) -> list[OrderHandle]:
        raise ConnectionError("simulated disconnect")
```

Test Stub is the right pick when the SUT calls into a peer whose return value the test needs to vary across cases. Configure the Stub during Setup; the SUT uses it transparently during Exercise.

### 3.3 Test Spy (p. 538)

A Stub that *records* how it was called so the test can assert on the calls after Exercise. Implements **Behavior Verification** via post-hoc inspection.

```python
class _NotifierSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
    def notify(self, subject: str, body: str) -> None:
        self.calls.append((subject, body))


def test_risk_failure_alerts_operator(self) -> None:
    spy = _NotifierSpy()
    gate = RiskGate(notifier=spy)
    gate.evaluate(oversized_proposal)
    assert spy.calls == [("RiskGate rejected", "2% Rule exceeded for AAPL")]
```

Test Spy is the default when the test cares about *what the SUT does to its peers*, not just *what the SUT returns*. Prefer it over Mock Object when the assertion can be made after Exercise.

### 3.4 Mock Object (p. 544)

A DOC replacement loaded with **expectations** during Setup; asserts during Exercise and fails immediately on an unexpected call. Implements Behavior Verification via expectation matching.

```python
def test_authorized_trade_calls_executor_in_order(self) -> None:
    mock_executor = MockExecutor()
    mock_executor.expect_place_orders(entry_spec)  # pre-set
    mock_executor.expect_place_orders(stop_spec)   # pre-set
    pattern = TripleScreenLongEntry(...)
    pattern.execute(mock_executor)
    mock_executor.verify_all_expectations_met()
```

Mock Object is heavier than Test Spy because the test couples to the call sequence. Reach for it when the *exact* sequence is part of the invariant (security operations, transactional ordering, OCA-group setup). For most cases, a Test Spy plus a single post-hoc assertion on the recorded calls is sufficient and reads more clearly.

Meszaros's Mock Object is the "classical" mock-with-expectations pattern. Python's `unittest.mock.MagicMock` is more flexible: it defaults to recording (Test Spy-like) and gains expectation behavior when you write `mock.assert_called_with(...)`. The vocabulary still applies — when you set expectations up front and assert during, you have a Mock Object; when you record and assert after, you have a Test Spy.

### 3.5 Fake Object (p. 551)

A working alternative implementation of the DOC, lighter than the real thing. In-memory database for a real SQL store; in-process queue for a real message broker; deterministic clock for a real wall clock.

```python
class _InMemoryDecisionRepository:
    """Fake: implements the same Protocol as the real DecisionRepository,
    but stores records in a list rather than a Postgres table."""
    def __init__(self) -> None:
        self._records: list[DecisionRecord] = []
    def save(self, record: DecisionRecord) -> None:
        self._records.append(record)
    def for_run_id(self, run_id: str) -> list[DecisionRecord]:
        return [r for r in self._records if r.run_id == run_id]
```

Fake Object is preferred over Test Stub when the test cares about the DOC's *semantics* rather than just its calls — for example, when many tests need a working repository and stubbing per-test is tedious. The Fake is itself testable (and should have a Contract Test verifying it conforms to the same Protocol as the real implementation).

### 3.6 Configurable vs Hard-Coded Test Doubles

- **Hard-Coded Test Double** (p. 568) — responses baked into the subclass. Use when responses are universal across all tests of one suite.
- **Configurable Test Double** (p. 558) — accepts a setup phase that feeds it values. Default.

Most test frameworks favor configurable: `unittest.mock.MagicMock().return_value = ...` is configurable per-test. Hard-coded versions show up when the response is invariant across the suite (e.g., a fake clock that always returns 2026-05-19).

### 3.7 The taxonomy diagram

```
                    Test Double
                        │
        ┌───────┬───────┼────────┬────────┐
        │       │       │        │        │
     Dummy    Test    Test     Mock     Fake
     Object   Stub    Spy      Object   Object
              │       │
        ┌─────┴─────┐ │
     Responder Saboteur
```

When code review names a Test Double precisely (Stub vs Spy vs Mock), the conversation about whether the test over-specifies the SUT becomes resolvable.

## 4. Indirect inputs and indirect outputs

The vocabulary that makes the Test Double choices legible (Chapter 11, pp. 125–132):

- **Direct input** — argument the test passes to the SUT. Trivially controlled by the test.
- **Indirect input** — value received from a peer the SUT calls into. Controlled via Test Stub (or Fake) standing in for the peer.
- **Direct output** — return value from the SUT to the test. Verified with assertions on the return.
- **Indirect output** — method call the SUT makes to a peer. Verified via Test Spy (post-Exercise inspection) or Mock Object (pre-Exercise expectation).

Most testing pain comes from failing to recognize that the SUT has indirect inputs or outputs that the test isn't controlling or verifying. The five-step roadmap (§9) makes the recognition systematic.

### 4.1 State Verification vs Behavior Verification

- **State Verification** (p. 462) — assert on the SUT's post-Exercise state. Touches the return value or queries the SUT.
- **Behavior Verification** (p. 468) — assert on the calls the SUT made to its peers during Exercise.

Meszaros's recommendation, repeated through the book: **prefer State Verification**. Behavior Verification couples the test to the SUT's implementation (how it talks to peers); State Verification couples only to the SUT's contract (what state it ends in).

> "Behavior Verification is a more invasive form of testing because it requires the test to know more about how the SUT works." (p. 470)

Behavior Verification earns its place when:

- The SUT has no observable state after Exercise (a fire-and-forget action like logging or notifying).
- The invariant is *about the calls* (the order of OCA-group submissions; the exact set of cleanup operations during shutdown).
- The DOC has no inspectable state (e.g., the real DOC is a remote API whose state can't be queried).

For everything else, reach first for State Verification and a Fake DOC.

## 5. The thirteen principles

From Chapter 5, pp. 39–48. In priority order — when in doubt, the earlier principles override the later ones. Some overlap with GOOS; the rationale below cites where they extend rather than restate the GOOS guide.

### 5.1 Write the Tests First (p. 40)

The TDD discipline. GOOS calls this the Golden Rule. Tests before code clarify acceptance criteria, expose coupling, and force the production code to be designed for testability.

### 5.2 Design for Testability (p. 40)

Load-bearing when TDD is skipped (which it shouldn't be, but is, sometimes). Constructor injection over service location; sensible peer-stereotype defaults (`craft-tdd.md`'s peer stereotypes); the Humble Object pattern (p. 695) for components that can't be tested directly.

### 5.3 Use the Front Door First (p. 41)

Test through the public interface using State Verification. Behavior Verification (Spy/Mock) and Back Door Manipulation (peeking at internals, hitting the database directly) are reached for only when the front door cannot express the invariant.

> "Overuse of Behavior Verification and Mock Objects can result in Overspecified Software and tests that are more brittle and may discourage developers from doing desirable refactorings." (p. 41)

This is the principle most often violated in agentic test generation: it's easier for an LLM to wire up `unittest.mock.MagicMock` and assert on `.call_args` than to reason through what the SUT's state is supposed to look like after Exercise. Reviewers should flag this aggressively.

### 5.4 Communicate Intent (p. 42)

Tests are documentation. "Single-Glance Readable" — the test reads in one screen and the intent is obvious. Extract Test Utility Methods with Intent-Revealing Names. Tests longer than ~10 lines (Meszaros's threshold, p. 42 footnote) are smells.

The companion patterns are Creation Method (p. 415), Custom Assertion (p. 474), and Test Utility Method (p. 599). Used together, they reduce a 30-line test into:

```python
def test_oversized_proposal_is_rejected(self) -> None:
    proposal = a_proposal().with_risk(Decimal("250")).build()
    decision = self.gate.evaluate(proposal)
    assert_rejected_for(decision, "2% Rule cap of $200")
```

### 5.5 Don't Modify the SUT (p. 41)

No `if testing then ...` branches in production. No debug flags that change behavior. No environment-sniffing. If the SUT needs Test Hooks (p. 709), the design is wrong — refactor the design, don't add the hook.

Meszaros makes one exception explicit: a Test-Specific Subclass (p. 579) that overrides only the methods the test needs to control. Even there, the subclass must not override behavior the test is verifying.

### 5.6 Keep Tests Independent (p. 42)

Each test can run alone. Fresh Fixture per test (p. 311), not Shared Fixture (p. 317). Interacting Tests are the most common cause of Erratic Tests — they fail in clusters, mask each other, and resist Defect Localization.

### 5.7 Isolate the SUT (p. 43)

Use Test Doubles to replace DOCs whose behavior the test isn't verifying. Dependency Injection (p. 678) over service location. Avoid touching ambient infrastructure (clock, environment, network) directly — wrap and inject.

### 5.8 Minimize Test Overlap (p. 44)

Each test condition covered by exactly one test. Two tests that fail together for the same root cause are duplication; refactor.

### 5.9 Minimize Untestable Code (p. 44)

GUI logic, multi-threaded code, untestable Test Methods themselves — refactor into a Humble Object. The Humble Object is a thin shell around a testable component the shell delegates to.

### 5.10 Keep Test Logic Out of Production Code (p. 45)

No `if testing then` branches; no debug flags; no environment-sniffing. Test logic in production is invisible from outside the SUT but changes behavior under test, defeating the test.

### 5.11 Verify One Condition per Test (p. 45)

Single-Condition Test. **One assertion per test is fine; multiple assertions on the same logical condition is also fine.** What's NOT fine is verifying two distinct behaviors in one Test Method.

The distinction: asserting all five fields of a returned object after Exercise is one condition ("the object came back with the right shape"). Asserting that the SUT returned `True` AND notified the listener AND incremented the counter is three conditions; split them.

### 5.12 Test Concerns Separately (p. 47)

When a method handles multiple concerns, test each concern in its own test so failures point at the broken concern, not "something in this method." This principle is the test-side mirror of the Single Responsibility Principle on the production code.

### 5.13 Ensure Commensurate Effort and Responsibility (p. 47)

Effort to write/maintain a test should not exceed the effort to write the SUT. If it does, *the SUT needs testability work* — the test shouldn't bear the load. Data-Driven Tests (p. 288), Parameterized Tests (p. 607), and Test Utility Methods exist for this reason.

## 6. The smell catalog

Meszaros classifies smells into three tiers (Chapter 2, pp. 9–17; Chapters 15–17 contain the detailed entries):

### 6.1 Project Smells (Chapter 17, p. 259)

Symptoms a project manager would notice, even without reading test code:

- **Production Bugs** (p. 268) — bugs reaching production despite the test suite. Often caused by Untested Code (paths the suite doesn't reach) or Untested Requirements (behaviors the suite doesn't verify).
- **Buggy Tests** (p. 260) — tests with bugs in the test code itself. False negatives.
- **Developers Not Writing Tests** (p. 263) — caused by overly aggressive schedules, hostile management, missing testability in the SUT, or Fragile Tests that erode confidence.
- **High Test Maintenance Cost** (p. 265) — the suite costs more to maintain than the production code costs to write. Cause for halting test investment if not addressed; almost always traces to Fragile Tests + Obscure Tests + Shared Fixture problems.

### 6.2 Behavior Smells (Chapter 16, p. 223)

Symptoms that surface at compile or run time:

- **Fragile Test** (p. 239) — passes today, fails tomorrow on unrelated change. Four sub-types:
  - **Interface Sensitivity** — couples to UI or API shape that doesn't matter to the test's intent.
  - **Behavior Sensitivity** — fails on SUT changes that shouldn't have semantic effect.
  - **Data Sensitivity** — depends on shared data that drifted (Shared Fixture cause).
  - **Context Sensitivity** — depends on ambient state (clock, network, files, OS).
- **Erratic Test** (p. 228) — sometimes-passes, sometimes-fails on the same code. Sub-types:
  - **Interacting Tests** — Shared Fixture pollution.
  - **Test Run Wars** — parallel runs against a shared resource.
  - **Unrepeatable Tests** — depend on time, randomness, or environment without seeding.
- **Slow Tests** (p. 253) — > 30s; developer stops running per-change.
- **Assertion Roulette** (p. 224) — multiple assertions, no messages, failure log doesn't say which.
- **Frequent Debugging** (p. 248) — need the debugger to figure out why a test failed. Sign of coverage gap or Eager Test.
- **Manual Intervention** (p. 250) — test requires a human to advance.

### 6.3 Code Smells (Chapter 15, p. 185)

Read-the-code smells:

- **Obscure Test** (p. 186) — can't grasp the behavior at a glance. Sub-causes:
  - **Mystery Guest** (p. 198) — fixture data appears in the test from nowhere; the reader can't tell where it came from.
  - **Eager Test** (p. 192) — verifies multiple concerns in one Test Method.
  - **Irrelevant Information** (p. 195) — constants and helpers that distract from the test's concern.
- **Conditional Test Logic** (p. 200) — `if`/`for`/`try` in the test body. The logic the test takes makes the test itself testable — the failure mode.
- **Hard-to-Test Code** (p. 209) — the SUT is the smell. Refactor the SUT for testability.
- **Test Code Duplication** (p. 213) — the same fixture-building or verification logic in multiple tests. Extract Test Utility Methods.
- **Test Logic in Production** (p. 217) — `if testing then` branches; debug flags. Violates principle #10.

## 7. Fixture strategies

A **Fixture** (p. 297) is the state the SUT needs to be in before Exercise. Strategies form a hierarchy of preference:

### 7.1 By lifetime

- **Fresh Fixture** (p. 311) — built per Test Method, garbage-collected after.
  - **Transient** — in-memory; teardown is GC.
  - **Persistent** — needs explicit teardown (e.g., Database Sandbox + Table Truncation Teardown).
- **Shared Fixture** (p. 317) — multiple tests reuse one instance. Avoid for mutating tests; primary cause of Interacting Tests and Fragile Fixture.

### 7.2 By size

- **Minimal Fixture** (p. 302) — smallest fixture that exercises the concern. *Default.* Don't build the customer + account + 10 orders when the test only needs one row.
- **Standard Fixture** (p. 305) — same fixture shape across many tests. Tempting for DRY but yields Fragile Fixture: changing the shape breaks N tests at once.

### 7.3 By setup style

- **In-line Setup** (p. 408) — fixture built inside the Test Method. Most explicit; use for unusual fixtures or one-off tests.
- **Delegated Setup** (p. 411) — Test Method calls a Creation Method (`_a_proposal(...)`). Default for shared shapes; pairs with the Test Data Builder pattern in `craft-tdd.md`.
- **Implicit Setup** (p. 424) — fixture built in a `setUp` method (or pytest `fixture` decorator). Use when the fixture is identical across many tests in one class.
- **Lazy Setup** (p. 435) — fixture built in the first test that needs it. `scope="session"` fixtures in pytest.

### 7.4 By teardown style

- **Implicit Teardown** (p. 516) — framework calls cleanup. Pytest's `yield` fixtures with `try/finally` are this shape.
- **In-line Teardown** (p. 509) — cleanup at the end of the Test Method.
- **Garbage-Collected Teardown** (p. 500) — language GC cleans up. Works only for in-memory fixtures.
- **Automated Teardown** (p. 503) — framework tracks resources created during Setup and frees them automatically.

For Elder, the default is: Fresh Fixture (Transient) + Minimal Fixture + Implicit Setup via pytest fixtures + Implicit Teardown via `yield`. Standard Fixture only appears when explicitly justified.

## 8. The five-step roadmap (Chapter 14)

Meszaros's synthesis: when designing the test surface for a new module, walk these five steps in order. The order also doubles as a learning progression for teams new to test automation.

### 8.1 Exercise the happy path

One Simple Success Test (p. 348) that calls the SUT through its public interface with valid inputs. No assertions yet. Pass = doesn't crash. This step proves the test infrastructure can reach the SUT.

### 8.2 Verify direct outputs of the happy path

Add assertions on return values and post-test state. This turns the test into a Self-Checking Test (p. 26). State Verification.

### 8.3 Verify alternative paths

Vary the SUT's arguments + pre-test state + indirect inputs. Indirect inputs are controlled via Test Stubs:

- **Responder Stubs** for valid/invalid return values.
- **Saboteur Stubs** for exception-raising DOCs.

This is where the test design earns its keep — most production bugs hide in alternative paths, and most untested code is alternative-path code.

### 8.4 Verify indirect output behavior

When State Verification cannot express the invariant (the SUT's effect is on a peer, not on its own state), reach for Behavior Verification. Test Spy by default; Mock Object when the call sequence is the invariant.

### 8.5 Optimize execution and maintainability

Once the test surface is complete, address the cost-of-ownership smells:

- **Slow Tests** → Fake Object for slow DOCs, Minimal Fixture, in-memory store.
- **Obscure Tests** → Custom Assertion, Creation Method, Test Utility Method.
- **Test Code Duplication** → extract Test Utility Methods.
- **Buggy Tests** → Test Utility Test (testing the test utilities themselves).

The optimization step is iterative across the suite's lifetime — it's not a one-time pass after the first version of the tests lands.

## 9. Relationship to the other testing rules and guides

This guide is a sibling to `goos-guide.md`; both treat tests as design feedback. The differences in emphasis:

- **GOOS / Freeman & Pryce** — tests *drive* design. The test-first cycle is the design conversation. Mocks are a design discovery tool. The London-school TDD style.
- **Meszaros** — tests are *artifacts* with their own design language. The patterns and smells form a vocabulary for reviewing test code. Less prescriptive about TDD style; more rigorous about the test-code-as-code thesis.

In Elder, the two are complementary:

- The TDD discipline in `rules/craft-tdd.md` is GOOS-shaped: outer/inner loops, listen-to-the-tests, peer stereotypes, test data builders.
- The structural rules in `rules/craft-tdd.md` are pragmatic xUnit conventions: arrange/act/assert, fresh fixture, fault injection, property tests.
- The vocabulary in `rules/craft-xunit.md` (and this guide) is Meszaros-shaped: name the Double, name the smell, name the pattern.

When a chain agent or reviewer is in doubt about *what to do*, they reach for `craft-tdd.md` first. When they need to *name what's wrong*, they reach for `craft-xunit.md`. The two are not in tension.

The DDD guide (`ddd-guide.md`) and Modularity guide (`modularity-guide.md`) are upstream of all of this: they shape the SUT into something testable. The Refactoring guide (`refactoring-guide.md`) supplies the moves used during the refactor step. Together, the five guides form the discipline pack's principal-engineer reference shelf.

## 10. References

- Meszaros, G. (2007). *xUnit Test Patterns: Refactoring Test Code*. Addison-Wesley. ISBN 0-13-149505-4. Page references throughout this guide.
- Freeman, S. & Pryce, N. (2009). *Growing Object-Oriented Software, Guided by Tests*. Addison-Wesley. See `goos-guide.md`.
- Fowler, M. et al. (1999). *Refactoring: Improving the Design of Existing Code*. Addison-Wesley. See `refactoring-guide.md`.
- Beck, K. (2002). *Test-Driven Development: By Example*. Addison-Wesley. The originating TDD text.
- van Deursen, A., Moonen, L., van den Bergh, A., & Kok, G. (2001). "Refactoring Test Code." *XP2001 Proceedings*. The paper that coined the test-smell vocabulary Meszaros expanded.
