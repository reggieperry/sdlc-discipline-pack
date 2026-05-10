---
paths:
  - "**/*.py"
---

> Full reasoning, citations (Evans 2003), and worked examples: `docs/ddd-guide.md`.
> Elder examples in this rule are illustrative — they show what DDD looks like
> in a non-trivial codebase. The principle applies across projects.

# DDD rules

## Vocabulary

- **Bounded context** — boundary inside which one model is coherent. Different from a module.
- **Entity** — defined by identity that persists across state changes (e.g., `Trade`, `Account`).
- **Value object** — defined by attributes; `frozen=True`; no identity (e.g., `Price`, `RiskAmount`, `ImpulseColor`).
- **Service** — stateless domain operation that doesn't fit on an entity or value.
- **Aggregate** — entity-and-value cluster with a single root; transactional consistency unit.
- **Factory** — encapsulates aggregate construction; enforces invariants at creation.
- **Repository** — collection-illusion over storage; one per aggregate root.
- **Specification** — predicate-shaped value object naming a domain rule.

## Layer placement

| Layer | What lives there | Examples |
| ----- | ---------------- | -------- |
| UI | Display, input | `dashboard/` |
| Application | Coordinate use cases; thin; no business state | Pipeline managers (planned) |
| Domain | Concepts, rules, business state | `agents/risk_agent.py`, `core/state.py`, `indicators/elder.py`, `risk_parameters.py` |
| Infrastructure | Technical capability | `core/llm.py`, `core/ib_*.py` (deferred), `knowledge/store.py`, `db/` |

The 2% Rule, 6% Rule, Triple Screen, Impulse System, Trade Apgar, ABC Rating, QARP composite — domain layer. Always.

## Entity vs. value

- **Entity** — has continuity across changes; identified by ID; mutable through methods that preserve invariants. Stripped to identity-establishing attributes plus essential behavior.
- **Value** — defined by attributes; `frozen=True`; never bidirectionally associated to another value (if you need that, one of them is an entity).
- **Whole-value rule** — conceptually coherent attribute groups travel together. Don't pass `entry_price`, `stop_price`, `share_count` as three primitives; pass a `TradeProposal`. Money types are `Decimal`, never `float`.

## Aggregates

- One root per aggregate. External code holds references to the root only. Internal entities have local identity.
- Cross-aggregate references are by ID, never by Python reference.
- All invariants of the aggregate are satisfied at every committed state.
- A single transaction commits one aggregate; cross-aggregate consistency is eventual.
- Only aggregate roots are queried directly through repositories.

Elder aggregates: `AccountState` (open positions + equity, enforces 2% and 6% Rules), `Trade` (filled, with entry/exit/fills/commissions), `Run` (pipeline execution + decisions + costs), `TradeProposal` (constructable only through `account.evaluate_proposal`).

## Factories

- Atomic creation: invariants enforced at construction, not after.
- Use a constructor only when: no hierarchy, no polymorphism, simple inputs, no multi-step assembly.
- Aggregate creation goes through factory methods on the root or standalone factory objects, never client-side assembly.
- Reconstitution from storage preserves identity (does not assign new) and handles invariant violations explicitly.

## Repositories

- One repository per aggregate root. Not per class.
- Returns typed domain objects; never `dict[str, Any]` or framework rows.
- No `find_or_create` — two methods, two intentions.
- Transaction control belongs to the client (the application service), not the repository.
- Specification-based queries (`repo.satisfying(spec)`) for reusable domain rules.

## Services

- Stateless. Operates on entities and value objects.
- Used judiciously; don't strip behavior from objects ("everything-is-a-service" antipattern).
- Three layers: domain service (business rules), application service (coordination), infrastructure service (technical).

Domain services in Elder: risk-gate sequence (gates 1–7 against a proposal), QARP composite scoring, momentum ranking. Each is a real domain operation that doesn't sit on a single entity.

## Supple-design discipline

- **Intention-revealing names.** `account.evaluate_proposal(p)` over `account.run_all_checks(p)`.
- **Side-effect-free queries.** Methods that mutate don't return domain information; methods that return domain information don't mutate.
- **Closed operations** on value objects: `Price.plus(Price) -> Price`, `Specification.and_(Specification) -> Specification`. Compose declaratively.
- **Standalone classes** for the most computation-heavy concepts. `indicators/elder.py` is the model — minimal dependencies.
- **Specifications** for named, reusable rules. Use them in selection (`repo.satisfying(...)`) and validation.

## Bounded contexts

- **Elder content** vs **ADW infrastructure** is the standing context boundary (CLAUDE.md "Scope"). Never mix in one commit; never let Elder code import from `adws/`.
- **Knowledge context** has its own vocabulary (corpus, embedding, similarity); accessed only through `LessonRepository` / `SimilarTradeRepository`.
- **Execution context** (when item #12 lands) wraps IB. Anticorruption layer translates `ib_async` types to domain types.

## Anticorruption layer (for external systems)

When integrating with a system whose model differs from Elder's:

1. FACADE in the external system's terms (simplifies access).
2. ADAPTER per service in your model (semantic conversion).
3. Translator (lightweight, stateless).

Domain code never sees `httpx.Response`, `ib_async.Order`, `chromadb.Collection`, or `psycopg.Row`. Translation happens at the boundary.

Exception — **Conformist** when the external model is small, well-designed, and clearly the senior partner: Anthropic SDK is conformist. Don't build an ACL over a clean SDK; just wrap it thinly.

## Antipatterns (refuse on sight)

- **Anemic domain model** — data classes plus `*Service` for everything. Behavior goes where data lives.
- **God-object aggregate** — root holds references to everything in the system. Aggregate is the *transactional* unit, not "everything related to."
- **Cross-aggregate Python references** — A inside aggregate X holds a reference to B inside aggregate Y. Use IDs.
- **Repository per class** — every entity gets one. Aggregate roots only.
- **Find-or-create** — papers over a real domain distinction.
- **Repository commits** — repository commits the unit of work. Client owns the transaction.
- **Leaky framework type** — `httpx.Response`, `ib_async.Order`, `psycopg.Row`, `chromadb.QueryResult` in domain code.
- **Primitive obsession** — money as bare `Decimal`, address as `dict`. Use whole-value objects.
- **Implicit business rule as guard clause** — `if voyage.capacity() * 1.1 < total: ...`. Name it (`OverbookingPolicy.is_allowed(...)`).
- **Smart UI alongside model-driven design** — UI re-implements rules for "speed."
- **Infrastructure-driven packaging** — one concept split across `data/`/`service/`/`facade/` packages.

## Self-audit (binary; partial credit does not exist)

1. Public names use the project's ubiquitous language (the project overlay supplies the vocabulary list). No generic synonyms where a domain term exists.
2. New objects are unambiguously entity or value object. Values are `frozen=True`.
3. Whole-value: conceptually coherent attribute groups travel together.
4. Aggregate boundary identified; invariants stated; cross-aggregate references by ID.
5. Factory atomicity: invariants enforced at construction.
6. Repositories on aggregate roots only; return typed domain objects.
7. Intention-revealing names; queries and commands segregated.
8. Reusable rules are specifications, not free functions.
9. No leaky framework types in the domain layer's public surface.
10. New aggregate invariants added to the project's invariants catalog (where the project keeps named invariants tied to concrete tests) with binding tests.
11. Bounded-context boundary honored (no cross-context mixing in one commit).
12. The change can be explained using only the project's domain vocabulary.

A change failing any item is not finished, no matter how green its tests are.
