---
paths:
  - "**/*.py"
  - "**/*.go"
  - "**/*.sh"
  - "**/tests/**"
---

# Domain modeling

Building code around a rigorous model of the problem domain. Source: Eric Evans, *Domain-Driven Design* (2003). Calibrated for a modest domain — favor the tactical and supple-design rules, and use bounded contexts as a primary guide to module boundaries (below), not as enterprise-only machinery. Scale the heavy strategic artifacts (a formal context-map document, a full anticorruption layer, the complete specification algebra) to need.

> Full reasoning, citations, and worked examples: `.claude/sdlc-discipline/guides/ddd-guide.md`.
> This rule states the language-agnostic discipline and defers idiom to the language overlay. See `craft-abstraction.md` for value objects as abstract data types, and the language overlay (`go-types.md` / `go-modules.md`; the `python-types.md` / `python-modules.md` set) for encoding them and for packages as domain modules.

## Vocabulary

- **Bounded context** — the boundary within which one model is coherent and every term means exactly one thing. Different from a module (below).
- **Entity** — defined by an identity that persists across state changes (e.g., `Account`, `Order`).
- **Value object** — defined by its attributes; immutable; no identity (e.g., `Money`, `Quantity`, `DateRange`).
- **Service** — a stateless domain operation that doesn't fit naturally on an entity or value object.
- **Aggregate** — an entity-and-value cluster with a single root; the unit of transactional consistency.
- **Factory** — encapsulates aggregate construction; enforces invariants at creation.
- **Repository** — a collection-illusion over storage; one per aggregate root.
- **Specification** — a predicate-shaped value object naming a domain rule.

## Ubiquitous language and model-driven design

- **Build one shared vocabulary spanning speech, docs, and code; the model is the backbone of that language.** Make code names match domain terms exactly, and rename when a term shifts — a change in the language *is* a change to the model.
- **Let the model drive the design literally, so the mapping from model to code is obvious.** A model that doesn't guide implementation is worthless paper; whoever writes the code is modeling.
- **Push knowledge-rich behavior and rules into the model, not just data** — a domain model is not a database schema. Isolate domain logic in its own layer, free of transport, persistence, and orchestration.

## Layer placement

| Layer | What lives there |
| ----- | ---------------- |
| UI | Display, input |
| Application | Coordinate use cases; thin; no business state |
| Domain | Concepts, rules, business state |
| Infrastructure | Technical capability (clients, stores, transports) |

Business rules and policies — the constraints that define correct behavior in the domain — live in the domain layer, without exception. A rule that has migrated into a UI handler or an infrastructure adapter is misplaced.

## Tactical building blocks

- **Model anything with identity over time as an Entity** — define identity explicitly (an `ID` field), keep the type spare (identity-establishing attributes plus essential behavior), and don't lean on language `==` or pointer identity for domain identity. Entities are mutable through methods that preserve their invariants.
- **Model anything defined purely by its attributes as a Value Object — immutable and side-effect-free.** Make its attributes a conceptual whole and replace the whole rather than mutate a part. A value object is never bidirectionally associated to another value object — if you need that, one of them is really an entity. Value objects are typically the bulk of a domain; this is where suppleness pays off.
- **Whole-value rule: conceptually coherent attribute groups travel together.** Don't pass three loose primitives (`unit_price`, `quantity`, `currency`) where one value object (`LineItem`) carries the concept. Money is a dedicated type backed by a decimal representation, never a binary float.
- **Express an operation that isn't naturally a thing as a Service** — stateless, verb-named, defined in domain terms, with domain objects for parameters and results. Forcing an action onto an entity distorts it; "Manager" objects are a smell.
- **Organize code into Modules named in the ubiquitous language; let the boundaries emerge from the model, not from technical tiers.** Modules are the chapters of the domain's story — group the classes you want the next reader to think about together, and seek low coupling as *concepts that can be reasoned about independently*, not a metric over imports. Refine the model until it partitions along high-level domain concepts; resist infrastructure-driven packaging (a tier per layer, data split from the behavior that operates on it) that fragments a conceptual object and robs cohesion. Let modules coevolve with the model rather than freezing the early guess. The outer boundary that contains these modules is the bounded context (below).
- **Cluster entities and values into Aggregates with one root and a consistency boundary; enforce invariants at every commit** — but only where a real invariant spans objects. Outside objects reference the root only.
- **Use a Factory to encapsulate complex creation** (produce a whole aggregate atomically, invariants satisfied or fail loudly) — but prefer a plain validating constructor when construction is simple.
- **Provide a Repository only for aggregate roots that need global access; make it act like an in-memory collection that hides persistence**, and leave transaction control to the caller.

## Aggregates

- One root per aggregate. External code holds references to the root only; internal entities have local identity.
- Cross-aggregate references are by ID, never by in-memory reference.
- All invariants of the aggregate are satisfied at every committed state.
- A single transaction commits one aggregate; cross-aggregate consistency is eventual.
- Only aggregate roots are queried directly through repositories.

