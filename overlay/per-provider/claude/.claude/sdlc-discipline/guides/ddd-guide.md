# Domain-driven design guide

A principal-engineer reference for designing and auditing domain models in Elder. The thesis is Evans's: complex software is made tractable by a model the team genuinely shares with domain experts, expressed literally in the code. The spine of this guide is *Domain-Driven Design: Tackling Complexity in the Heart of Software* (Evans, Addison-Wesley, 2003), with citations by chapter and page where the position depends on the primary text. Where Liskov's modularity guide already covers a point (level ownership, hierarchy, connection-as-assumption), this guide cites it rather than restating.

A vocabulary note before anything else. Evans uses **model** in a strong sense: not a diagram, not a schema, but the set of concepts the team carries in their heads, expressed in code, exercised in conversation, and refined over time. The model is the *project's hypothesis about the domain*. The diagram is a sketch of the model; the code is its implementation; the team's spoken language (the **ubiquitous language**) is its working vocabulary. The three must stay aligned, or the model decays into "data with code attached" (Evans p. 47, anti-pattern of the SMART UI).

## 1. Foundations

### 1.1 Why Evans for Elder

Elder's CLAUDE.md already commits to DDD vocabulary: domain events over shared mutable state, bounded contexts, aggregate invariants, repositories per context, agents that return typed output. Build plan items #1–#11 are nearly all DDD-shaped decisions waiting to be made: replacing the `PipelineState` god-object with typed events (#1); designing a cost-ledger aggregate and its repository (#2); enforcing the 2% and 6% Rule as aggregate invariants on `AccountState` (#10); deciding whether the four pipeline stages are one bounded context or four (#11 onward). The vocabulary is in place; the *tradeoff grounding* is what this guide adds — Evans is rigorous about *when patterns don't fit*, and that rigor is hard to derive from summary.

### 1.2 The model as the heart

Evans's premise (chapter 1, pp. 4–14): the team's job on a complex project is **knowledge crunching** — collaborative distillation of a domain into concepts that are both rigorous enough to implement and faithful enough to capture how experts actually reason. The output is a deep model that exposes the domain's hidden structure, not a noun-extraction of the requirements document.

Five ingredients of effective modeling (p. 8):

1. **Bind the model to the implementation.** A model that exists only on paper or only in code, but not in both, has nowhere to live.
2. **Cultivate a language based on the model.** Domain experts and developers must use the same terms with the same meanings.
3. **Develop a knowledge-rich model.** Behavior and rules belong in the model, not just data.
4. **Distill.** Drop concepts that don't earn their keep. Replace ones that don't fit.
5. **Brainstorm and experiment.** The model is found, not specified.

For Elder this lands directly: the design docs (`docs/comprehensive-design.md`, `docs/risk-model-overview.md`, `docs/quality-value-filter-reference.md`) are the model in prose; the typed events and frozen dataclasses are the model in code; the `2% Rule`, `Triple Screen`, `SafeZone`, `Trade Apgar`, `ABC Rating` vocabulary in CLAUDE.md is the ubiquitous language. The job is to keep all three in sync as the system grows.

### 1.3 Model-driven design

> "Software design is a constant battle with complexity. We must make distinctions so that special handling is applied only where necessary." (p. 60)

> "If the design, or some central part of it, does not map to the domain model, that model is of little value, and the correctness of the software is suspect." (p. 31)

Evans rejects the analysis/design split (p. 30): a single model serves both purposes, refined as understanding deepens. **MODEL-DRIVEN DESIGN** (p. 32) is the discipline of designing software so that the code literally maps to the model — class names match domain terms, public methods match domain operations, and the structure of the code reflects the structure of the concepts.

Constraint (p. 33): MODEL-DRIVEN DESIGN requires a modeling paradigm the implementation language can express. Object-oriented and logic-programming paradigms qualify; pure procedural does not. Python with frozen dataclasses, `Protocol` types, and class methods qualifies straightforwardly.

Practical consequence (p. 56): **HANDS-ON MODELERS.** Anyone who designs the model must touch the code; anyone who changes the code must understand the model. There are no ivory-tower architects in DDD because their decisions don't survive contact with the implementation. For Elder, this is already the operating mode — the same person who writes the build plan writes the code.

### 1.4 Two pre-requisites Liskov already covers

DDD assumes two things this guide does not re-derive — they live in `docs/modularity-guide.md`:

- **Level ownership and hierarchy.** Evans's "layered architecture" is one application of Liskov's level rules (each level owns its resources, lower levels do not reference higher). When the modularity guide and this guide both speak of "levels," they mean the same thing.
- **Connection-as-assumption.** Evans's "interface that doesn't reveal the means" (p. 195) is the same discipline as Parnas/Liskov's "the connection is the assumption." The corollary — hide what's likely to change — is identical.

What DDD adds on top is *what to put in those levels*: entities, value objects, services, aggregates. Where Liskov tells you how to build the chassis, Evans tells you what shape the engine should be.

## 2. Vocabulary

- **Domain.** The subject area the software is about. For Elder: trading and risk management per Alexander Elder's methodology, with QARP fundamental filtering and IB-mediated execution.
- **Domain model.** The team's set of concepts about the domain, expressed precisely enough to implement.
- **Ubiquitous language.** A language structured around the domain model and used by everyone — expert and developer — in speech, documents, and code.
- **Bounded context.** The boundary within which a model applies coherently. Outside the boundary, terms may mean something different. Multiple bounded contexts coexist in one system.
- **Layered architecture.** UI / Application / Domain / Infrastructure separation; the domain layer holds the model, the application layer coordinates use cases, infrastructure provides technical capability.
- **Entity** (a.k.a. *reference object*). An object defined by identity that persists across state changes. Example: `Trade`, `Position`, `Account`.
- **Value object.** An object defined by its attributes, not identity, and treated as immutable. Example: `Price`, `RiskAmount`, `ImpulseColor`, `EMACrossover`.
- **Service.** A stateless operation that doesn't naturally belong to any entity or value object. Example: a quality-filter computation, a risk-gate sequence.
- **Module** (a.k.a. *package*). A grouping of model elements that tells one chapter of the domain's story. Cohesion is conceptual, not just technical.
- **Aggregate.** A cluster of entities and value objects with a single root entity, bounded for the purpose of consistency. Invariants are enforced at the aggregate boundary.
- **Factory.** A construct that encapsulates the assembly of an entity or aggregate, ensuring invariants are met at creation.
- **Repository.** A construct that provides the illusion of an in-memory collection of all instances of an aggregate root, encapsulating storage.
- **Specification.** A predicate-shaped value object that names a domain rule and can be evaluated, combined, and used to query.

## 3. Layered architecture

