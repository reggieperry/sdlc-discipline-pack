# Growing software, guided by tests — guide

A principal-engineer reference for using tests to drive both code correctness and design quality in Elder. The thesis is Freeman and Pryce's: tests are not merely a verification step — they are the design oracle. Test pain is design feedback; pain ignored is design that won't survive contact with future requirements. The spine of this guide is *Growing Object-Oriented Software, Guided by Tests* (Freeman & Pryce, Addison-Wesley, 2009), with citations by chapter and page where the position depends on the primary text. Where Liskov's modularity guide and the DDD guide already cover a point, this guide cites them rather than restating.

A vocabulary note before anything else. **TDD** in GOOS is not "write a unit test before each method." It is a layered feedback discipline: an outer cycle of failing acceptance tests measures progress against features; an inner cycle of failing unit tests guides design moment by moment. Acceptance tests are end-to-end where possible; unit tests describe the *interactions* between an object and its peers, not the methods of an object in isolation. Mocks are a design tool for discovering those interactions, not a convenience for stubbing dependencies. This is sometimes called the **London school** of TDD, distinct from the Detroit/classical school where mocks are minimized.

## 1. Foundations

### 1.1 Why GOOS for Elder

Elder's CLAUDE.md and the slash-command vocabulary already commit to TDD discipline:

- Behaviors-section discipline in `/feature`, `/bug`, and `/chore` plans.
- Cycles paired 1:1 with Behaviors (Red → Green pattern).
- Coverage-check rule for code-touching chores.
- A binding-tests catalog (`docs/elder-invariants.md`) where each invariant has a test name, public surface, and Source line range.

What's missing — and what GOOS supplies — is the *test-pain → design-feedback* pattern as a deliberate, named practice. The slash commands enforce TDD shape; they don't yet name the reading of test pain as a design signal. Several recent decisions on the project (the deferral of `/suggest_tests` per option 3, the recognition that the pipeline-engine refactor will involve a "breakthrough" rather than incremental moves) are GOOS-shaped without GOOS-grounded vocabulary. This guide closes that gap.

The leverage is concentrated in Phase B/C of `docs/concurrent-elder-execution-plan.md` and in build items #1 (pipeline refactor), #2 (cost ledger), #10 (risk-gate sequence), #12 (execution agent with IB), and #15 (vector store). Each is a place where the *test-design* decisions and the *production-design* decisions are not separable.

### 1.2 The thesis

Freeman and Pryce open with three claims that everything else builds on (chapter 1, pp. 3–11):

1. **Software development is a learning process.** Almost every project attempts something new — to the team, to the domain, or to the technology. Plans built on certainty fail; plans built on iterated feedback don't.
2. **Feedback is the fundamental tool.** Each cycle of work — from seconds (a single test) to months (a release) — exposes the team's output to empirical reality. Nested feedback loops reinforce each other.
3. **TDD turns testing into a design activity** (p. 5). Writing tests before code clarifies acceptance criteria, exposes coupling, drives the code toward modularity. Reading test pain reveals design problems that would otherwise stay hidden until requirements forced a rewrite.

> "We've found that the qualities that make an object easy to test also make our code responsive to change." (chapter 20, p. 229)

This is the operative claim. Tests aren't a tax on development; they are the cheapest design feedback the team can buy. A change that's hard to test will be hard to make safely later — the test pain is the early warning.

### 1.3 The Golden Rule of TDD

> **Never write new functionality without a failing test.** (chapter 1, p. 6)

Two consequences:

- **Outer loop, end-to-end.** Each new feature begins with an acceptance test that exercises the system as a user would. While the test is failing, the system does not yet have the feature; when it passes, the work is done.
- **Inner loop, unit.** Inside the failing acceptance test, smaller TDD cycles guide each object's implementation: write a failing unit test, write the simplest code that makes it pass, refactor.

The two loops are not optional. Elder's slash commands enforce the inner loop (Behaviors paired with Cycles). The outer loop is less formalized today; it's the role the iso-chain pilot tests are growing toward.

> "The outer test loop is a measure of demonstrable progress, and the growing suite of tests protects us against regression failures when we change the system." (p. 7)

### 1.4 End-to-end, not edge-to-edge

> "We prefer to have the end-to-end tests exercise both the system and the process by which it's built and deployed." (chapter 1, p. 9)

End-to-end does not mean "calls into the public API." It means: built, packaged, deployed (to a production-like environment), and tested through the same external entry point users would use. Anything less is *edge-to-edge*, and Freeman and Pryce tell a horror story (p. 8) of a project where every acceptance test passed because they instantiated the internal objects directly — and the entry point contained `// TODO implement this`.

For Elder, end-to-end means:

- **The pipeline-engine refactor (item #1):** acceptance tests should drive `main.py` through one ticker end-to-end (real PostgreSQL test DB, real Anthropic API or carefully chosen HTTP-layer mock, faked IB), asserting on the diary entry / approved-trade list.
- **The iso-chain ADW pilots:** the chain itself is exercised against real worktrees and real `gh` operations; the pilots have already validated this for three runs.
- **Live integration risks remain until they're tested.** Freeman and Pryce explicitly note (p. 31) that the auction-sniper example is weak because tests run against a fake, not the real auction site. For Elder this means: tests against a fake IB are not the same as tests against IB paper trading; the gap is real risk and should be documented.

### 1.5 Levels of testing

Three levels (chapter 1, pp. 9–11):

| Level | Question answered | Elder example |
| ----- | ----------------- | ------------- |
| **Acceptance** | Does the whole system work? | Pipeline-end-to-end: ticker → analysis → risk → execution |
| **Integration** | Does our code work against code we can't change? | `LessonRepository` against real ChromaDB; LLM client against real Anthropic API |
| **Unit** | Do our objects do the right thing, are they convenient to work with? | `account.evaluate_proposal(...)` returns expected `RiskDecision` |

Critical asymmetries:

- **Unit tests run fast, in memory, in seconds.** They run on every save.
- **Integration tests run against real third-party code.** They are slower; they catch configuration and contract mismatches that unit tests can't.
- **Acceptance tests run end-to-end through the deployed system.** They are slowest; they catch system-level failures that no smaller test would.

Most defects can be caught by units; the ones that escape are typically integration or system-level. Freeman and Pryce's discipline: many small fast tests, fewer integration tests, even fewer end-to-end tests — pyramid by count, not by importance.

## 2. Vocabulary

- **Acceptance test** — end-to-end test of one feature, written in the domain's language. Outer-loop driver.
- **Integration test** — test of code-we-own against code-we-can't-change.
- **Unit test** — test of one object (and its peers, possibly mocked) in isolation.
- **Walking skeleton** — the thinnest possible end-to-end slice of the system that can be automatically built, deployed, and tested. Establishes the architecture and the build pipeline with minimal feature content.
- **Listening to the tests** — reading test pain as design feedback; refactoring the production code in response, not just rewriting the test.
- **Mockery** — the test fixture that creates mocks, sets expectations, and verifies they were satisfied.
- **Expectation** — a "must happen" claim about how the SUT will call its peers.
- **Allowance / stub** — a "may happen" claim; supporting infrastructure that doesn't itself fail the test.
- **Test data builder** — a chainable helper with safe defaults for constructing complex test objects; replaces *Object Mother*.
- **Tracer object** — a marker object used purely to verify routing through a graph of collaborators.
- **Sampling vs. listening** — the two strategies for synchronizing an asynchronous test with the system: poll observable state, or wait for an emitted event.

## 3. The walking skeleton (chapter 4, chapter 10)

> "A 'walking skeleton' is an implementation of the thinnest possible slice of real functionality that we can automatically build, deploy, and test end-to-end." (chapter 4, p. 32)

> "We can cut through this 'first-feature paradox' by splitting it into two smaller problems. First, work out how to build, deploy, and test a 'walking skeleton,' then use that infrastructure to write the acceptance tests for the first meaningful feature." (p. 32)

The walking skeleton is *not* a stub; it's a thinnest-possible *real* feature, deployed through the same automation that will deploy every subsequent feature. Its content is uninteresting on purpose — the work is in the deployment pipeline.

### 3.1 What a walking skeleton contains

From the auction-sniper example (chapter 10, lines 4358–4626):

- All architectural layers exercised: UI, business logic, external communication.
- A real `main()` entry point invoked by the test.
- A fake external dependency, controllable from the test (FakeAuctionServer).
- Real message broker running locally — no in-memory shortcut for the transport that the production system depends on.
- An `ApplicationRunner` that hides multi-thread coordination from the test.
- An automated build/deploy/test in one command.
- Source control before any code is written.

### 3.2 What a walking skeleton intentionally omits

- Domain logic beyond the absolute minimum to verify the wiring (the auction-sniper skeleton joins an auction and loses without bidding — no bid logic).
- Polish: minimal UI, `print(traceback.format_exc())` placeholders for error handling.
- All complexity of the eventual feature set — multi-item, retry, persistence, capability gates.

### 3.3 Elder's walking skeletons

Elder has *two* walking-skeleton-shaped systems, at different layers:

**The Elder pipeline** has not yet had its walking skeleton built. Build item #1 (pipeline refactor) is essentially the walking-skeleton exercise: a real `main.py` that runs one ticker through the new architecture (ScanCompleted → AnalysisCompleted → RiskChecked → diary entry written), with a real PostgreSQL test DB, a real Anthropic API path (or HTTP-layer mock), and a faked IB. Acceptance test asserts on the persisted diary record. Everything else — multi-ticker, real IB orders, capability gates, full QARP — is later slices.

**The ADW chain** has had its walking skeleton built and validated. The three Phase B sub-step 4 iso-chain pilots (06b77b54, 5ef96495, 0be350bf) are walking-skeleton-shaped: a real PR through the real GitHub flow, exercising plan → build → test → review → PR with the full chain, on a deliberately small chore. The deferral of complex multi-issue scaling, sensitive-file gating, and concurrent-execution scheduling — all "later slices" in the GOOS sense.

### 3.4 The first-feature paradox costs effort, but pays back

> "For most projects, developing the walking skeleton takes a surprising amount of effort. First, because deciding what to do will flush out all sorts of questions about the application and its place in the world. Second, because the automation of building, packaging, and deploying into a production-like environment (once we know what that means) will flush out all sorts of technical and organizational questions." (chapter 4, p. 31)

Specifically for Elder's pipeline refactor: the skeleton work will surface questions about test-DB management, fake-IB shape, Anthropic test-mode policy, fixture lifecycle. Better to surface those before writing twenty agents.

> "Expose Uncertainty Early. … incremental development can be disconcerting … because it front-loads the stress in a project. Projects with late integration start calmly but generally turn difficult towards the end as the team tries to pull the system together for the first time." (p. 36)

This is also the right framing for build items #2 (cost ledger) and #10 (risk-gate sequence): the temptation is to defer the integration with the rest of the pipeline until "the components are ready." That ordering is the late-integration trap.

## 4. The TDD cycle (chapters 4 and 5)

### 4.1 Start each feature with an acceptance test

> "We write the acceptance test using only terminology from the application's domain, not from the underlying technologies (such as databases or web servers)." (chapter 5, p. 39)

For Elder, this means acceptance tests speak in Elder's vocabulary: "Triple Screen passes," "Impulse permits long," "2% Rule satisfied," "trade approved," "diary lesson recorded." Not "PostgreSQL row inserted into `trade_decisions` with `status='approved'`." The latter ties the test to today's storage; the former ties it to the domain.

### 4.2 Separate progress tests from regression tests

> "Once passing, the acceptance tests now represent completed features and should not fail again. A failure means that there's been a regression." (p. 40)

Two suites:

- **In-progress acceptance tests.** Excluded from the build until the feature is shipped. Their purpose: measure progress on the current feature.
- **Regression acceptance tests.** Included in the build. Always pass after the feature ships.

For Elder, this maps naturally onto build items: each build-plan item gets an acceptance test, written before implementation, lifted into the regression suite when the item ships.

### 4.3 Start with the simplest success case

> "It's tempting to start with degenerate or failure cases because they're often easier. … Degenerate cases don't add much to the value of the system and, more importantly, don't give us enough feedback about the validity of our ideas. Incidentally, we also find that focusing on the failure cases at the beginning of a feature is bad for morale — if we only work on error handling it feels like we're not achieving anything." (chapter 5, p. 41)

Failure handling is not optional, but it isn't the place to start. Start with one good path through the feature; record failure cases as you discover them; come back to handle them. Keep a notepad or index card for "things I noticed but am deferring."

### 4.4 Write the test you'd want to read

> "We want each test to be as clear as possible an expression of the behavior to be performed by the system or object. While writing the test, we ignore the fact that the test won't run, or even compile, and just concentrate on its text; we act as if the supporting code to let us run the test already exists." (chapter 5, p. 42)

> "When the test reads well, we then build up the infrastructure to support the test."

This is the **programming-by-wishful-thinking** pattern (chapter 10, p. 33): write the test as though the helpers already exist, *then* build the helpers. The discipline forces the test to be written from the caller's perspective — what would I *want* to say to exercise this behavior? — not the implementer's.

For Elder this lands hard on aggregate-related tests:

```python
# What you'd want to read:
def test_2pct_rule_rejects_oversized_proposal():
    account = an_account().with_equity(Decimal("10000")).build()
    proposal = a_proposal().for_ticker("AAPL").with_risk(Decimal("250")).build()
    decision = account.evaluate_proposal(proposal)
    assert decision.is_rejected
    assert "2%" in decision.reason
```

Builders, named factory methods, intention-revealing assertions. The mechanism (test-data builders, see §6) supports the readability.

### 4.5 Watch the test fail

> "We always watch the test fail before writing the code to make it pass, and check the diagnostic message. … If the failure description isn't clear, someone (probably us) will have to struggle when the code breaks in a few weeks' time. We adjust the test code and rerun the tests until the error messages guide us to the problem with the code." (chapter 5, p. 42)

Three things to verify when watching the failure:

1. The test fails *for the reason expected*. If it fails for a different reason, you misunderstood something — don't make it pass yet; fix the misunderstanding first.
2. The diagnostic message is informative. A failed assertion that just says `assert 0 == 1` will not help future-you. Improve the message.
3. The diagnostic is in domain language. `"AAPL proposal risk $250 exceeds 2% Rule cap of $200"` over `"value mismatch"`.

For Elder, RISK-001 through RISK-012 binding tests should all have informative failure messages. Many do not yet — opportunity for incremental improvement when the tests are touched.

### 4.6 Develop from inputs to outputs

> "We start developing a feature by considering the events coming into the system that will trigger the new behavior. … In this way, we work our way through the system: from the objects that receive external events, through the intermediate layers, to the central domain model, and then on to other boundary objects that generate an externally visible response. … It's tempting to start by unit-testing new domain model objects and then trying to hook them into the rest of the application. … but we're more likely to get bitten by integration problems later." (chapter 5, p. 43)

The trap: "the model is so clear, let me just build it and connect it later." Rebuilds happen because the model that felt clean in isolation didn't fit the actual flow of events. Start from the entry point.

For Elder's pipeline refactor: start from `main.py`'s entry, drive the first event in, follow the path to the first observable output. Build the pieces that flow demands; resist building pieces the flow doesn't yet need.

### 4.7 Unit-test behavior, not methods

> "A test called `testBidAccepted()` tells us what it does, but not what it's for. We do better when we focus on the features that the object under test should provide, each of which may require collaboration with its neighbors and calling more than one of its methods." (chapter 5, p. 43)

Test names describe behavior, not API surface. `testBidAccepted` is API-shaped; `notifiesBidWonWhenPriceMeetsBid` is behavior-shaped. The latter survives method renames; the former rots.

For Elder, the binding tests in `tests/unit/test_risk_agent.py` already follow this discipline. The chore-coverage rule should preserve it: when a chore touches a method named in a test, the chore plan's Behaviors section should describe the *behavior*, not the method.

## 5. Object-oriented style for testability (chapters 6, 7)

The patterns in chapters 6 and 7 are why GOOS-style TDD produces designs that resist rot. They translate cleanly to Python; the Java syntax is incidental.

### 5.1 Object peer stereotypes (chapter 6, pp. 52–54)

A peer of an object falls into one of three categories:

- **Dependency.** A required service, without which the object cannot do its job. *Inject through the constructor.* No null defaults; nothing optional.
- **Notification.** A listener the object tells about state changes; the object expects nothing back. *Default to a no-op listener; allow late binding through a setter or factory method.*
- **Adjustment.** A policy or strategy peer (e.g., `RetryPolicy`, `SpreadEstimator`). *Default to a sensible value; allow override.*

Recognizing the category guides constructor design. A bloated constructor (chapter 20, p. 239) is often a constructor that doesn't distinguish dependencies from adjustments.

For Elder:

- `LLMClient`, `Repository`, `EventBus` — dependencies. Constructor parameters.
- `cost_ledger`, `decision_audit_writer` — notifications. Default to no-op writers; production wires real ones.
- `retry_policy`, `apgar_threshold` — adjustments. Default to documented sensible values; tests override.

### 5.2 Composite simpler than the sum of its parts (chapter 6, pp. 75–80)

> "A composite's API must be no more complicated than any component's. A `moneyEditor.setValue(money)`, not `moneyEditor.setAmountField(...).setCurrencyField(...)`."

A composite exists to *simplify* the API for its callers, not to expose all of its parts. If the composite's interface is wider than the union of its components', you're not composing — you're re-exposing.

For Elder, `AccountState` aggregates `Position`s, `Equity`, monthly P&L. Its public surface should be narrower than the union of those — `evaluate_proposal`, `record_fill`, `current_open_risk`. Not getters for every internal piece.

### 5.3 Context independence (chapter 6, pp. 54–55)

> "An object should know nothing about the system it executes in; everything external is passed in (permanent via constructor, transient via method arg)."

Sister rule: **One Domain Vocabulary.** A class using terms from multiple domains is probably violating context independence. (Exception: bridging-layer classes like adapters whose purpose is translation across domains.)

For Elder this maps to the layered architecture: an `AccountState` should not know about `httpx.AsyncClient`, `ChromaDB`, or `ib_async.Order`. Test-pain corollary: if a test for `AccountState` requires setting up an HTTP client, the design has slipped.

### 5.4 Tell, don't ask (chapter 2, pp. 17–18)

This is in CLAUDE.md as Elder's design philosophy. GOOS provides the operationalization:

> "When we don't follow the style, we can end up with what's known as 'train wreck' code. … `master.allowSavingOfCustomisations()` … wraps all that implementation detail up behind a single call." (chapter 2, p. 17)

For Elder, the canonical example is `account.evaluate_proposal(proposal)` over `if account.equity * 0.02 > proposal.risk: ...`. The first tells the account to do its job; the second pulls data out and does the account's job for it.

> "Adding a query method moves the behavior to the most appropriate object, gives it an explanatory name, and makes it easier to test." (p. 18)

Test pain that says "I keep needing to set up `account.equity`, `account.month_start_equity`, `account.open_positions` to test this" is a Tell-Don't-Ask violation in the calling code. Push the logic into `account`.

### 5.5 Three discoveries: breaking out, budding off, bundling up (chapter 7, pp. 60–62)

When an object grows uncomfortable to test:

- **Breaking out.** Extract a coherent unit of behavior into its own class. Test it independently.
- **Budding off.** Pull a new collaborator interface into existence from the client's needs. The interface didn't exist before; the test pressure reveals it should.
- **Bundling up.** Package related collaborators behind a containing object that simplifies their joint use.

These are the same three moves as Evans's "Make Implicit Concepts Explicit" (DDD chapter 9), described from the test-design side.

### 5.6 Mocking peers, not internals (chapter 7, pp. 60–61)

> "Mocks should target objects the SUT *talks to*, not pieces of it."

Mocking concrete subclasses of the SUT (chapter 20, pp. 235–237) is rejected on sight: it leaves the relationship between collaborators implicit, makes the test brittle to refactors, and prevents the test from discovering a missing role. The exception ("break glass in case of emergency"): legacy code under test, third-party APIs that genuinely have no exit ramp.

For Elder, the rule lands directly on agents: don't mock `RiskAgent` itself in a test of `TradeManager`. Mock `AccountRepository`, the things `RiskAgent` collaborates with — or use a real risk agent.

### 5.7 Listen to the tests (chapter 5, p. 44; chapter 20)

The most operationally important pattern in the book.

> "Our experience is that, when code is difficult to test, the most likely cause is that our design needs improving. The same structure that makes the code difficult to test now will make it difficult to change in the future." (chapter 5, p. 44)

> "When we find a feature that's difficult to test, we don't just ask ourselves how to test it, but also why is it difficult to test." (chapter 1, p. 11)

Specific test-pain → design-feedback patterns from chapter 20 (the highest-leverage chapter in the book):

| Test pain | Design problem | Refactor |
| --------- | -------------- | -------- |
| "I need to mock `datetime.now()` / `os.environ` / a global" | Hidden dependency on time/environment | Inject a `Clock` or read env at module load (Liskov §IV problem 1) |
| "Setting up the test takes 50 lines" | Object has too many dependencies, or the dependencies aren't really needed | Bloated constructor — push some dependencies down into a smaller object |
| "I need to mock 5 collaborators" | Test boundary is too coarse, or the SUT is doing too many things | Break out functionality into smaller objects with narrower test boundaries |
| "I'm asserting on internal state" | Object isn't communicating its state through events; encapsulation is leaking | Add events or query methods that describe the state in domain terms |
| "I need to assert order across many mocks" | The test object is coordinating too much, or the order is incidental | Either coordinate less (move logic to receivers) or use a state machine pattern, not arbitrary sequence constraints |
| "Mocking the third-party library hurts" | You're mocking something you don't own | Wrap the library in your own interface; mock the interface |
| "I want to test exact log lines" | Logging is mixed with domain logic | Separate `support` notification (domain interface) from diagnostic logging |
| "I keep adding setters to the SUT for testing" | You're using setters where constructor-injection should be | Inject dependencies through the constructor; remove the setters |

For Elder, the most relevant patterns are:

1. **Hidden time dependency.** `datetime.now()` calls scattered through indicator math, agents. Inject a `Clock` or pass timestamps explicitly. (Liskov §IV problem 1 says the same thing from a different angle.)
2. **Hidden environment dependency.** Reading `os.environ` mid-call. Read once at module load via the ADW config rule.
3. **Setup-heavy tests.** RISK-001 through RISK-012 already use shared `_make_risk_agent()` and `_baseline_proposal()` builders. As the catalog grows, the test-data-builder pattern (§6) should be extended.
4. **Asserting on internal state.** When the pipeline refactor lands, agent tests should assert on emitted events, not on `state.risk_decisions[ticker]`. CLAUDE.md already commits to this; GOOS provides the rationale.

### 5.8 Logging is a feature (chapter 20, pp. 233–234)

> "Support logging (errors and info) is part of the user interface of the application … Diagnostic logging (debug and trace) is infrastructure for programmers."

Two different concerns share an implementation; they should be split.

> "Maybe we could do this instead: `support.notifyFiltering(tracker, location, filter);` where the `support` object might be implemented by a logger, a message bus, pop-up windows, or whatever's appropriate."

For Elder, this lands directly on the cost-ledger and decision-audit work (build item #2):

- The application code emits `cost_ledger.charge_llm_cost(ticker, request_id, usage)` — domain interface, not `logger.info("LLM call cost $0.024 for AAPL")`.
- The application code emits `decision_audit.record(approved_trade, gate_decisions)` — domain interface, not `logger.info("Trade approved")`.
- Diagnostic logging (debug-level inspection of intermediate values) is separate; can use Python's `logging` module directly.

The test pressure is the design feedback: tests should be able to verify "the pipeline charged the LLM cost for this ticker" without parsing log strings.

## 6. Building tests that don't rot (chapters 21, 22, 23, 24)

### 6.1 Test names describe features (chapter 21)

> "Test names describe features. The class is the implicit subject; method name is a sentence." (chapter 21, p. 248)

Pattern: `holdsItemsInTheOrderTheyWereAdded`, not `test1` or `add`. For Elder's pytest functions: `test_2pct_rule_rejects_oversized_proposal`, not `test_evaluate_proposal_invalid`.

A name that doesn't fit on one line is a test that does too much; split it.

### 6.2 Canonical test structure (chapter 21)

Setup → Execute → Verify → Teardown. Skim-readable. Variation across tests in the same suite is itself a smell — the reader expects to find the action and the assertion in predictable places.

For mock-based tests:

```text
# Setup (build SUT and peers)
# Expectations declared
# Execute (the one action under test)
# Verify (mocks check; assertions assert)
# Teardown (usually automatic)
```

### 6.3 Test data builders (chapter 22)

> "A class with one field per constructor arg, each initialized to a safe default; chainable `withX()` methods; `build()` at the end. Static factory `anOrder()` for readability." (chapter 22, pp. 257–272)

In Python, this maps to:

```python
@dataclass
class _ProposalBuilder:
    ticker: str = "AAPL"
    risk_amount: Decimal = Decimal("100")
    direction: Direction = Direction.LONG
    entry_price: Decimal = Decimal("150")

    def for_ticker(self, ticker: str) -> "_ProposalBuilder":
        return replace(self, ticker=ticker)

    def with_risk(self, amount: Decimal) -> "_ProposalBuilder":
        return replace(self, risk_amount=amount)

    def build(self) -> TradeProposal:
        return TradeProposal(
            ticker=self.ticker,
            risk_amount=self.risk_amount,
            direction=self.direction,
            entry_price=self.entry_price,
        )

def a_proposal() -> _ProposalBuilder:
    return _ProposalBuilder()
```

Tests specify only the relevant attributes:

```python
proposal = a_proposal().for_ticker("AAPL").with_risk(Decimal("250")).build()
```

**Builder over Object Mother** (p. 258): Object Mother (`make_apple_proposal()`, `make_oversized_proposal()`) doesn't cope with variation — every minor difference becomes a new factory method. Builders compose.

**Pass builders, not products, through helpers** (pp. 13454–13601): the helper takes a builder, fills in defaults, then builds. This makes the test read declaratively: `havingReceived(an_order().for_customer("Alice").of_value(Decimal("100")))`.

For Elder, builders are warranted for: `ScanResult`, `TradeProposal`, `ApprovedTrade`, `RejectedTrade`, `RiskDecision`, `AccountState`. Probably worth a small `tests/builders.py` module once item #1 lands.

### 6.4 Test diagnostics (chapter 23)

> "Design tests to fail. The point of a test is to fail informatively." (chapter 23, p. 267)

Patterns:

- **Explanatory assertion messages.** `assert decision.is_rejected, f"expected rejection for {proposal.ticker} above 2% cap"` over bare `assert decision.is_rejected`.
- **Self-describing values.** `account_id = "a-customer-account-id"` over `account_id = "573242"`. Failures show the role.
- **Obviously canned constants.** `INVALID_ID = 666` so leaked test values stand out in production logs.
- **Tracer objects.** A marker object used purely for routing checks: "did the right collaborator receive this event?" In Python, often a class instance with `__eq__ = lambda *a: NotImplemented` so any comparison fails noisily.

### 6.5 Test flexibility (chapter 24)

> "Specify Precisely What Should Happen and No More." (chapter 24, p. 273)

The whole chapter compresses to one rule: don't over-specify. Test brittleness is feedback about test quality *and* design quality.

Specific patterns:

- **Test for information, not representation.** Assert "the result is the absent customer" via `decision.customer == NO_CUSTOMER_FOUND`, not `decision.customer is None`. When the representation changes (say, from `None` to `Maybe[Customer]`), one constant moves; tests stay valid.
- **Precise assertions.** `assert "2%" in decision.reason`, not `assert decision.reason == "Risk amount of $250 exceeds 2% rule cap of $200 for account DU1234567"`. The latter locks the test to formatting.
- **Allow queries; expect commands** (pp. 277–280). Queries (no observable side effect) — use `allowing()` / stub. Commands (change the world) — use `expecting()` / mock. The distinction makes tests readable: expectations are the assertions; allowances are the supporting infrastructure.

For Elder this maps directly to mock discipline in pytest:

```python
# Allow query; expect command
mock_account_repo.find_by_id.return_value = an_account().build()  # allowance
trade_manager.process(proposal)
mock_executor.submit.assert_called_once_with(approved_trade)  # expectation
```

## 7. Integration and persistence (chapters 8, 25)

### 7.1 Only mock types that you own (chapter 8)

> "When we mock something we don't own, we cannot act on the design feedback the test gives us; the feedback loop is broken. We also lose the protection of testing against the real library, because mocks can drift from real behavior across upgrades." (chapter 8, pp. 69–70)

The pattern: write your own interface in your domain's terms, implement it with a thin adapter, integration-test the adapter against the real third-party code. Many unit tests against your interface (fast, in memory). Few integration tests against the adapter (slower, exercises real library).

For Elder this is already the operating shape:

- `LessonRepository` (Elder's interface) — `ChromaLessonRepository` (adapter) — integration tests against real ChromaDB.
- `LLMClient` (Elder's interface) — `AnthropicClient` (adapter) — integration tests against real Anthropic API (or careful HTTP-layer mock).
- IB adapter (planned, item #12) — `IBExecutor` (Elder's interface) — `IBAsyncExecutor` (adapter) — integration tests against IB paper trading.

The exception (p. 70): mocking third-party types is acceptable for paths that are hard to trigger otherwise — exceptions, transaction rollbacks, network failures. Few such tests in a suite.

### 7.2 Don't name types after patterns (chapter 25)

> "Don't name the type `*Repository` or `*DAO`. Name it after its domain role: `CustomerBase`, not `CustomerRepository`. Reasons: pattern names leak technical-domain into application-domain; clients don't care about the pattern; pattern names age badly when implementation changes; generic words add no information." (chapter 25, pp. 295–298)

For Elder, the planned items #2 and #14 use the names `AccountRepository`, `TradeRepository`, `RunRepository`. Worth re-checking whether `AccountStore`, `TradeJournal`, `RunLog` (or similar) would read better. The repository *role* is incidental; what the thing *is* in the domain is primary.

Counter-argument: the DDD guide §4.7 uses `Repository` as the canonical pattern name, and the broader project speaks DDD vocabulary. Naming consistency across the project may outweigh the GOOS-pure form. This is a judgment call to make at item #2 design time, not a fixed rule.

### 7.3 Make transaction boundaries explicit (chapter 25, pp. 290–293)

> "Extract a `Transactor` (or `JPATransactor`/`JMSTransactor`) that runs a `UnitOfWork` inside a real begin/commit/rollback. Don't use the 'wrap each test in a transaction and roll it back' pattern — it doesn't exercise commit, the most behavior-rich event."

For Elder's PostgreSQL work (items #2, #14): integration tests should commit and roll back through a real `Transactor`-shaped abstraction. Don't simulate transaction boundaries with savepoints; exercise the real ones.

### 7.4 Clean up at the start, not the end (chapter 25)

> "Leftover data from a failing test helps diagnostics. Subsequent tests clean up before they run, so isolation is preserved either way." (chapter 25, p. 290)

Counter to common pytest idiom (which is teardown-driven). The argument: if a test fails halfway through and the teardown wipes the DB, the post-mortem investigator has nothing to look at. Cleanup-on-entry preserves the failure scene.

For Elder's PostgreSQL test fixtures, this is worth considering as the test-DB workflow grows. Today's `db/setup.sh --test` creates an empty DB; per-test cleanup pattern needs design when the pipeline tests start writing real records.

## 8. Concurrency and async (chapters 26, 27)

Elder is async-heavy. The single asyncio event loop carries every IB connection, every LLM call, every event-bus subscriber. ProcessPoolExecutor crosses process boundaries for indicator computation. These are exactly the cases where GOOS chapter 26 and chapter 27 are operationally consequential.

### 8.1 Separate functionality from concurrency policy (chapter 26)

> "Concurrency is a system-wide concern that should be controlled outside the objects that need to run concurrent tasks." (chapter 26, p. 305)

Pattern: the object that needs concurrent tasks accepts an `Executor` (or asyncio equivalent — an event loop, a `TaskGroup`, a scheduler) as a constructor parameter. Tests pass a `DeterministicExecutor` that runs tasks synchronously on the test thread.

For Elder, this design move applies directly to:

- **Pipeline managers (item #1).** A `ScanManager` or `TradeManager` should not own its asyncio tasks — it should accept a scheduler/runner. Tests can use a synchronous runner; production uses the real loop.
- **ProcessPoolExecutor for indicators.** The compute pool is itself injectable. Tests use an in-process `MockComputePool` that runs the worker function synchronously.

> "If concurrency is hidden behind the object's API, you can't test functionality and synchronization separately, so failures are double-faulted and hard to diagnose." (p. 305)

Test pain that says "I had to use `asyncio.run()` in a unit test" is concurrency-policy-leaking-into-the-SUT pain.

### 8.2 Two test types per concurrent object (chapter 26, pp. 305–306)

1. **Functional unit test.** Uses `DeterministicExecutor` (synchronous). Verifies the *logic* — what the object does given inputs — without concurrency concerns.
2. **Stress test.** Uses real thread pool / event loop. Verifies that concurrent invocations don't violate the object's invariants. Run many times; tune until failure is reliable on broken code.

> "Watch the stress test fail, then fix, then watch again. The first race fix often reveals a second race underneath." (chapter 26, p. 306)

For Elder, stress-testing is most directly relevant to:

- The pipeline event bus under high event volume (race conditions on subscriber lists).
- IB connection pool under concurrent demand (clientId allocation, idempotent retries).
- Pipeline interrupt handling (the external-interrupt feature in build item #1).

The invariant catalog (`docs/elder-invariants.md`) is the right home for concurrency-shaped invariants. Candidates for future RISK-013+ entries: pipeline interrupt safety, idempotent execution under retry, ProcessPoolExecutor workers receiving stale data.

### 8.3 Sampling vs. listening (chapter 27, pp. 316–317)

Two ways for an asynchronous test to observe the system:

- **Listening.** The test registers as an event listener; blocks waiting for the event of interest; wakes up immediately on receipt. Fast; precise.
- **Sampling.** The test polls observable state with a short delay between samples; loops until either the expected state is observed or a timeout fires. Works against any system; can miss intermediate state changes.

Both must use timeouts for failure detection; both must `succeed fast` (return as soon as the expected state is observed, not after a fixed delay).

> "Beware of Flickering Tests. A test can fail intermittently if its timeout is too close to the time the tested behavior normally takes to run, or if it doesn't synchronize correctly with the system. … Allowing flickering tests is bad for the team. It breaks the culture of quality." (p. 317)

For Elder's pipeline tests, listening is preferred where possible: the typed event bus naturally supports it (subscribe to `RiskChecked` events; assert when one arrives). Sampling fits I/O-driven cases (poll for a row in `trade_decisions`). For PostgreSQL state assertions, sampling at 100ms intervals with 5-second timeout is a reasonable starting policy.

### 8.4 Testing that nothing happens (chapter 27, pp. 325–326)

> "Asynchronous tests look for changes in a system, so to test that something has not changed takes a little ingenuity. … The skill here is in picking a behavior that will not interfere with the test's assertions and that will complete after the tested behavior."

Pattern: drive an unrelated probe event through the system after the action under test; assert that the failed/silent component stays silent and that the probe event is processed as expected. The probe event proves "the system has had time to do whatever it would have done."

For Elder, this is directly relevant to error-handling tests:

```python
# Failed scanner should not propagate to risk gate
await event_bus.publish(MalformedScanResult(...))
# Drive an unrelated event; assert risk gate processed it
await event_bus.publish(SecondTickerCandidate(...))
await wait_for_event(RiskChecked, ticker="MSFT", timeout=5.0)
# If we got here, the malformed event was handled (silently or with error reporting)
# without blocking the rest of the pipeline
```

### 8.5 Externalize event sources (chapter 27, pp. 326–327)

> "Some systems trigger their own events internally. The most common example is using a timer to schedule activities. … Hidden timers are very difficult to work with because they make it hard to tell when the system is in a stable state for a test to make its assertions."

Solution: pull scheduling out into an external service that the system listens to. The test poses as the scheduler and drives the system through its behavior deterministically.

For Elder, the relevant case is the **stop monitor** (build item #13). The stop monitor recomputes SafeZone trailing stops on a schedule. If that schedule lives inside the monitor, end-to-end tests have to wait real time. Externalize: the monitor accepts a `RecomputeRequested` event; production wires a real scheduler that emits the event on its cadence; tests emit the event directly.

Same applies to the **iso-chain reaper** (Phase C of `docs/concurrent-elder-execution-plan.md`): the reaper's stale-heartbeat threshold should be part of the reaper's configuration, but the *firing* of the reaper should be event-driven, not internal-timer-driven, so tests can drive it deterministically.

## 9. Failure handling (chapter 19)

Failure policy is a domain decision, not a technical default. GOOS makes this explicit through the auction-sniper's failure handling:

> "Our policy will be that when we receive a message that we cannot interpret, we will mark that auction as Failed and ignore any further updates, since it means we can no longer be sure what's happening. Once an auction has failed, we make no attempt to recover." (chapter 19, p. 215)

Three operational patterns:

### 9.1 Coarse exception catch at the message-translator boundary

> "We could be precise about which exceptions to catch but in practice it doesn't really matter here: we either parse the message or we don't, so to make the test pass we extract the bulk of `processMessage()` into a `translate()` method and wrap a try/catch block around it." (chapter 19, p. 217)

The argument: at the seam where domain logic meets a serialization/deserialization layer, the binary "did it parse?" matters more than the precise exception type. Catch broadly; report through the domain interface.

For Elder:

- The LLM-response parser (when build item #4 grader work lands). Either the response parses or it doesn't; the failure policy is "mark this analysis as failed, log the structured failure, do not attempt to act on the malformed result."
- The IB-message handler (item #12). Either the message is recognized or it isn't; failure policy is "mark this fill-confirmation as failed, alert the operator, do not let the ib_async exception propagate into the pipeline."

### 9.2 Composition over centralization for failure handling

The auction-sniper failure design (chapter 19, pp. 218–220) explicitly chooses *composition* over a centralized error handler: a separate `ChatDisconnector` listener handles disconnect-on-failure, rather than the translator detaching itself. Each object keeps one responsibility.

For Elder, this is the design grain for the cost ledger and decision audit (build item #2): each is its own listener on the event bus, not a method called by every agent. When an agent emits a `RiskChecked` event, the cost ledger picks it up and charges the cost; the decision audit picks it up and records the audit row. Failures in either listener don't block the other.

### 9.3 No exceptions across framework boundaries

> "The Smack library drops exceptions thrown by `MessageHandlers`, so we have to make sure that our handler catches everything." (chapter 19, p. 217)

Equivalent for Python:

- Exceptions raised inside a coroutine handed to `asyncio.create_task` without an explicit error handler will be silently swallowed if the task is garbage-collected before the exception is awaited. **Don't let exceptions cross asyncio task boundaries silently.** Wrap the task body in a try/except that explicitly publishes a domain failure event.
- Exceptions raised inside a `ProcessPoolExecutor` worker function are pickled and re-raised when `.result()` is called — but only if `.result()` is called. If the future is dropped, the exception is lost. **Always await the future or attach a callback that reports failure.**

## 10. Elder mapping summary

| GOOS pattern | Status in Elder | Notes |
| ------------ | --------------- | ----- |
| Acceptance / integration / unit pyramid | Partial | Unit (120 tests) + property (hypothesis) strong; integration via `db/setup.sh --test`; acceptance not yet present (pipeline runs once item #1 lands) |
| Walking skeleton | Implemented (ADW chain); planned (Elder pipeline) | Phase B sub-step 4 pilots are walking-skeleton-shaped; item #1 is the Elder-pipeline equivalent |
| Outer-loop acceptance test per feature | Standing rule once item #1 lands | Each build-plan item gets one acceptance test, in-progress until ship |
| Start with simplest success case | Standing rule | Already documented in `/feature` and `/bug` slash commands |
| Write the test you'd want to read | Standing rule | Already enforced by Behaviors-section discipline |
| Watch the test fail | Standing rule | Red-fail-then-Green pairing in slash commands |
| Develop from inputs to outputs | Standing rule | Apply on item #1 pipeline refactor |
| Unit-test behavior, not methods | Implemented | Binding tests in `docs/elder-invariants.md` use behavior-shaped names |
| Object peer stereotypes | Standing rule | Apply on items #1, #10 — distinguish dependencies, notifications, adjustments |
| Tell, Don't Ask | Implemented (CLAUDE.md) | `account.evaluate_proposal` is the canonical example |
| Mock peers, not internals | Standing rule | Don't mock concrete agent classes; mock their collaborators |
| Listen to the tests | Standing rule | Test pain → design feedback table in §5.7 |
| Logging is a feature | Standing rule | Cost ledger and decision audit (item #2) are domain notification interfaces |
| Test names describe features | Implemented | RISK-001 through RISK-012 follow this |
| Test data builders | Partial | `_make_risk_agent`, `_baseline_proposal` exist; extend to a `tests/builders.py` |
| Test diagnostics | Partial | Many tests have informative failure messages; opportunity to improve incrementally |
| Test flexibility | Standing rule | Allow queries / expect commands; precise assertions; information not representation |
| Only mock types you own | Implemented | `LessonRepository` interface + adapter; same for LLM client |
| Don't name types after patterns | Open question | Items #2, #14 — re-check `*Repository` naming |
| Externalize event sources | Standing rule | Stop monitor (item #13), reaper (Phase C) — no internal timers |
| Two test types per concurrent object | Standing rule | Functional + stress; stress-testing for the event bus, IB pool, interrupt safety |
| Sampling vs. listening | Standing rule | Listening preferred; sampling for I/O state |
| Testing that nothing happens | Standing rule | Drive a probe event after the action under test |
| Coarse catch at translator boundary | Standing rule | LLM-response parser, IB-message handler |
| No exceptions across asyncio / process boundaries | Standing rule | Wrap task bodies; await futures or attach error callbacks |

## 11. Antipatterns

Each entry is a specific failure mode this guide rejects. They are the mirror of §§3–9.

- **Edge-to-edge tests claiming to be end-to-end.** Tests that instantiate internal objects directly, bypass deployment, and assert on internal state. Fix: drive `main.py` (or equivalent entry point) and assert on user-visible state.
- **Skipping the walking skeleton.** Building a feature before the deploy/test pipeline exists. Fix: build the smallest deployable thing first, even if its content is uninteresting.
- **Starting with failure cases.** Because they're easier to write. Fix: simplest success case first; record failure cases for later.
- **Testing methods, not behaviors.** `test_evaluate_proposal_invalid` over `test_2pct_rule_rejects_oversized_proposal`. Fix: name tests after observable behaviors.
- **Mocking concrete classes.** Subclass-and-override of the SUT or its peers. Fix: mock interfaces (or `Protocol`s in Python). Break-glass exception only for legacy or third-party code with no exit ramp.
- **Mocking values.** No point creating a `Mock(spec=Price)`; just construct a real `Price`. Fix: real values; mock only collaborators.
- **Mocking types you don't own.** Mocking `httpx.AsyncClient`, `chromadb.Collection`, `psycopg.Connection`. Fix: write your own interface, mock that.
- **Bloated constructor.** Five-plus dependencies. Fix: extract a helper object, or re-categorize peers (notifications and adjustments default; only dependencies are required).
- **Setup-heavy tests.** 30+ lines before the action under test. Fix: either the SUT does too much (split it), or use test-data builders.
- **Asserting internal state.** `assert account._open_positions[0].risk == ...`. Fix: assert through events or query methods that describe state in domain terms.
- **Too many expectations.** Every method call mocked as a strict expectation. Fix: distinguish queries (allowances) from commands (expectations); only the commands are the real assertions.
- **Order-dependent mock expectations across many objects.** Fragile; locks the test to incidental ordering. Fix: split the test, use a state machine if the order is meaningful, or relax the constraint.
- **Logging mixed with domain logic.** `logger.info("Trade approved for ...")` inside `evaluate_proposal`. Fix: domain notification interface (`decision_audit.record(...)`); logger is one implementation.
- **Hidden time / environment dependencies.** `datetime.now()`, `os.environ.get(...)` mid-call. Fix: inject `Clock`; read env at module load.
- **Hidden concurrency.** `asyncio.create_task(...)` inside a method that pretends to be synchronous. Fix: pass a scheduler; let the caller decide when work runs.
- **Flickering tests.** Intermittent failures tolerated. Fix: investigate every flicker as a real signal — either a synchronization bug in the test, or a real race in production.
- **Sleep-based test synchronization.** `time.sleep(2)` to "wait for the system to catch up." Fix: poll for observable state with timeout, or listen for events.
- **Roll-back-the-test-transaction isolation.** Wrapping every test in a transaction and rolling it back. Fix: real begin/commit/rollback through a `Transactor`-shaped abstraction; cleanup at the start of each test.
- **Repository per class.** A repository for every entity, including aggregate members. Fix: one repository per aggregate root (DDD §4.7).
- **Object Mother for test data.** `make_apple_proposal()`, `make_oversized_proposal()`. Fix: builders.

## 12. Self-audit checklist

Run this against any TDD-driven change before declaring it done. Each item is binary; partial credit does not exist.

1. **Failing test exists before code.** The Red step was actually run. Diagnostic message is informative.
2. **Test name describes a behavior**, not a method or API surface.
3. **Test reads in domain language.** A domain expert could read it and understand what's being verified.
4. **Test is end-to-end-shaped at acceptance level**: drives the system through its real entry point, asserts on user-visible state. (Acceptance tests only.)
5. **Builders, not Object Mother.** Test data is constructed with `a_proposal().with_risk(...).build()` shape.
6. **Mocks are peers, not internals.** No subclassing of the SUT; no mocking of values.
7. **No mocking of third-party types.** `httpx`, `chromadb`, `psycopg`, `ib_async` types are wrapped behind Elder-domain interfaces; tests mock the interfaces.
8. **Allowances vs. expectations distinguished.** Queries are allowances; commands are expectations. The test reads as "this method must be called" only for the command being asserted.
9. **No hidden time / env / concurrency.** `datetime.now()`, `os.environ.get(...)`, `asyncio.create_task(...)` are dependencies, injected or read at module load.
10. **Async tests use listening or sampling with timeout.** No `await asyncio.sleep(...)` for synchronization; no fixed-delay-then-assert.
11. **Failure policy is explicit.** What happens when this fails? Where is it caught? What is reported and to whom?
12. **No flickering.** The test runs ten times in a row without intermittent failure. (Property tests with `@given` count if the seed is fixed.)
13. **Diagnostic message is in domain language.** A failure tells the reader what went wrong using Elder's vocabulary, not generic "value mismatch."
14. **Test refactoring follows code refactoring.** When the production code's shape changes meaningfully, the test's shape changes with it.

A change failing any item is not finished, no matter how green its tests are.

## 13. What this guide does not cover

- **Java syntax and JUnit/jMock specifics.** GOOS is written in Java; the patterns translate to Python, but the mechanical details (annotations, `@Test`, `Hamcrest` matchers) do not. Where Python has equivalents (`pytest`, `unittest.mock`, `hypothesis`), they are mentioned; otherwise the *principle* is what carries.
- **Acceptance-test framework choice.** GOOS uses WindowLicker, FitNesse, JBehave-style frameworks. Python's nearest equivalents are `behave` (BDD), `pytest-bdd`, or simply pytest with carefully-shaped fixtures. Elder will likely use pytest with high-level fixtures rather than a separate BDD framework; the choice is open until item #1's acceptance tests are written.
- **CI-server choice.** GOOS assumes a CI server runs the full suite on every commit. Elder uses `./scripts/verify.sh` locally and GitHub Actions (where applicable). The principle — "fast feedback from a real build" — is what matters; the choice of tool is incidental.

## Sources

- Freeman, S. and Pryce, N. (2009). *Growing Object-Oriented Software, Guided by Tests.* Addison-Wesley. The spine of this guide; all chapter and page citations refer to this book.
- Cockburn, A. *Crystal Clear: A Human-Powered Methodology for Small Teams.* Origin of the "walking skeleton" term; cited in GOOS chapter 4.
- `docs/modularity-guide.md` — Liskov-grounded modularity discipline; pre-requisite to several patterns here (level-initialization function, hidden dependencies as level-ownership violations).
- `docs/ddd-guide.md` — Evans-grounded domain modeling; complementary to GOOS at the design layer (entities/values/services map naturally to GOOS's peer-stereotype distinctions; aggregates are the unit GOOS unit-tests assert against).
- `docs/elder-invariants.md` — the binding-tests catalog; many of its entries are GOOS-shaped already (RISK-001 through RISK-012 follow "test names describe features," "watch the test fail with clear diagnostics," etc.).
- CLAUDE.md "Design philosophy" — the operating rules that codify several patterns from this guide (Tell, Don't Ask; domain events over shared mutable state; agents return typed output).
- Wirfs-Brock, R. and McKean, A. *Object Design: Roles, Responsibilities, and Collaborations.* Cited by GOOS for the role/responsibility framing.
- Meszaros, G. *xUnit Test Patterns.* Cited by GOOS for the broader test-smells taxonomy; chapter 20 of GOOS is a focused subset.