Example aggregates: an `Account` (open positions plus balance, enforcing the balance and exposure invariants); an `Order` (placed, with line items, fills, and fees); a `Run` (one execution plus its decisions and costs). A constructable-only-through-validation type — e.g., an `ApprovedProposal` that can only be produced by passing the account's own check — is an aggregate whose factory is the validation method.

### Consistency boundary and aggregate sizing

Pick the aggregate by naming the multi-row invariant it must defend, then size the transaction to exactly that boundary. For each persistence function, write one line of doc:

> Aggregate: `<name>`. Invariant: `<plain English>`. Boundary: `<lock or constraint that enforces it>`.

If several persistence functions share an aggregate, lock the parent once at the orchestrator and pass the locked context down — don't lock per call. Wrong size shows up as deadlocks (boundary too big) or write skew (boundary too small). See `decoupling.md` and the language concurrency overlay (`go-concurrency.md` / `python-concurrency.md`) for the defense catalog.

## Factories

- Atomic creation: invariants enforced at construction, not after.
- Use a plain constructor only when: no hierarchy, no polymorphism, simple inputs, no multi-step assembly.
- Aggregate creation goes through factory methods on the root or standalone factory objects, never client-side assembly.
- Reconstitution from storage preserves identity (does not assign a new one) and handles invariant violations explicitly.

## Repositories

- One repository per aggregate root. Not per class.
- Returns typed domain objects; never an untyped map/`dict[str, Any]` or framework rows.
- No `find_or_create` — two methods, two intentions.
- Transaction control belongs to the client (the application service), not the repository. The repository does not commit the unit of work.
- Specification-based queries (`repo.satisfying(spec)`) for reusable domain rules.

## Services

- Stateless. Operates on entities and value objects, named by a verb in domain terms.
- Used judiciously; don't strip behavior off objects (the "everything-is-a-service" antipattern).
- Three layers: domain service (business rules), application service (coordination), infrastructure service (technical capability). A domain service is a real domain operation that doesn't sit on a single entity — e.g., a multi-gate validation sequence applied to a proposal, or a ranking computed across a set.

## Supple design