Evans's standard partition (chapter 4, pp. 41–48):

| Layer | Responsibility | Elder mapping |
| ----- | -------------- | ------------- |
| User Interface | Show information; interpret input | `dashboard/` (Streamlit) |
| Application | Coordinate tasks; direct domain work; thin, stateless of *business* state | Pipeline managers (`ScanManager`, `TradeManager`, `ReviewManager`, planned) |
| Domain | The model: concepts, rules, business state | `core/state.py` (typed events), `agents/risk_agent.py`, `indicators/elder.py`, `risk_parameters.py` |
| Infrastructure | Technical capability supporting higher layers | `core/llm.py`, `core/ib_*.py` (deferred), `knowledge/store.py`, `db/` |

> "The application layer is responsible for ordering the notification. The domain layer is responsible for determining if a threshold was met." (p. 65)

The application layer is **kept thin** (p. 41). It doesn't hold business state and it doesn't enforce business rules. It coordinates: it decides *that* a stop-monitor scan must run, that an LLM analysis must follow, that a risk gate must precede execution. *How* the risk gate decides is in the domain layer. *What it physically takes to fire an order* is in infrastructure.

### 3.1 The Smart UI anti-pattern

Evans treats the SMART UI (p. 46–48) as a legitimate alternative for projects with simple data-entry-and-display requirements and no advanced object modelers. He explicitly says it is mutually exclusive with model-driven design and warns against committing to a flexible language while building a SMART UI ("just using a flexible language doesn't create a flexible system, but it may well produce an expensive one," p. 48).

For Elder this is a *don't go there* boundary, not a real choice. The dashboard reads from PostgreSQL using the same domain types the engine writes; there is no parallel rule-encoding in the UI layer. If a dashboard widget ever wants to "just check the 2% Rule itself for speed," that's the SMART UI temptation and is rejected on sight.

### 3.2 Frameworks: selective use

> "A lot of the downside of frameworks can be avoided by applying them selectively to solve difficult problems without looking for a one-size-fits-all solution. Judiciously applying only the most valuable of framework features reduces the coupling of the implementation and the framework, allowing more flexibility in later design decisions." (p. 44)

Elder uses asyncio, ib_async, ChromaDB, Anthropic's SDK. Each is a framework with its own opinions. The discipline is to wrap each at the infrastructure boundary and let the domain layer see only domain terms. ib_async's `Order` does not appear in `agents/risk_agent.py` or `core/state.py`; it's translated at the IB connection boundary into Elder's types.

## 4. The building blocks (Part II)

Evans's four primary building blocks for an object model (chapter 5, p. 49). Each is a named distinction that earns its keep by clarifying what kind of object you are looking at and what design rules apply to it.

### 4.1 Entities

> "When an object is distinguished by its identity, rather than its attributes, make this primary to its definition in the model. … Be alert to requirements that call for matching objects by attributes. Define an operation that is guaranteed to produce a unique result for each object." (pp. 55–56)

An entity is an object that has continuity through a lifecycle. Two entities with identical attributes are still distinct things. Identity must be defined operationally — a unique key, an external identifier, or a generated symbol — and it must remain stable through serialization, persistence, and reconstitution.

**Modeling rule** (p. 56): keep entities spare. Strip them down to the attributes that establish identity and the behavior essential to the concept. Push other attributes into associated value objects. The entity coordinates; it doesn't accumulate.

**Elder entities** (committed):

- `Trade` — identified by trade ID; persists from entry through close.
- `Position` — identified by ticker + account + entry timestamp.
- `Account` (`AccountState`) — identified by IB account number.
- `Run` (a pipeline execution) — identified by `run_id`; persists for audit.

**Not entities for Elder:**

- A `Price` snapshot — has no continuity beyond the bar it represents. Value object.
- An `EMACrossover` event — same.
- An `ImpulseColor` for a given bar — same.

### 4.2 Value objects

> "When you care only about the attributes of an element of the model, classify it as a VALUE OBJECT. Make it express the meaning of the attributes it conveys and give it related functionality. Treat the VALUE OBJECT as immutable. Don't give it any identity and avoid the design complexities necessary to maintain ENTITIES." (p. 60)

