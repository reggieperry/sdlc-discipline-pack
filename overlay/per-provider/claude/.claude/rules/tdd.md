---
paths:
  - "**/*.py"
  - "tests/**"
---

> Full reasoning, citations (Freeman & Pryce 2009), and worked examples: `.claude/sdlc-discipline/guides/goos-guide.md`.
> Elder examples in this rule are illustrative — they show what TDD discipline
> looks like in a non-trivial codebase. The principle applies across projects.

# TDD rules

## The Golden Rule

**Never write new functionality without a failing test.** Two loops: outer (acceptance test per feature, runs end-to-end through `main.py` against the deployed system); inner (unit test per behavior, drives object design).

Slash commands enforce this through Behaviors-section discipline and Cycle (Red/Green) pairing.

## Watch the test fail

- Run the test before writing implementation.
- Verify it fails *for the reason expected*. If it fails for a different reason, fix the misunderstanding before writing code.
- Verify the diagnostic message is in domain language. `"AAPL proposal $250 exceeds 2% Rule cap of $200"` over `"value mismatch"`.

## End-to-end means deployed, not edge-to-edge

Acceptance tests drive the system through its real entry point against a production-like deployment. Tests that instantiate internal objects and assert on internal state are *edge-to-edge*, not end-to-end. Use them only when end-to-end is impossible and document the gap.

## Start with the simplest success case

- Don't start with degenerate or failure cases — they don't validate the model and they're bad for morale.
- Record failure cases on a notepad as you discover them; come back to handle them.
- Feature is complete when every recorded case is handled or explicitly deferred.

## Walking skeleton before features