- **Name every type, method, and argument by its effect and purpose, never its mechanism** — an intention-revealing interface. `account.evaluate_proposal(p)` over `account.run_all_checks(p)`. If a client must read the internals to use it, encapsulation is lost.
- **Put as much logic as possible into side-effect-free functions** that return a result without changing state, and strictly **separate commands from queries** — methods that mutate don't return domain information; methods that return domain information don't mutate. Side-effect-free functions compose safely.
- **State post-conditions and invariants as assertions** (encode them as tests where the language can't express them) — assertions describe state, so they're analyzable without tracing execution.
- **Factor intricate computation into standalone, dependency-free types understandable in isolation; prefer closure of operations** (a return type matching the argument type) where it fits: `Money.plus(Money) -> Money`, `Specification.and(Specification) -> Specification`. Compose declaratively. **Decompose along the domain's conceptual contours**, not by uniform grain — align module boundaries with the domain's real axes of change.

## Making implicit concepts explicit

- **When a constraint, process, or rule distorts its host object, promote it to a first-class object.** A rule buried in a guard clause can't be discussed or reused.
- **Express a rule that tests an object as a Specification** — a predicate-like value usable for validation, selection, and creation — but implement only the combinators (AND/OR/NOT) you actually use.

## Commands and events — different failure semantics

Tag each step in a workflow as `COMMAND` or `EVENT`. They are not interchangeable:

- **Command** runs user-visible work. It may raise; failure aborts the surrounding flow and bubbles to the caller. Examples: place an order, charge a payment, transition a status.
- **Event** does bookkeeping or notification. It must not abort the surrounding command on failure. Examples: write to an audit log, refresh a derived view, notify a downstream listener.

No message bus is required to apply the discipline — route the exception handling explicitly. Commands re-raise into the runner's failure path. Events log-and-continue and never abort the surrounding command. The test must drive each event-handler failure and assert the command still succeeds.

## Payloads stay simple value objects

Every event, command, and inter-stage payload is a frozen/immutable value object — fields and equality, no behavior. Transformations belong in handlers or normalizer functions, not as methods on the value. Keeping payloads simple means they can be logged-and-replayed, serialized across process boundaries without ceremony, and constructed as test fixtures with minimal setup.

If a payload starts growing helper methods (`payload.normalize()`, `payload.with_attempt(n)`), extract them to module-level functions. Builders for tests return fully-valid payload values; see `craft-tdd.md` and `craft-xunit.md` for the builder pattern.

## Bounded contexts and the module boundary

- **A Bounded Context is the boundary within which one model — and every term in it — stays coherent and means exactly one thing.** It is the outermost and hardest modularity boundary: decide the context seams first, then partition inside each. A context boundary is wherever the model's meaning changes — an external system you don't control, a separate subsystem or team, a distinct physical model (code base, schema).
- **Draw the hard module boundary at the context seam and put explicit translation across it; only inside one context do you split into modules for cognitive load.** Model-driven design is context-bounded — work with one model within any single context, and don't force the whole system into one model.
- **Heed Evans's precision: bounded contexts are not modules.** Modules also organize elements *within* a context and don't by themselves signal a context change, and naive module-splitting can *hide* accidental fragmentation — the same concept duplicated, or one name meaning two different things (false cognates). When a module boundary is really a context boundary, it needs translation across it, not a shared import.
- **A system that integrates with models it doesn't own has a real context seam at each — identifying them is load-bearing even at small scale.** An external platform, a separate data store, a different target system, the LLM's own output schema: make each a hard module boundary with an explicit translation type, rather than letting a foreign model leak in. Only the heavy *artifacts* (a formal context-map document, a full anticorruption layer) scale with the number of contexts.
- **The real hazard at modest scale is the opposite of over-application: missing a context seam and fusing two models into one**, which produces exactly those duplicate concepts and false cognates. Scale the heavy strategic artifacts to the domain's size — but never skip drawing the boundary.
- **Never mix two contexts in one commit, and never let one context import the other's internals.** A distinct subsystem with its own vocabulary (e.g., a knowledge/search context with its own terms — corpus, embedding, similarity) is reached only through its repository interface, not by reaching into its types.

## Anticorruption layer (for external systems)

When integrating with a system whose model differs from yours:

1. FACADE in the external system's terms (simplifies access).
2. ADAPTER per service, in your model (semantic conversion).
3. Translator (lightweight, stateless).

Domain code never sees a raw HTTP response, a broker/exchange API object, a database driver row, or a search-engine result type. Translation happens at the boundary. See `decoupling.md` for adapter placement.

**Exception — Conformist** when the external model is small, well-designed, and clearly the senior partner: a clean, well-modeled SDK is conformist. Don't build a full ACL over it; wrap it thinly. (A model SDK / LLM client is typically conformist — see `craft-tdd.md` and the language LLM overlay, `go-llm.md` / `python-llm.md`.)

## Antipatterns (refuse on sight)

- **Anemic domain model** — data classes plus a `*Service` for everything. Behavior goes where the data lives.
- **God-object aggregate** — the root holds references to everything in the system. An aggregate is the *transactional* unit, not "everything related to."
- **Cross-aggregate in-memory references** — an object inside aggregate X holds a reference to an object inside aggregate Y. Use IDs.
- **Repository per class** — every entity gets one. Aggregate roots only.
- **Find-or-create** — papers over a real domain distinction.
- **Repository commits** — the repository commits the unit of work. The client owns the transaction.
- **Leaky framework type** — a raw HTTP response, a broker API object, a driver row, or a search-result type in domain code.
- **Primitive obsession** — money as a bare number, an address as a map/`dict`. Use whole-value objects.
- **Implicit business rule as a guard clause** — `if capacity() * 1.1 < total: ...`. Name it (`OverbookingPolicy.is_allowed(...)`).
- **Smart UI alongside model-driven design** — the UI re-implements rules for "speed."
- **Infrastructure-driven packaging** — one concept split across `data/` / `service/` / `facade/` packages. Evans calls partitioning by technical tier or by pattern type an error.

## Translating to the target language

The building blocks land differently per language — value objects as immutable constructed types, entities as types with an explicit identity field, repositories as a narrow interface over the store, services as stateless functions or focused modules, modules as packages or namespaces named in domain terms (never partitioned by technical layer or by pattern type). The per-language overlay carries the concrete idiom (`go-types.md` / `go-modules.md` for Go; the `python-types.md` / `python-modules.md` set for Python). At modest scale the load-bearing pieces are value objects plus constructor validation, the repository interface over the store, intention-revealing names, command/query separation, and drawing boundaries along context seams; elaborate aggregates, factories beyond a validating constructor, the full specification algebra, and the heavy strategic artifacts scale to need.

## Self-audit (binary; partial credit does not exist)

1. Public names use the project's ubiquitous language (the project overlay supplies the vocabulary list). No generic synonyms where a domain term exists.
2. New objects are unambiguously entity or value object. Value objects are immutable.
3. Whole-value: conceptually coherent attribute groups travel together.
4. Aggregate boundary identified; invariants stated; cross-aggregate references by ID.
5. Factory atomicity: invariants enforced at construction.
6. Repositories on aggregate roots only; return typed domain objects; client owns the transaction.
7. Intention-revealing names; queries and commands segregated.
8. Reusable rules are specifications, not free functions.
9. No leaky framework types in the domain layer's public surface.
10. New aggregate invariants added to the project's invariants catalog (where the project keeps named invariants tied to concrete tests) with binding tests.
11. Bounded-context boundary honored (no cross-context mixing in one commit; no cross-context import of internals).
12. The change can be explained using only the project's domain vocabulary.

A change failing any item is not finished, no matter how green its tests are.