> "The attributes that make up a VALUE OBJECT should form a conceptual whole." (p. 60, citing Cunningham's WHOLE VALUE pattern)

A value object is defined by *what it is*, not *who it is*. Two `RiskAmount` instances with the same dollar value are interchangeable. Value objects in Elder are `frozen=True` dataclasses by default.

**Whole-value rule.** Don't pass a city, state, and ZIP code as three separate strings; pass an `Address` value object. For Elder: don't pass `entry_price`, `stop_price`, `share_count` as three primitives — pass a `TradeProposal` value object that knows what its risk is. This is the anti-`primitive obsession` rule expressed positively.

**Designing value objects** (pp. 60–63):

- Default to immutable. Mutability is a special case justified by performance, never by convenience.
- Bidirectional associations between two value objects make no sense — without identity, there is nothing to point back to. If you find one, you misclassified one of the two as a value.
- Sharing instances safely is possible only when the value is immutable. For Elder this is automatic with `frozen=True`.

**Elder value objects** (committed and planned):

- `RiskAmount`, `Price`, `Capital` — money types using `Decimal`.
- `ImpulseColor` (RED/YELLOW/GREEN), `Direction` (LONG/SHORT) — enums acting as value objects.
- `TradeProposal`, `ApprovedTrade`, `RejectedTrade`, `RiskDecision` — domain events flowing through the pipeline.
- `EMACrossover`, `MACDDivergence` — indicator findings.
- `Apgar` (5 components, 1–10 scale) — composite score; whole-value because the score is meaningless without the components.

**Closure of operations** (Evans pp. 187–189): when a value's operation takes the same type and returns the same type — `Price.plus(Price) -> Price`, `RiskAmount.scaled(factor) -> RiskAmount` — the operation is *closed*. Closed operations compose without dragging in unrelated types and read declaratively at call sites.

### 4.3 Services

> "When a significant process or transformation in the domain is not a natural responsibility of an ENTITY or VALUE OBJECT, add an operation to the model as a standalone interface declared as a SERVICE. Define the interface in terms of the language of the model and make sure the operation name is part of the UBIQUITOUS LANGUAGE. Make the SERVICE stateless." (p. 65)

A service is a standalone domain operation. Its *operation* is meaningful in the domain (e.g., funds transfer, risk-gate sequence, momentum ranking); its *home* is not naturally any single entity or value object. Three properties (p. 65):

1. The operation relates to a domain concept that doesn't fit naturally on an entity or value.
2. The interface is defined in terms of other model elements.
3. The operation is stateless.

**Distinguishing service layers** (p. 65):

- **Domain service.** Embeds business rules. Example for Elder: a `RiskGateSequence` service that runs gates 1–7 in order against a proposal and returns an `ApprovedTrade` or `RejectedTrade`. The 2% Rule, 6% Rule, and capability gates are domain logic.
- **Application service.** Coordinates a use case. Example: the application service that, on each daily-pipeline tick, asks the scanner for candidates, hands them to the analyzer, sends approved trades to the executor.
- **Infrastructure service.** Pure technical capability. Example: `core/llm.py`'s Claude API client; `core/ib_*.py`'s order placement when built.

**Caveat against service-itis** (p. 65):

> "SERVICES should be used judiciously and not allowed to strip the ENTITIES and VALUE OBJECTS of all their behavior."

The risk in DDD-curious codebases is that everything becomes a `*Service` and entities become anemic data containers. The discipline: ask whether the operation has a natural object home before reaching for a service. Funds transfer involves two accounts, so it doesn't sit on either alone — service. The 2% Rule check, on the other hand, is a property of the account's state at the time of the proposal — `account.evaluate_proposal(...)` is a domain method, not a service.

### 4.4 Modules (packages)

> "Choose MODULES that tell the story of the system and contain a cohesive set of concepts. … Give the MODULES names that become part of the UBIQUITOUS LANGUAGE." (p. 70)

Modules in Evans's sense are conceptual chunks of the model, not just code-organization conveniences. They should:

- Have names that come from the ubiquitous language, not from technical strata (`risk`, `indicators`, `knowledge`, not `services`, `helpers`, `utils`).
- Group concepts by conceptual relationship, not by tier or framework convention.
- Coevolve with the model — if the model changes shape, the modules change shape.

**The infrastructure-driven packaging anti-pattern** (pp. 70–71):

> "Elaborate technically driven packaging schemes impose two costs. … the code no longer reveals the model. … There is only so much partitioning a mind can stitch back together, and if the framework uses it all up, the domain developers lose their ability to chunk the model into meaningful pieces."

Splitting one conceptual entity across "data," "session," "facade," and "interface" packages is the canonical example. It preserves a tier separation at the cost of obscuring the model. The fix: **keep all the code that implements one conceptual object in one module**, and use packages to separate domain from non-domain, not to separate aspects of one concept.

Elder is reasonably well-aligned today. `core/`, `agents/`, `indicators/`, `knowledge/`, `db/` are domain-shaped names. The watch is for technical-strata creep — a future `core/services/`, `core/handlers/`, `core/managers/` that fragments concepts.

### 4.5 Aggregates

This is the most consequential pattern Evans introduces and the one Elder leans on most. Read carefully.

> "Cluster the ENTITIES and VALUE OBJECTS into AGGREGATES and define boundaries around each. Choose one ENTITY to be the root of each AGGREGATE, and control all access to the objects inside the boundary through the root. Allow external objects to hold references to the root only. Transient references to internal members can be passed out for use within a single operation only." (p. 79)

Six rules (p. 78):

1. The root entity has global identity and is responsible for checking invariants.
2. Root entities have global identity. Internal entities have local identity, unique only within the aggregate.
3. Nothing outside the aggregate boundary can hold a reference to anything inside, except to the root.
4. Only aggregate roots can be obtained directly with database queries.
5. Objects within the aggregate can hold references to other aggregate roots.
6. A delete operation must remove everything inside the aggregate boundary. When a change to any object in the aggregate is committed, all invariants of the whole aggregate must be satisfied.

**Why aggregates exist.** Database transactions need a scope. Invariants need a unit. Without explicit boundaries, every change risks touching every other thing. Locking gets pessimistic; consistency gets fragile. Aggregates declare the *transactional unit* in domain terms — the set of objects that must be consistent as a group, separated from the objects that can drift.

**Discovering aggregates is a domain question** (Evans's purchase-order example, pp. 79–82):

- An invariant says total line-item amounts ≤ PO limit. PO and items are one aggregate; locking the items individually fails.
- A line item references a part. Should a part-price change propagate? Domain answer: no — once the PO is filled, prices are frozen. So the line item *copies* the part's price at creation; the part is a separate aggregate; the eventual-consistency relationship between them is acceptable.

The pattern: tighten what must be consistent, loosen what merely should-mostly-be-consistent. The aggregate boundary is the line.

**Elder aggregates (proposed for build items #1, #2, #10):**

| Aggregate root | Members | Invariants |
| -------------- | ------- | ---------- |
| `AccountState` | open `Position`s, current `Equity`, monthly P&L total | 6% Rule (sum of open-position risks ≤ 6% of month-start equity); equity ≥ 0; max_positions = ⌊equity / min_position_size⌋ |
| `Trade` (filled) | entry/exit `Price`s, `Position`, fills, commissions, slippage | P&L derivable from entry and exit; never two open instances for same ticker+account |
| `Run` (pipeline execution) | `Decision` events, costs, status | Idempotent on `run_id`; total cost ≤ run budget |
| `TradeProposal` (pre-execution) | candidate `Position`, computed `RiskAmount`, `Apgar` score, gate decisions | 2% Rule (proposal risk ≤ 2% account equity); only constructable through `AccountState.evaluate_proposal` |

Note `Position` appears as a member of `AccountState` *and* as a member of `Trade`. Same conceptual thing, different aggregate roles at different lifecycle phases. The transactional unit changes because the invariants change: while open, position state is bounded by the account's risk math; once closed, the trade record is a historical aggregate.

**The 2% Rule and 6% Rule are aggregate invariants.** This is the design-grade reason CLAUDE.md says `ApprovedTrade` is constructable only through `AccountState.evaluate_proposal`: the construction *is* the invariant check, by Evans's rule 6. `RiskDecision` is not a free-standing object the analyst builds; it is the structured outcome of a guarded factory method on the aggregate root.

**Cross-aggregate references go through roots** (rule 5). A `Trade` references the `Account` it belongs to by ID; it doesn't hold a pointer into the `Account`'s open-position list. A `Run` references the `Trade`s it produced by ID. This rule prevents the god-object trap — `PipelineState`'s problem was that every stage held references into every other stage's mutable state.

### 4.6 Factories

> "Shift the responsibility for creating instances of complex objects and AGGREGATES to a separate object, which may itself have no responsibility in the domain model but is still part of the domain design. Provide an interface that encapsulates all complex assembly and that does not require the client to reference the concrete classes of the objects being instantiated. Create entire AGGREGATES as a piece, enforcing their invariants." (p. 85)

Two rules for any good factory (p. 85):

1. Each creation method is atomic and enforces all invariants of the created object or aggregate.
2. The factory should be abstracted to the type desired, not to the concrete class.

**When a constructor is enough** (p. 87):

- The class is the type (no hierarchy, no polymorphism).
- The client cares about the implementation choice.
- All construction parameters are simple values the client already has.
- The construction doesn't require multi-step assembly.

For Elder, most value objects use plain constructors — `Price(Decimal("100.50"))` is fine. Aggregates use factory methods on the root — `AccountState.create_initial(...)`, `account.evaluate_proposal(...)` returning either an `ApprovedTrade` or a `RejectedTrade`. The factory hides which concrete type is returned and guarantees invariants at construction.

**Reconstitution** (p. 89): a factory used to rebuild an aggregate from storage differs from a creation factory in two ways: it does not assign a new identity (it preserves the stored one), and it must handle invariant violations more flexibly (the data is already there; the question is what to do about it). For Elder, reconstitution lives in the repository layer; if the data layer ever returns a stored aggregate that violates current invariants, the question becomes "fix the data, change the invariants, or quarantine the row" — explicitly, not silently.

### 4.7 Repositories

> "For each type of object that needs global access, create an object that can provide the illusion of an in-memory collection of all objects of that type. Set up access through a well-known global interface. Provide methods to add and remove objects, which will encapsulate the actual insertion or removal of data in the data store. … Provide REPOSITORIES only for AGGREGATE roots that actually need direct access." (p. 93)

A repository encapsulates persistence and presents a domain-shaped interface to the rest of the system. Four properties (p. 94):

- Simple model for obtaining persistent objects and managing their lifecycle.
- Decouples application and domain from persistence technology.
- Communicates design decisions about object access (which aggregates are query-able, which must be reached by traversal).
- Allows easy substitution of an in-memory implementation for testing.

**Repositories per aggregate root, not per class.** For Elder:

- `AccountRepository` — find by account ID, save updated state.
- `TradeRepository` — find by trade ID, find by date range, save closed trades.
- `RunRepository` — idempotent insert by `run_id`, find by date.
- `LessonRepository` (knowledge layer) — semantic-similarity search; this is a repository even though backed by ChromaDB.
- `SimilarTradeRepository` — same reasoning.

There is *no* `PositionRepository` because `Position` is not an aggregate root in Elder's model — it's a member of either `AccountState` (open) or `Trade` (closed). External code asks the account for its open positions; it doesn't query positions independently.

**The find-or-create anti-pattern** (p. 98):

> "This function should be avoided. It is a minor convenience at best. … Usually, the distinction between a new object and an existing object is important in the domain, and a framework that transparently combines them will actually muddle the situation."

For Elder: `account_repo.find_or_create(account_id)` is the wrong shape. The system either has an account or it doesn't; the difference matters (an account being created today has zero history; an account being loaded has months of P&L). Two methods, two intentions.

**Transaction control belongs to the client** (p. 96):

> "Although the REPOSITORY will insert into and delete from the database, it will ordinarily not commit anything. … the client presumably has the context to correctly initiate and commit units of work."

For Elder, this means the application service ("execute one trade") owns the transaction boundary; the repository participates but does not commit. The exception is read-only operations, where the question doesn't arise.

**Specification-based queries** (p. 95, expanded in chapter 9): a repository can take a `Specification` value object — a named domain rule — and return matching aggregates. `delinquent_invoices = invoice_repo.satisfying(DelinquentInvoiceSpecification())`. For Elder, this lands as: `closed_trades = trade_repo.satisfying(LosingTradeSpecification(threshold=Decimal("-100")))`. The specification is itself a domain concept; named rules become reusable.

## 5. Supple design (Part III)

The Part II patterns give you objects that fit. Part III is about making the design *supple* — flexible enough to refactor as the model deepens, expressive enough that complex behavior reads as clearly as simple behavior. The patterns below are how Evans's "deep model" actually expresses itself in code.

### 5.1 Intention-revealing interfaces

> "Name classes and operations to describe their effect and purpose, without reference to the means by which they do what they promise." (chapter 10, p. 169)

The interface names *what*, not *how*. `paint.mixIn(other)` says what's happening; `paint.paint(other)` says nothing. For Elder: `account.evaluate_proposal(proposal)` over `account.check_2pct_and_6pct_and_position_count(...)`. The internal implementation may run several checks; the name says what the caller is asking.

This is the same discipline as the modularity guide's "name precisely labels the abstraction" rule, applied to method names instead of module names.

### 5.2 Side-effect-free functions

> "Place as much of the logic of the program as possible into functions, operations that return results with no observable side effects. Strictly segregate commands … into very simple operations that do not return domain information." (chapter 10, p. 175)

Two kinds of operations:

- **Queries.** Return information; have no observable side effects.
- **Commands.** Change state; ideally don't return domain information.

When the two are mixed — a method that updates state and returns a computed result — reasoning about it requires understanding both at once. Splitting them lets each be tested and used independently.

For Elder this is a strong fit because most domain logic operates on immutable value objects. `risk_amount.scaled(factor)` returns a new `RiskAmount` and changes nothing. `apgar.with_component(component, score)` returns a new `Apgar`. Commands cluster around aggregate roots: `account.record_fill(fill)` mutates the account's state; `account.evaluate_proposal(...)` is a pure query that returns a typed event.

> "You can't avoid commands in most software systems … but you can keep them simple by separating them from queries." (p. 176)

### 5.3 Assertions

> "State post-conditions of operations and invariants of classes and AGGREGATES. If ASSERTIONS cannot be coded directly in your programming language, write automated unit tests for them." (chapter 10, p. 180)

Evans uses *assertion* in a broader sense than `assert` statements: every method has implicit pre- and post-conditions, every aggregate has implicit invariants. The discipline is to **state them explicitly** — in tests, in docstrings, in `Final` declarations, in invariant-checking code at aggregate boundaries.

For Elder this overlaps directly with `docs/elder-invariants.md` (the implemented-only invariants catalog). RISK-001 through RISK-012 are aggregate invariants for `AccountState`; the binding tests in `tests/unit/test_risk_agent.py` are the executable form of those assertions. Evans's chapter 10 *is* the design rationale for the catalog.

### 5.4 Conceptual contours

> "Decompose design elements (operations, interfaces, classes, and AGGREGATES) into cohesive units, taking into consideration your intuition of the important divisions in the domain. … Locate the conceptual contours by listening for changes that don't follow the contours of objects in the design." (chapter 10, p. 187)

A unit (method, class, aggregate, module) has good contours when changes that should be local *are* local. When a single domain change ripples across many places, the contours are wrong. When two domain concepts that ought to be separable are tangled, the contours are wrong.

> "These are oversimplifications that don't work well as general rules: 'Sometimes people chop functionality fine to allow flexible combination. Sometimes they lump it large to encapsulate complexity. Sometimes they seek a consistent granularity.'" (p. 186)

Don't seek "consistent granularity." Seek *contours that match the domain*. Some methods are one line; some are 25. Some classes have three public methods; some have ten. The rule is conceptual cohesion, not size.

For Elder this guides the pipeline-engine refactor (item #1): the temptation will be to seek a uniform shape for every stage. Evans's rule says find each stage's natural boundary first, then accept that they may differ in size and shape.

### 5.5 Standalone classes

> "Try to factor the most intricate computations into STANDALONE CLASSES, perhaps by modeling VALUE OBJECTS held by the more connected classes. … The goal is not to eliminate all dependencies, but to eliminate all nonessential ones." (p. 191)

The most computation-heavy concepts (Apgar, indicator math, momentum scoring, QARP composite ranking) should live in classes whose dependencies are minimal — ideally just primitives, value objects from the same level, and `Decimal`/`datetime` stdlib. This makes those classes testable in isolation and maximally portable across pipeline stages.

For Elder, `indicators/elder.py` already follows this — its functions take OHLCV data (ndarray-shaped or Arrow IPC bytes) and return `EMACrossover`, `MACDDivergence`, `Channel` value objects. Zero dependencies on agents, repositories, or pipeline state. Factor #18 in the build plan (invariants catalog) extends this discipline to indicator results.

### 5.6 Closure of operations

> "Where it fits, define an operation whose return type is the same as the type of its argument(s)." (p. 191)

Closed operations compose. `Price.plus(Price) -> Price` is closed; `RiskAmount.scaled(Decimal) -> RiskAmount` is half-closed (one argument is a primitive, but the receiver and return are the same type). Both are valuable.

For Elder this lands on monetary types and on specifications:

- `Capital.plus(Capital) -> Capital`, `RiskAmount.minus(RealizedPL) -> RiskAmount` — closed operations on money types make P&L math read declaratively.
- `Specification.and_(Specification) -> Specification`, `Specification.or_(Specification) -> Specification`, `Specification.not_() -> Specification` — closed boolean algebra over specifications. Build complex queries like `LosingTrade().and_(InLastWeek())` declaratively.

### 5.7 Specification

> "Create explicit predicate-like VALUE OBJECTS for specialized purposes. A SPECIFICATION is a predicate that determines if an object does or does not satisfy some criteria." (chapter 9, p. 153)

Three uses (p. 154):

1. **Validation.** Does this object satisfy the rule?
2. **Selection.** From a collection, return the ones satisfying the rule.
3. **Building-to-order.** Given the rule, construct an object that satisfies it.

For Elder, validation and selection are the live cases:

- `RiskBudgetSpecification(account)` — does this proposal fit the 6% Rule given current open risk? Use case: filtering candidate proposals before LLM analysis.
- `ImpulseAllowsLong()` — does the daily Impulse permit a long entry? Use case: filtering scanner output.
- `LosingTradeSpecification(threshold)` — historical filter for the diary agent's "what went wrong" review.

Specifications are value objects. They are not services. They have closed boolean operations. They name rules; the rule names enter the ubiquitous language.

**Caveat** (p. 156–157):

> "Full implementation of logic in objects is a major undertaking. … Most of our rules fall into a few special cases. We can usually borrow the predicate idea, even if we don't implement it that completely."

Don't try to build a Prolog clone in Python. Borrow the *named-predicate value-object* idea where it pays.

## 6. Strategic design (Part IV)

Strategic design is the half of DDD that summaries underweight. It is where the most consequential decisions for Elder live: which models exist, how they relate, where the core lives, how systems integrate.

### 6.1 Bounded contexts

> "Explicitly define the context within which a model applies. Explicitly set boundaries in terms of team organization, usage within specific parts of the application, and physical manifestations such as code bases and database schemas. Keep the model strictly consistent within these bounds, but don't be distracted or confused by issues outside." (chapter 14, p. 336)

A bounded context is the boundary inside which one model is coherent. Outside the boundary, terms may mean different things, models may diverge, and the only legitimate way across is a deliberate translation.

> "BOUNDED CONTEXTS Are Not MODULES. … MODULES also organize the elements within one model; they don't necessarily communicate an intention to separate CONTEXTS." (p. 337)

Modules organize concepts within a model. Bounded contexts separate models. They are different rules.

**Elder's bounded contexts:**

The strategic question CLAUDE.md already answers: **ADW infrastructure and Elder content are categorically separate.** They are two bounded contexts in one repository. ADW (`adws/`, `.claude/`, `scripts/`, `conftest.py` at root) processes Elder; it is not part of Elder's runtime model. The CLAUDE.md "Scope" section is the *context map* for this separation in narrative form.

Within Elder itself, the open question is whether the four pipeline stages (fund rotation, QARP filter, momentum rank, Elder timing) constitute one bounded context or four. The decision rule (p. 376):

> "The ultimate decision: One BOUNDED CONTEXT or several? Make the decision based on each subdomain's relationship to the others. Forces favoring larger boundaries: smoother flow, easier comprehension, hard translations, shared language. Forces favoring smaller: less communication overhead, easier continuous integration, smaller models need less abstraction skill."

For Elder's four stages, the practical answer is **one bounded context**. The vocabulary is shared (ticker, momentum, quality, Apgar, Impulse), the lifecycle is sequential (each stage's output is the next stage's input), there is no team boundary or independent deployment. Trying to split them into separate contexts would create translation overhead with no compensating clarity.

Where the boundary *does* sit, in addition to ADW/Elder:

- **Knowledge context.** ChromaDB vector store + structured PostgreSQL store, accessed through `LessonRepository` and `SimilarTradeRepository`. Internally, the lesson is a different concept from a trade: it has embeddings, similarity scoring, retrieval-by-vector. The lesson context has its own vocabulary (corpus, similarity, embedding). The integration to Elder's main context is through a clean repository interface — the analysis agent asks for "five most-similar past trades" and gets back typed lessons.
- **Execution context** (when item #12 lands). IB connection management is its own context with its own vocabulary (clientId, reqId, contract, bracket order). Domain code does not see those terms; an anticorruption layer translates.

### 6.2 Context map

> "Map the existing terrain. Take up transformations later. Identify each model in play on the project and define its BOUNDED CONTEXT. … Describe the points of contact between the models, outlining explicit translation for any communication and highlighting any sharing." (chapter 14, p. 344)

A context map is a written and diagrammatic description of which models exist and how they relate. For Elder, the relevant pieces today:

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| Elder | ADW | Customer/Supplier (Elder is customer, ADW is supplier of dev workflow) | ADW's job is to make Elder's development reliable; CLAUDE.md scope rules are the contract |
| Elder | Knowledge | Repository/Anticorruption | Domain code asks repositories; ChromaDB/Postgres specifics hidden |
| Elder | IB (when built) | Anticorruption Layer | ib_async types translated to domain types at the boundary |
| Elder | EDGAR/Yahoo (when built) | Anticorruption Layer | Raw 13F/N-PORT/OHLCV translated to domain `Holding`, `Bar` value objects |
| Elder | Anthropic API | Conformist (small interface) | LLM client wraps `httpx` thinly; Anthropic's request/response shape is the interface; we don't fight it |

Evans's discipline (p. 354):

> "Don't say, 'George's team's stuff is changing, so we're going to have to change our stuff that talks to it.' Say instead, 'The Transport Network model is changing, so we're going to have to change the translator for the Booking context.'"

Talk about model boundaries by their names, not by who owns them.

### 6.3 Integration patterns: the spectrum

Evans gives a spectrum from cooperation to defense (chapter 14 throughout):

| Pattern | When | Cost |
| ------- | ---- | ---- |
| **Continuous integration** | Single team, single model | Communication overhead |
| **Shared kernel** | Two teams, partial overlap, mutual consultation | Synchronization on the kernel |
| **Customer/supplier** | One-way dependency, shared management | Upstream commits to downstream's tests |
| **Conformist** | One-way dependency, upstream's model is acceptable, large interface | Foreclose enhancement beyond upstream |
| **Anticorruption layer** | Upstream's model is bad or incompatible; integration is essential | Cost of building and maintaining the translator |
| **Separate ways** | No functional relationship | Forecloses future integration |
| **Open host service / Published language** | Many integrators; need a stable formal protocol | Stable interface design and discipline |

**Anticorruption layer** is the most consequential of these for Elder (chapter 14, pp. 365–370):

> "Create an isolating layer to provide clients with functionality in terms of their own domain model. The layer talks to the other system through its existing interface, requiring little or no modification to the other system. Internally, the layer translates in both directions as necessary between the two models." (p. 365)

Structure (p. 367):

- **FACADE** in the *external* system's bounded context, simplifying access to it.
- **ADAPTER** per service in your model, translating semantically.
- **Translator** — lightweight, stateless conversion logic.

For Elder, the IB integration (item #12) is the textbook ACL case. ib_async exposes `Order`, `Contract`, `Execution` types that don't fit Elder's model. The ACL exposes `submit_bracket_order(approved_trade) -> OrderConfirmation`, internally calling ib_async with translated arguments and translating fills back to Elder's types. The reaper of `httpx.ConnectError` and `psycopg.OperationalError` mentioned in the modularity guide §2.1 is the same pattern.

**Conformist**, on the other hand, is when the upstream's model is fine and integration cost dominates. Elder's relationship with the Anthropic SDK is conformist: their request/response shape is the interface. Building an ACL over Anthropic's SDK would be over-engineering — the SDK is already a clean translation of the HTTP API.

> "It is very unappealing emotionally, which is why we choose [conformist] less often than we probably should." (p. 364)

For dependencies that are both well-designed and clearly the senior partner, conform. For dependencies whose models would corrupt yours, build the wall.

### 6.4 Distillation (chapter 15)

The strategic-design pattern with the highest leverage for ambitious projects. Evans's argument:

> "Apply top talent to the CORE DOMAIN, and recruit accordingly. … If you need to keep some aspect of your design secret as a competitive advantage, it is the CORE DOMAIN." (p. 397)

**Core domain.** The part of the model that is the project's reason to exist. For Elder this is *unambiguous*:

- The 2% Rule and 6% Rule risk envelopes.
- The Triple Screen and Impulse System for direction and timing.
- The QARP filter (quality + value composite) for fundamental selection.
- The Trade Apgar for systematic entry-quality scoring.
- The risk-gate sequence enforcing all of the above.

These are the things Elder does *that off-the-shelf trading systems do not*. They cannot be bought.

**Generic subdomains** (p. 405): cohesive subdomains that are not the project's reason to exist. For Elder:

- IB connection management (build item #12). Generic. Could in principle be replaced by Tradier, Alpaca, or another broker; the domain doesn't care which.
- Cost tracking (item #2). Generic. Commission and slippage capture is the same for any system.
- Vector storage (knowledge context). Generic. ChromaDB could be Pinecone or Qdrant.
- Dashboard (item #16). Generic. Streamlit could be a different frontend.

> "Generic ≠ reusable. … You should specifically not concern yourself with the reusability of that code. This would go against the basic motivation of distillation." (p. 416)

The point of identifying generic subdomains is to **stop paying core-domain attention to them**. Use off-the-shelf where it fits; build minimum viable where it doesn't; concentrate the team's design effort on the core.

**Cohesive mechanisms** (p. 425). A mechanism that solves a sticky computational problem but is not itself part of the domain model. Distill it into a separate lightweight framework with an intention-revealing interface.

For Elder this lands on:

- The pipeline orchestration primitives (item #1: `StageCoordinator`, `Gate[T]`, `Retry`, `EventBus`). These are mechanisms; they don't represent the trading domain. They're a framework that the domain managers (`ScanManager`, `TradeManager`, `ReviewManager`) use.
- Arrow IPC for cross-process indicator computation. A mechanism, not a domain concept.
- Cost ledger persistence. A mechanism for capturing the structured cost stream.

> "A model proposes; a COHESIVE MECHANISM disposes." (p. 427)

The trading model says "compute EMA, then MACD, then Impulse." The mechanism is whatever ProcessPoolExecutor + Arrow IPC + asyncio.gather does to make that fast and reliable.

**Domain vision statement** (p. 422). One page describing the core domain and the value it brings. CLAUDE.md plus `docs/comprehensive-design.md` plus `docs/risk-model-overview.md` collectively serve this role for Elder; a focused one-page statement of "what Elder is" might be worth distilling once the build plan reaches the post-#10 milestone.

### 6.5 Patterns from chapter 16 not used by Elder

Three patterns from chapter 16 deserve a deliberate "not for Elder, here's why":

- **System metaphor.** Evans is skeptical even in the book — "Few projects have found really useful METAPHORS, and people have tried to push the idea into domains where it is counterproductive" (p. 442). For Elder, no candidate metaphor. Skip.
- **Knowledge level.** Powerful but heavy: a configuration model that constrains an operational model. "If the KNOWLEDGE LEVEL becomes complex, the system's behavior becomes hard to understand. The users (or superuser) who configure it will end up needing the skills of a programmer" (p. 469). Elder has no end-user-configurable rules; the trading rules are fixed by Elder's methodology. Skip.
- **Pluggable component framework.** Heavy; presupposes multiple independent applications sharing a core. "A PLUGGABLE COMPONENT FRAMEWORK should not be the first large-scale structure applied on a project, nor the second" (p. 473). Skip until there's a second application sharing Elder's core.

**Responsibility layers** (p. 451) is the one large-scale structure pattern that *might* fit Elder eventually:

> "Look at the conceptual dependencies in your model and the varying rates and sources of change. … Cast them as broad abstract responsibilities. … Refactor the model so that the responsibilities of each domain object, AGGREGATE, and MODULE fit neatly within the responsibility of one layer."

Evans's example layers (Potential / Operations / Decision Support / Policy / Commitment) map roughly to Elder's pipeline stages: scanning is *Potential*, execution is *Operations*, review is *Decision Support*, the risk-gate sequence is *Policy*. This is suggestive, not prescriptive — Evans warns against imposing a structure before it has emerged (p. 433: "Less is more. … An ill-fitting structure is worse than none"). Revisit when item #1 (pipeline refactor) settles and items #4–#10 lay down the rest.

## 7. Refactoring discipline (Part III)

Evans's Part III is about *how* the model gets better over time. Three points are non-negotiable for Elder.

### 7.1 Listen to the language

> "Listen to the language the domain experts use. … Are there terms that succinctly state something complicated? Have they corrected your word choice (perhaps diplomatically)? Do the puzzled looks on their faces go away when you use a particular phrase? These are hints of a concept that might benefit the model." (chapter 9, p. 137)

For Elder, the domain expert is Alexander Elder via *Come Into My Trading Room* and *The New Trading for a Living*. CLAUDE.md's vocabulary section is the operational form: when speaking about the system, use "Triple Screen," "Impulse System," "SafeZone stop," "Channel," "2% Rule," "Trade Apgar," "ABC Rating" — Elder's exact terms, never generic synonyms. A concept that doesn't have an Elder-vocabulary name probably doesn't exist in the domain.

### 7.2 Make implicit concepts explicit (chapter 9, p. 134)

When a domain rule appears as a guard clause, a comment, a sequence of conditionals, or a constant the team keeps re-deriving — it's an *implicit* concept. Refactor it into a named object. Evans's overbooking example (p. 12): a `voyage.capacity() * 1.1` comparison becomes an `OverbookingPolicy`, even when no algorithmic substitution is yet needed.

For Elder, build item #1's pipeline refactor is essentially this discipline applied at scale: every rule currently embedded in `core/pipeline.py`'s `Signal`-handling logic becomes a typed event with a named meaning. The 2% Rule lives in `AccountState.evaluate_proposal`, not in a guard clause inside the analysis agent.

### 7.3 Refactoring toward deeper insight

> "Refactor the model toward greater clarity, abstraction, and aptness. … Indications: the design doesn't express current understanding; important concepts are implicit; you see an opportunity to make some part suppler." (chapter 13, p. 209)

> "If you wait until you can make a complete justification for a change, you've waited too long." (p. 209)

**Counter-rule** (p. 209):

> "Don't refactor the day before a release. Don't introduce 'supple designs' that are just demonstrations of technical virtuosity but fail to cut to the core of the domain. Don't introduce a 'deeper model' that you couldn't convince a domain expert to use, no matter how elegant it seems."

Two operating implications for Elder:

- The pipeline refactor (item #1) will likely involve a *breakthrough* moment (chapter 8): after several smaller refactorings, the right shape suddenly becomes obvious and a non-incremental change is required. Evans is honest about this (p. 122): "The gospel of refactoring is that you always go in small steps … but to refactor our code to this new model would require changing a lot of supporting code, and there would be few stable stopping points in between." Plan for it; don't pretend it doesn't happen.
- A change you can't explain to a hypothetical Alexander Elder reading the code is not a deeper model. It's technical virtuosity. If a refactoring removes a vocabulary term Elder uses or introduces a term Elder wouldn't recognize, that's a red flag.

## 8. Elder mapping summary

| Pattern | Status in Elder | Notes |
| ------- | --------------- | ----- |
| Layered architecture | Implemented | UI=`dashboard/`, App=managers (planned), Domain=`core/state.py`, `agents/`, `indicators/`, Infra=`core/llm.py`, `db/` |
| Smart UI | Rejected | Not applicable; complex domain |
| Entity | Implemented (`Trade`, `Position`, `Account`, `Run`) | |
| Value object | Implemented (frozen dataclasses) | `Price`, `RiskAmount`, `ImpulseColor`, `TradeProposal`, `ApprovedTrade` |
| Service | Partial | Risk-gate sequence (item #10), QARP filter (item #7) need explicit service shape |
| Module | Implemented | `core/`, `agents/`, `indicators/`, `knowledge/`, `db/` are domain-shaped |
| Aggregate | Partial | `AccountState` has invariants; `Trade`, `Run` aggregates need formalization in items #1, #2 |
| Factory | Partial | `account.evaluate_proposal` is the factory pattern; needs more |
| Repository | Partial | `KnowledgeStore` protocols exist; `AccountRepository`, `TradeRepository`, `RunRepository` to land with items #2, #14 |
| Intention-revealing interfaces | Standing rule | Code review check |
| Side-effect-free functions | Implemented | Frozen dataclasses + pure indicator functions |
| Assertions | Implemented | `docs/elder-invariants.md` + binding tests |
| Conceptual contours | Standing rule | Watch for changes that don't follow contours |
| Standalone classes | Implemented | `indicators/elder.py` |
| Closure of operations | Partial | Money types could lean further into closure |
| Specification | Not yet | Candidate for items #7, #10 (filter and gate sequences) |
| Bounded context | Implemented (ADW vs Elder) | Knowledge, Execution contexts to formalize |
| Context map | Narrative form | CLAUDE.md "Scope" section |
| Anticorruption layer | Planned | IB integration (item #12); EDGAR/Yahoo (items #5, #6) |
| Conformist | Implemented | Anthropic SDK relationship |
| Separate ways | N/A | |
| Customer/supplier | Implemented (Elder ↔ ADW) | |
| Open host service / Published language | N/A | |
| Core domain | Identified | Risk envelopes, Triple Screen/Impulse, QARP, Apgar, gate sequence |
| Generic subdomains | Identified | IB, cost tracking, vector store, dashboard |
| Cohesive mechanisms | Partial | Pipeline primitives (item #1), Arrow IPC |
| Domain vision statement | Diffuse | CLAUDE.md + comprehensive-design.md collectively |
| Highlighted core | Diffuse | CLAUDE.md "Elder's rules (NEVER violate these in code)" section |
| Segregated core | Not yet | Possible refactor target post-#10 |
| Abstract core | N/A | |
| System metaphor | Rejected | No fitting metaphor |
| Responsibility layers | Possible eventual fit | Suggestive mapping to pipeline stages |
| Knowledge level | Rejected | No end-user configurable rules |
| Pluggable component framework | Rejected | Single application |

## 9. Antipatterns

Each entry is a specific failure mode this guide rejects. They are the mirror of §§3–6.

- **Anemic domain model.** Entities and value objects with only data, behavior in services. Sign: the methods on objects are mostly getters/setters; the work is done by `*Service` classes. Fix: move behavior to where the data lives (Tell, Don't Ask).
- **Everything is a service.** A reflexive `*Service` for every operation. The discipline is to ask whether the operation has a natural object home before reaching for a service.
- **Primitive obsession.** Passing money as `Decimal`, addresses as `dict`, scores as `tuple`. Fix: WHOLE VALUE — coherent groups of attributes are value objects.
- **Aggregate as god-object.** Aggregate root holds references to everything in the system, not just to objects whose invariants it must enforce. Fix: the aggregate is the *transactional consistency unit*, not "everything related to."
- **Cross-aggregate references through pointers.** Object A inside aggregate X holds a Python reference to object B inside aggregate Y. Fix: hold IDs, not references; resolve on demand through a repository.
- **Find-or-create.** Repository methods that paper over "is this new or existing?" Fix: two methods, two intentions.
- **Repository per class.** Repository for every entity, including ones that are members of an aggregate. Fix: one repository per aggregate root.
- **Transaction commits inside the repository.** Repository commits the unit of work. Fix: client owns the transaction; repository participates.
- **Smart UI alongside model-driven design.** UI widgets re-implement business rules for "speed." Fix: UI reads typed domain values; if it needs a derived field, the domain produces it.
- **Infrastructure-driven packaging.** One conceptual entity split across `data/`, `service/`, `facade/`, `interface/` packages. Fix: keep one concept in one module; use packages to separate domain from non-domain.
- **Leaky framework type.** ib_async's `Order`, httpx's `Response`, psycopg's `Row` returned to domain code. Fix: anticorruption layer translates at the boundary.
- **The naive metaphor.** Treating the domain model as "just a metaphor" rather than as the precise vocabulary of the project. Evans rejects this explicitly (p. 442).
- **Rule engine that fragments the model.** Static-data objects on one side; rules in a separate engine on the other. Fix: rules live in the model; if a rule engine is genuinely needed, the rules are domain objects implemented through the engine, not data plus separate scripts.
- **Refactoring without domain insight.** Renaming for elegance; introducing abstractions for technical virtuosity; deeper "models" the domain expert can't speak (p. 209).
- **Implicit business rule as guard clause.** `if voyage.capacity() * 1.1 < total: ...` rather than `OverbookingPolicy.is_allowed(...)`. Fix: name the rule.

## 10. Self-audit checklist

Run this against any domain change before declaring it done. Each item is binary; partial credit does not exist.

1. **Ubiquitous language.** Every public name in the change comes from Elder's vocabulary or is a candidate for it. No generic synonyms ("trend filter" instead of "Impulse System").
2. **Layer placement.** UI/Application/Domain/Infrastructure responsibilities are correct. Business rules live in the domain layer; coordination lives in the application layer.
3. **Entity vs. value.** Each new object is unambiguously one or the other. Value objects are `frozen=True`. Entities have a defined identity operation.
4. **Whole value.** Conceptually coherent attribute groups travel together. No three-string addresses, no four-`Decimal` proposals.
5. **Aggregate boundary.** The change identifies which aggregate it belongs to and which invariants it enforces. Cross-aggregate references are by ID.
6. **Factory atomicity.** New entities and aggregates are constructable only in valid states. Invariants are enforced at creation, not after.
7. **Repository scope.** Only aggregate roots have repositories. Repositories return typed objects, not `dict[str, Any]`.
8. **Intention-revealing.** Method names describe effects, not means.
9. **Side-effect segregation.** Queries and commands are separate. Methods that mutate don't return domain information.
10. **Specification for named rules.** Rules that get reused (filtering, validation, selection) are specifications, not free functions or lambdas.
11. **Bounded context honored.** The change does not silently mix Elder content and ADW infrastructure (per CLAUDE.md scope boundary). Cross-context references go through repositories or explicit translators.
12. **No leaky framework type.** No `httpx`, `ib_async`, `psycopg`, or `chromadb` types in the domain layer's public surface.
13. **Documented invariants.** New aggregate invariants are added to `docs/elder-invariants.md`. New binding tests assert them.
14. **Refactoring discipline.** The change either (a) implements an existing build-plan item or (b) makes an implicit concept explicit and the new concept has an Elder-vocabulary name.
15. **Domain expert test.** The change can be explained using only Elder's vocabulary, without reference to Python, asyncio, or ChromaDB.

A change failing any item is not finished, no matter how green its tests are.

## 11. What this guide does not cover

- **Strategic team organization.** Evans's chapters on team structures, knowledge partitioning, and "six essentials for strategic design decision-making" (chapter 17) presuppose a multi-team project. Elder is single-developer; the team-coordination patterns don't apply.
- **The full integration-pattern decision tree.** Evans gives detailed criteria for choosing between OPEN HOST SERVICE and PUBLISHED LANGUAGE, between SHARED KERNEL and CUSTOMER/SUPPLIER, etc. For Elder, only ANTICORRUPTION LAYER and CONFORMIST are live decisions; the rest are noted in §6.3 for completeness but not elaborated.
- **DDD for non-OO languages.** Evans's chapter 5 discusses Prolog and rules engines for domains that resist object-oriented modeling. Elder is solidly OO; this guide assumes Python with frozen dataclasses, `Protocol` types, and class methods.

## Sources

- Evans, E. (2003). *Domain-Driven Design: Tackling Complexity in the Heart of Software.* Addison-Wesley. The spine of this guide; all chapter and page citations refer to this book.
- `docs/modularity-guide.md` — Liskov-grounded modularity discipline; pre-requisite to several patterns here (level ownership, hierarchy, connection-as-assumption).
- `docs/elder-invariants.md` — the executable form of Evans's "assertions" (§5.3) for Elder's risk model.
- `docs/comprehensive-design.md` — Elder's architectural decisions; partially serves the role of Evans's "domain vision statement" (§6.4).
- CLAUDE.md "Design philosophy (apply to all new and refactored code)" — the operating rules that codify several patterns from this guide (Tell Don't Ask, domain events over shared mutable state, repositories per context, agents return typed output).
- Fowler, M. (2002). *Patterns of Enterprise Application Architecture.* Addison-Wesley. Cited by Evans for METADATA MAPPING LAYERS and QUERY OBJECT (§4.7).
- Cunningham, W. (1995). The WHOLE VALUE pattern. Cited by Evans for value-object cohesion (§4.2).