For new subsystems (e.g., the pipeline refactor in build item #1):

1. Establish the deploy/test pipeline first.
2. Implement the thinnest end-to-end slice — minimal real behavior, real entry point, real third-party integrations (or carefully chosen fakes).
3. Build subsequent features on the skeleton. Don't defer the integration to "when the components are ready" — that's the late-integration trap.

## The refactor step is mandatory — local AND global

Red-green-refactor has three beats. The third is non-negotiable and has two scopes.

**Local refactor** — clean up the immediate change under green tests. Apply only refactorings from the catalog (`refactoring.md`). If you cannot name the move, you are rewriting; stop.

**Global refactor (REQUIRED after all plan steps complete)** — take a wide view of every file your diff touched. For each:

- If the file is now over the modularity cap (≤7 public names per `modularity.md`), the thing you added probably belongs elsewhere. Move it now.
- Ask where a future engineer would expect to find each new type/function. If your answer differs from where you put it, move it. The spec's "In:" scope is a guideline, not a constraint.
- Look at nearby modules (imports, callers, callees). Did your addition reveal a smell from the refactoring catalog (Duplicated Code, Divergent Change, Shotgun Surgery, Large Class)? Name the smell, refactor it.

Cross-module moves under the refactor hat are explicitly allowed — even on modules outside your spec's "In:" scope — as long as the move is from the catalog and behavior is preserved.

Skipping the global pass produces god-modules and shared-file merge conflicts across parallel chain runs. The composition discipline binds at the codebase level, not just at the function level. The worker formula's submit-and-exit gate refuses to advance without recorded evidence of the global pass (either applied `refactor:` commits or an explicit "no opportunity, justification: ..." note in the plan).

## Listen to the tests

When a test is hard to write, the design has a problem. Don't work around it; refactor the production code.

| Test pain | Design problem | Refactor |
| --------- | -------------- | -------- |
| Need to mock `datetime.now()` / `os.environ` | Hidden time/env dependency | Inject `Clock`; read env at module load into `Final[...]` constants |
| 30+ lines of setup before the action | Bloated SUT or implicit dependencies | Split the SUT, or use test-data builders |
| Need to mock 5 collaborators | Test boundary too coarse | Break out functionality; narrow the boundary |
| Asserting on internal state (`obj._private`) | Encapsulation leaking | Add events or query methods that describe state in domain terms |
| Need to assert order across many mocks | SUT coordinates too much | Move logic to receivers; use state machine if order is meaningful |
| Mocking third-party library hurts | Mocking something you don't own | Wrap library in your own interface; mock the interface |
| Want to test exact log lines | Logging mixed with domain logic | Domain notification interface (`cost_ledger.charge_llm_cost(...)`); logger is one impl |
| Adding setters to SUT just for testing | Constructor injection missing | Inject through constructor; remove the setters |

## Claims in prose need tests behind them

Words like *idempotent*, *no-op on retry*, *cancellation-safe*, *bounded*, *degraded mode*, *graceful fallback*, *safe to re-run* are specifications. If the test that exercises the path doesn't exist, the claim is aspirational and probably wrong.

- When writing a docstring or comment that says the code handles a fallback / degraded mode / failure case, immediately ask: does a test exercise that path? If yes, the prose should align with the test. If no, either write the test or soften the prose ("intended to be" / "TODO: verify under X").
- A "no-op" claim that hasn't been triggered is asserted, not proven. Degraded-mode paths are the ones most likely to run unexpectedly in production — they're exactly the ones that need to actually work.
- The opposite trap: don't put aspirational behavior in docstrings as if it's how the code works. Aspirational text belongs in TODOs or design notes, not in the function's description of itself.
- Tests proving cancellation-safety must actually invoke cancellation and inspect the durable record afterward. Tests proving a cap must drive the function past the cap and assert the no-op. Tests proving idempotency must invoke twice and inspect the resulting state — see `concurrency.md` for the three idempotency invariants.

## Test names describe behaviors

`test_2pct_rule_rejects_oversized_proposal` — yes.
`test_evaluate_proposal_invalid` — no (method-shaped, not behavior-shaped).
`test_1` / `test_basic` — never.

## Object peer stereotypes

Each peer is one of:

- **Dependency** — required service; constructor parameter; no defaults.
- **Notification** — fire-and-forget listener; one-way; default to no-op.
- **Adjustment** — strategy/policy; default to sensible value.

Bloated constructors usually conflate these. Re-categorize before splitting.

## Test data builders, not Object Mother

Chainable builder with safe defaults:

```python
@dataclass
class _ProposalBuilder:
    ticker: str = "AAPL"
    risk_amount: Decimal = Decimal("100")
    direction: Direction = Direction.LONG

    def for_ticker(self, t: str) -> "_ProposalBuilder":
        return replace(self, ticker=t)

    def with_risk(self, amount: Decimal) -> "_ProposalBuilder":
        return replace(self, risk_amount=amount)

    def build(self) -> TradeProposal:
        return TradeProposal(...)

def a_proposal() -> _ProposalBuilder:
    return _ProposalBuilder()
```

Tests specify only relevant fields:

```python
proposal = a_proposal().for_ticker("AAPL").with_risk(Decimal("250")).build()
```

`make_apple_proposal()`, `make_oversized_proposal()` (Object Mother) does not compose; every variation requires a new factory method.

## Mocks are peers, not internals

- Mock collaborators of the SUT, not the SUT itself.
- Don't mock concrete classes (subclass-and-override hides the relationship). Mock interfaces / `Protocol`s.
- Don't mock values — construct real `Price`, `RiskAmount`, `ImpulseColor` instances.
- Don't mock types you don't own (`httpx`, `chromadb`, `psycopg`, `ib_async`). Wrap in your own interface; mock that.

Break-glass exception only for legacy code or third-party APIs with no exit ramp. Document the exception.

## Allowances vs. expectations

- **Allowance** (`mock.method.return_value = ...`) — a query the SUT *may* call; supporting infrastructure.
- **Expectation** (`mock.method.assert_called_once_with(...)`) — a command the SUT *must* call; the assertion the test is making.

> Allow queries; expect commands.

A test with all-expectations reads as if everything is equally important. Distinguishing them makes the actual assertion legible.

## Async test discipline

- **Listen, don't sleep.** Subscribe to events from the system; block until the expected event arrives or timeout fires.
- **Sample with timeout when you must.** Poll observable state at 100ms with a 5-second timeout. Never `await asyncio.sleep(N)` and then assert.
- **Succeed fast.** Return as soon as the expected state is observed; don't wait the full timeout.
- **Test that nothing happens by driving a probe.** After the action under test, drive an unrelated event and assert it was processed. The probe proves the system has had time to do whatever it would have done.
- **Externalize event sources.** No internal timers; pass a scheduler the test can drive.

Flickering tests are real signals — investigate every flicker as a synchronization bug or a real race.

## Concurrency

- **Pass the executor / scheduler in.** Tests use a synchronous runner; production uses real asyncio / ProcessPoolExecutor.
- **Two test types per concurrent object.** Functional unit test (synchronous, verifies logic) + stress test (real concurrency, verifies invariants under load).
- **No exceptions across asyncio task boundaries.** Wrap task bodies; explicitly publish failure events; don't let `asyncio.create_task` swallow exceptions silently.
- **No exceptions across process boundaries.** Always `await` the future or attach a callback. Dropped futures lose exceptions.

## Failure handling is a domain decision

- State the failure policy explicitly: "On X, mark Y as failed and stop; do not attempt recovery."
- Catch broadly at the message-translator boundary (parse-or-don't is binary).
- Compose the failure handling: a separate listener handles cleanup, not the SUT.
- Logging-as-feature: domain notification interface (`failure_reporter.cannot_translate(message, exception)`), not raw `logger.error`.

## Antipatterns (refuse on sight)

- Edge-to-edge tests claiming end-to-end.
- Skipping the walking skeleton.
- Starting with failure cases.
- Method-shaped test names.
- Mocking concrete classes / values / third-party types.
- Bloated constructor with five dependencies.
- 30+ lines of test setup.
- Asserting on internal state.
- All-expectations mock setup.
- Order-dependent expectations across many mocks.
- Logging mixed with domain logic.
- Hidden time / environment / concurrency.
- Flickering tests tolerated.
- `await asyncio.sleep(N)` for test synchronization.
- Roll-back-the-test-transaction isolation.
- `*Repository` / `*DAO` for every entity.
- Object Mother for test data.

## Self-audit (binary; partial credit does not exist)

1. Test was watched failing before code was written; diagnostic message is informative.
2. Test name describes a behavior, not a method.
3. Test reads in domain language.
4. Acceptance tests drive the real entry point and assert on user-visible state.
5. Test data uses builders, not Object Mother.
6. Mocks are peers, not internals or values or third-party types.
7. Queries are allowances; commands are expectations.
8. No hidden time / env / concurrency dependencies.
9. Async tests listen for events or sample with timeout. No `await asyncio.sleep(N)`.
10. Failure policy is explicit; coarse catch only at translator boundaries.
11. No flickering. Test runs ten times in a row without intermittent failure.
12. Diagnostic message is in the project's domain vocabulary, not generic.

A change failing any item is not finished, no matter how green its tests are.
