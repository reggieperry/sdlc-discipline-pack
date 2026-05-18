# Modularity guide

A principal-engineer reference for designing and auditing levels of abstraction. The thesis is Liskov's: reliability is set by the design before any code is written. Modularity — done in the specific way Liskov defines it — is the lever. The spine of this guide is *Guidelines for the Design and Implementation of Reliable Software Systems* (Liskov, MITRE ESD-TR-72-164, February 1973), supplemented by Parnas's connection rule and modern realizations of the same idea in ML-family module systems and Scala. Where existing project rules cover a point, this guide cites them rather than restating.

A vocabulary note before anything else. Liskov uses "module" in a narrow sense — one named code expansion in a structured program, roughly one routine fitting on a printout page (page 6, footnote). Her unit of *design* is the **level of abstraction**: "one or more externally accessible functions which share common resources" (page 5). Modern usage of "module" usually means a Python file or package, which sits closer to her *level* than to her *module*. This guide uses **level** for the design unit and **module** for the file or package, and is explicit when the two diverge.

## 1. Foundations

### 1.1 Liskov's premise

The 1973 *Guidelines* opens (page 1) with the Dijkstra claim: "Testing shows the presence, not the absence of bugs." Liskov takes that as the starting point. If exhaustive testing is the only reliability gate but exhaustive testing of a complex system is impossible, the only path to reliable software is a design that makes exhaustive testing tractable. That requires few enough relevant test cases that all of them can actually be run. Decomposition determines the count.

Liskov gives three explicit requirements for good modularization (page 8):

1. The system is divided into a hierarchy of levels of abstraction. Connections in control are limited by the hierarchy rule. Connections in data are limited to explicit arguments and return values.
2. The combined activity of the functions in a level supports a single abstraction *and nothing more*.
3. The system structure is logically clear, understandable, and expressed by a structured program.

The first is a connection-shape rule. The second is a cohesion rule — one abstraction per level, not "scanning and analysis," not "files plus emails." The third is an expressibility rule: the structure must be representable as a program, not as informal commentary alongside one.

### 1.2 Parnas's framing of connections

Liskov adopts Parnas's definition (page 3): "The connections between modules are the assumptions which the modules make about each other." Connections are not the function calls visible at the call site; they include every fact that, if changed in one module, would force a change in the other. Information hiding is the discipline of arranging levels so the assumptions cross the boundary as a small, stable surface.

The corollary that drives most level-design decisions: **hide what is likely to change**. A change in a hidden detail costs one level rewrite. A change in an exposed assumption costs every caller.

### 1.3 The reliability arithmetic

Liskov's quantitative case for the discipline (pages 8–9):

> "The number of relevant test cases for two levels equals the *sum* of the relevant test cases for each level, not the product."

Levels that are logically independent — connections only through explicit arguments and returns, no shared mutable state across boundaries — multiply additively rather than combinatorially. This is the formal reason a well-modularized system is testable and a poorly-modularized one isn't. A reviewer who claims "this design is fine, we'll catch bugs in testing" is implicitly making a claim about combinatorial test budget that the arithmetic contradicts.

Practical consequence: every shared-mutable-state escape — a global, a god-object, a passed-around dict, an `os.environ` read deep in a worker — converts an additive cost into a multiplicative one. The discipline on test budget is to never introduce one without naming what it is worth.

### 1.4 Modern-language realization

The ML family (SML, OCaml) made Liskov's idea a first-class language feature with signatures, structures, and functors — a parameterizable algebra of levels distinct from the term language. Scala 3 reaches the same conclusions through traits and mixin composition. The points that transfer to Python:

- A **signature** (in Python: a `Protocol`) names the assumptions of a connection. A consuming level depends on the signature, not the implementation. This is the modern descendant of Liskov's level and function specifications (§7).
- A **functor** is a function from levels to levels — a level whose dependencies are explicit parameters rather than ambient imports. The Python analog is a class whose constructor takes `Protocol`-typed dependencies and references nothing else from the surrounding scope. A *closed* functor (referring only to its parameters) is maximally reusable because every assumption is on the surface.
- **Composition over inheritance** as Scala expresses it: traits are the primary decomposition tool; classes appear only at the leaves. Inheritance for code reuse is rejected. Inheritance for substitutability — one type genuinely standing in for another — is accepted only when the substitutability is real (§11).

These are restatements of Liskov, not extensions. Her 1973 vocabulary ("levels," "functions," "specifications") and ML's 1980s vocabulary ("structures," "values," "signatures") describe the same shape.

## 2. The kinds of useful abstraction

Liskov's actual taxonomy from Section III of the *Guidelines* (pages 9–11). Each entry below is a *reason* to introduce a new level. A proposed level that fits none of these is probably not a level; it is a grab bag.

The umbrella reason (page 9): an abstraction expresses *what* is being done without specifying *how*. Every category below is a specialization of that.

### 2.1 Resource abstractions

Liskov: "Every hardware resource available on the system will be represented by an abstraction having useful characteristics for the user. This abstraction will be supported by a level of abstraction whose functions map the characteristics of the abstract resource into the characteristics of the real underlying resource or resources" (page 10).

In a 2026 system, "hardware resource" generalizes to any external substrate: network, broker connection, LLM endpoint, database, file system, message bus.

- The level owns the resource exclusively. No other level talks to the same socket, file handle, or pool.
- Exposed operations are domain verbs (`fetch_holdings`, `place_order`, `embed_lesson`), not transport mechanics.
- Failures are translated into domain exceptions at the boundary. Callers do not see `httpx.ConnectError` or `psycopg.OperationalError`.

In Elder: `core/ib_*.py` (when built) wraps `ib_async`. `core/llm.py` wraps `httpx`. `knowledge/store.py` wraps ChromaDB and Postgres.

### 2.2 Abstract characteristics of data

Liskov: "In most systems the users are interested in the structure of data rather than (or in addition to) storage of data. The system can satisfy this interest by the inclusion of a level of abstraction which supports the chosen data structure" (page 10).

The hidden information is layout, encoding, normalization. The exposed surface is a value type with operations that preserve invariants.

- A data abstraction is constructed only through validated entry points. The dataclass is `frozen=True` by default; mutation goes through methods that re-establish invariants.
- The internal layout (which field stores what, what units, what encoding) does not appear in any caller.
- Equality, ordering, hashing, and serialization are operations on the abstraction, not on its fields.

In Elder: `AccountState` (validates the 2% and 6% rules through `evaluate_proposal`); `ApprovedTrade` (constructable only through validation); the indicator dataclasses in `indicators/elder.py`.

### 2.3 Simplification of levels

Liskov treats simplification as a parent category (page 10) with two distinct sub-techniques. They are listed separately below to avoid confusion, but they share a goal: reduce what a level needs to know.

#### 2.3.1 Generalization via common functions

Liskov: "One candidate for a level of abstraction is a function (or group of functions) which is obviously going to be generally useful. Separating such groups is a common technique in system implementation and is also useful for error avoidance, minimization of work, and standardization" (page 10).

Extract a function that two or more levels need so they share the implementation. The hidden information is the algorithm; the exposed surface is the signature.

The trap: premature generalization is the most common failure mode here. Three similar implementations are better than one premature abstraction. Wait until the third call site before generalizing — the first two teach you the shape, the third confirms it.

In Elder: `risk_parameters.py` is the canonical generalization. `indicators/elder.py` generalizes across all tickers and timeframes.

#### 2.3.2 Limiting information

Liskov: "Another way of simplifying levels of abstraction is to limit the amount of information which they need to know (or even have access to). … This knowledge can be successfully hidden within a lower level of abstraction whose functions will provide requested information to higher levels when called; note that the data in question becomes a resource of the lower level" (pages 10–11).

The hidden information is *complexity that the higher level must not see*, even if it could in principle handle it. Information that becomes a resource of the lower level is, by the hierarchy rule, inaccessible to higher levels except through explicit calls.

- A risk gate is information-limiting: callers see `evaluate_proposal(proposal) -> ApprovedTrade | RejectedTrade`, not the seven-stage gate sequence inside.
- A scanner is information-limiting: callers receive `ScanCompleted(events: list[ScanEvent])`, not the iteration over the universe, the per-ticker failures, or the partial-result policy.

This is the most powerful and most under-used technique. When in doubt, push knowledge down so it becomes a lower level's resource.

### 2.4 System maintenance and modification

Liskov: "Producing a system which is easily modified and maintained is one of the primary goals of the project. This goal can be aided by separating into independent levels of abstraction functions which are performing a task whose definition is likely to change in the future" (page 11).

This is Parnas's information-hiding rule operationalized as a level boundary, with change as the criterion for what to hide.

- Identify what is expected to change: the LLM provider, the broker API, the storage backend, an indicator parameter set, a fee schedule.
- Place the change behind a level whose signature is stable across the expected variations. The signature is the contract; the implementations swap behind it.
- Test the boundary by writing a second implementation, even a trivial one (an in-memory store, a fake broker, a mock LLM). If the second implementation forces changes to the signature, the signature was leaking.

In Elder: `LessonRepository` and `AccountRepository` Protocols (with `InMemory*` second implementations); `core/llm.py`'s thin wrapper; the deferred design intent for the indicator math (Python now, Rust via PyO3 if needed later) — the boundary lives at `indicators/elder.py`'s public functions.

## 3. Levels of abstraction and the hierarchy rule

Liskov's two rules governing levels (page 4):

> "The first concerns resources … each level has resources which it owns exclusively and which other levels are not permitted to access. The second involves the hierarchy: lower levels are not aware of the existence of higher levels and therefore may not refer to them in any way."

Connections are constrained correspondingly (page 5):

- Control: each function has one entry point and exits to the place from which it was invoked (no `goto`); the hierarchy rule is observed.
- Data: explicit arguments and returned values only between two levels. Implicit interaction on common data may only occur among functions in the same level.

In Elder, the levels are:

| Level | Implementing files | Owns |
| ----- | ------------------ | ---- |
| Domain primitives | `core/state.py`, `risk_parameters.py`, `indicators/elder.py` | Types, math, constants |
| Resource and data abstractions | `core/llm.py`, `core/ib_*.py` (deferred), `knowledge/store.py`, `db/` | External-system access |
| Workers | `agents/scanner_agent.py`, `agents/analysis_agent.py`, `agents/risk_agent.py`, `agents/diary_agent.py` | One pipeline-stage decision |
| Domain managers (planned) | `ScanManager`, `TradeManager`, `ReviewManager` | Coordination of one phase |
| Pipeline primitives | `core/pipeline.py`, `core/events.py`, `core/agent.py` | Orchestration |

A level may be implemented by one or several Python files. The level is the design unit; the file is an implementation detail. What matters is that the resources and connections of the level are defined as a single thing.

The hierarchy rule says: `risk_parameters.py` knows about no other Elder file. `indicators/elder.py` knows about `risk_parameters.py` only if it needs Elder's published thresholds, and about nothing else. Workers know about primitives and resources. Managers know about workers. Pipeline primitives know about the abstractions they coordinate but not about Elder's specific workers.

**Constants and tunables are data resources.** A constant declared at module top, a frozen dataclass holding configuration, an env-var override resolved at module load — all are data resources in Liskov's sense, owned by the level whose abstraction they parameterize. The level-ownership rules apply unchanged: another level wanting the value calls a function, it does not import the constant. A "shared constants module" imported by multiple levels is implicit common data crossing a level boundary, the connection violation Liskov rules out at page 5.

The current `import-linter` contract enforces a subset of this. The full hierarchy should be encoded explicitly. Any cycle in the import graph is a design failure, not a code-style nit.

## 4. Asynchronous systems: the cooperating-processes model

Liskov's special guideline for systems that handle asynchronous events (pages 11–12). Elder's pipeline is exactly such a system, so this section maps her recommendation onto Elder's architecture directly.

The recommended structure: a low-level *system nucleus* provides synchronization primitives — at minimum P/V on semaphores, more usefully queues or mailboxes (page 12). Higher levels are organized as cooperating processes that communicate via these primitives. Within a level, control passes by call. Between levels, control may pass by call *or* by process synchronization.

> "Within a level, however, probably only calls are legal, for the reason that the decision to change processes is a major one and probably coincides with a change in level." (page 12)

In Elder:

- The **system nucleus** is the asyncio event loop plus `core/events.py` (the typed event bus). Synchronization primitives are `asyncio.Queue`, `asyncio.Event`, and the typed `EventBus` interface.
- The **main process** is one cooperating process: pipeline orchestration, agent coordination, IB connections, LLM calls, the stop monitor.
- The **worker pool** is a community of cooperating processes (the `ProcessPoolExecutor`). They communicate with the main process via queue-shaped boundaries — Arrow IPC for data, futures for completion.
- Within a level, calls only. Between levels, the boundary is typed events on the bus or queue submissions to the executor.

Three rules from this guideline, each of which Elder's `.claude/rules/project/concurrency.md` already encodes mechanically:

1. **No shared mutable state across processes.** Workers receive Arrow IPC bytes and return plain dicts. Pickled DataFrames, shared globals, and `multiprocessing.Manager`-style proxies are connection violations: they break the rule that data crossing a level boundary is explicit and serialized.
2. **Synchronization at level boundaries, not within them.** A worker function has no `asyncio` and no inter-worker locks. The main loop's coroutines synchronize through the event bus and `await`, not through ad-hoc shared state.
3. **The decision to cross processes coincides with a change of level.** Don't fan out to workers from inside a level just for parallelism — fan out at the boundary where one level (orchestration) hands off to another (computation).

This section is the underlying *why* — the rule is not a Python idiom, it is Liskov's reliability discipline applied to a 38-core dual-Xeon.

## 5. Module shape

A *level* may be implemented by one or several Python *modules* (files or packages). The rules below govern the module — the implementation unit — within a level.

### 5.1 Size and cohesion

There is no honest line-count rule for modules — but there is a cohesion rule. A module is well-sized when its name is the most precise label for its contents. If the most precise label is a conjunction ("scanning and analysis", "orders and stops"), split the module.

Concrete heuristics:

- A module exporting more than seven public names probably hides at least two abstractions. Audit the exports; group by which call sites use which names; consider splitting.
- A module under 30 lines is suspect on the other end. Either it is the right size for a small abstraction (a Protocol declaration, a value type) or it is a fragment that belongs in a parent module.
- The 25-line function rule from `python.md` operates inside the module. The module-level analog is the seven-export ceiling above.

Liskov's structured-programming size rule (page 6, citing Mills): individual functions ("modules" in her vocabulary) should fit on one printout page. The 25-line ceiling is the modern restatement.

### 5.2 Public surface

The public surface is the set of names a module exports, plus the types of those names. Treat it as a contract.

- Use a leading underscore on every name that is not part of the contract. Linters and tools respect this; humans should too.
- Prefer narrower return types over wider ones. Returning `ScanCompleted` is a stronger contract than returning `dict[str, Any]`.
- Do not export mutable module-level state. If a module needs configuration, take it as a constructor parameter or read it once at module load into a `Final[...]`-annotated value or frozen dataclass. Read-once-at-load is the module's *level-initialization function* in Liskov's sense (§IV problem 1, page 18): each level's resources, including configuration values, are initialized once at the level's entry, not resolved on each call.
- Do not re-export the names of dependencies. A module that imports `Decimal` from `decimal` and then exposes `Decimal` to its callers has widened its surface for no reason.

### 5.3 Naming the connection

The module's name and its public names together describe what callers may assume. Read your own module's `__init__` line by line and ask: if a caller saw only this, would they understand what the module promises? If they would have to read the implementation to predict behavior, the surface is leaking.

## 6. Information hiding and connection design

### 6.1 The connection-as-assumption rule

For any pair of levels A and B where A depends on B, write down what A assumes about B. The assumption set includes:

- The names B exports
- The types of those names
- The pre- and post-conditions B promises (what B requires of its inputs; what it guarantees of its outputs)
- The exceptions B may raise
- B's side effects, if any (writes to disk, log lines, metric emissions)
- B's performance envelope, if A relies on it (sync vs async, blocking vs non-blocking, latency budget)

If this list is long, the connection is fat. Fat connections are where bugs live and where refactoring stalls. Reduce by:

- Replacing return types with narrower ones
- Removing optional arguments by splitting into two functions
- Eliminating side effects by returning data and letting the caller act
- Moving exception translation into B so A sees only domain errors

### 6.2 What to hide

Parnas's rule, as Liskov adopts it: hide every decision likely to change. Practical filters:

- Hide vendor-specific protocols (broker API, LLM API, database client). The system should be willing to swap any of these without rippling.
- Hide encoding and storage layout. Callers see typed objects; the storage level decides JSON vs Postgres rows vs Arrow IPC.
- Hide algorithms behind their signatures. EMA computation is hidden inside `compute_ema`; the signature names what is computed, not how.
- Hide mutable state. Mutability inside a level is acceptable; mutability across a level boundary is a connection bugs travel along.

### 6.3 What not to hide

Hiding has costs: indirection, harder navigation, slower comprehension. Do not hide:

- Domain invariants the system depends on for correctness. The 2% Rule is hidden inside `AccountState.evaluate_proposal` *as enforcement*, but it is named explicitly in `risk_parameters.py` and documented in `docs/risk-model-overview.md`. The hiding is one layer; the visibility is another.
- Stable infrastructure. `pathlib.Path`, `Decimal`, `datetime` — wrapping these adds a layer with no upside.
- The shape of typed events. Events are the connection. Their fields are part of the contract by design.

## 7. Specifying levels and functions

Liskov gives explicit specification templates (pages 19–20). They are old in style but the contents map directly to information modern reviewers want when accepting a level.

### 7.1 Level specification

Every level should have a written specification covering, in Liskov's enumeration:

1. The name of the level.
2. A description of the abstraction it supports — one sentence, *what* not *how*.
3. The hardware or external resources it owns, if any.
4. The data resources it owns, including the data that holds its state.
5. Its placement in the hierarchy and whether it occupies its own process.
6. The functions of the level that are externally accessible.
7. The functions of the level that are internally accessible (likely not known until implementation).

In Elder, this is the role the specs in `specs/*.md` should play. Today the specs cover (1), (2), and partial (6). They should be extended to cover the rest. A level whose resources, hierarchy placement, and process assignment are not written down has not been designed; it has been coded.

For modules outside `specs/*.md` — typically `adws/`, `scripts/`, internal helpers without a separate spec — the module's top-level docstring serves as the level specification. It enumerates owned data resources (constants, env-var-derived tunables, frozen state) so ownership is grep-able from the module itself.

### 7.2 Function specification

Every externally accessible function should have a specification covering:

1. The name of the function.
2. The level it belongs to and whether it is external or internal.
3. Every argument and return value, *with legal bounds on the values*.
4. What it does (not how), including handling of errors.
5. Its expectations about the state of the level's resources on entry, and its effect on that state on exit, including error handling.

Items 1, 2, and 4 are implicit in good Python signatures and docstrings. Item 3 — *legal bounds on values* — is what type hints partially cover and what `Protocol`s should formalize. Item 5 — entry and exit state expectations — is the precondition-postcondition pair that distinguishes a specification from a signature.

A function whose entry and exit state expectations cannot be stated has implicit preconditions, which by §1.3 means multiplicative bugs waiting to fire.

### 7.3 End-of-design criteria

Liskov's three explicit criteria for declaring a design finished (pages 20–21):

1. All major levels of abstraction have been identified, system resources have been distributed among them, their positions in the hierarchy established, and their distribution among processes known.
2. The system exists as a structured program showing how flow of control passes among the levels. Specifications exist for all levels and functions. Interfaces between levels have been defined. Relevant test cases for each level have been identified.
3. Sufficient information is available so that a skeleton of a user's guide could be written.

A design that fails any of these is not finished. Code-completeness is a different and later thing.

## 8. Composition over parameterization over inheritance

Three ways to build a level out of other levels. Use them in this priority order.

### 8.1 Composition (default)

The level holds references to its dependencies and delegates. Dependencies are passed in via the constructor — never instantiated inside, never imported and used as a singleton.

```python
class TradeManager:
    def __init__(
        self,
        analyst: AnalysisAgent,
        risk: RiskAgent,
        executor: ExecutionAgent,
    ) -> None:
        self._analyst = analyst
        self._risk = risk
        self._executor = executor
```

Composition is the most flexible: dependencies can be swapped per environment, per test, per scenario, without inheritance trees, without metaclass tricks.

### 8.2 Parameterization (Python's functor analog)

When a level's behavior depends on a *type* — not a value — parameterize over a `Protocol`. This is the Python equivalent of an OCaml functor: a level that takes another level as input and produces a specialized level as output.

```python
class Repository(Protocol[T]):
    async def get(self, key: str) -> T | None: ...
    async def put(self, key: str, value: T) -> None: ...

class Cached(Generic[T]):
    def __init__(self, backing: Repository[T]) -> None:
        self._backing = backing
        self._cache: dict[str, T] = {}
```

Rules transferred from ML:

- The parameter signature should be minimal — only what the parameterized level actually uses. Over-parameterization (requiring more of the parameter than you use) reduces reusability.
- The output should be specialized through the type system. `Cached[Lesson]` is a different type from `Cached[Account]`; instances do not mix.
- A *closed* parameterized level references only its parameters. Module-global imports of other components break closure and reduce substitutability.

### 8.3 Inheritance (rare)

Use inheritance only when the subtype is genuinely substitutable for the supertype — when every operation valid on the supertype is valid on the subtype with consistent behavior. The formal rule is the Liskov Substitution Principle (§11), which is a separate later contribution from the *Guidelines* paper.

In practice in this codebase:

- Inheritance for code reuse is forbidden. Use composition.
- Inheritance for *interface declaration* is acceptable but `Protocol` is preferred — it is structural, does not require declaration at the implementation site, and supports duck typing for tests.
- Abstract base classes (`ABC`) are forbidden until two concrete implementations exist (already in `python.md`).

## 9. Boundary contracts and enforcement

A boundary contract is a machine-checkable statement of who may import whom. Without enforcement, hierarchy decays.

Required:

- `import-linter` contracts for every level boundary in §3. The current contract covers `core` ↔ `agents` ↔ `indicators`; extend to cover `agents/` ↔ `agents/`, manager-to-worker, and the `adws/` internal layout.
- Architecture-level tests — a single test per boundary that asserts the rule. These survive when contracts get out of date.
- A pre-merge check that fails on any new edge in the import graph that crosses a level boundary.

Recommended:

- Module headers declaring intended dependencies. A one-line comment at the top of each module naming the levels it may depend on. A reviewer can verify against the imports below.
- Periodic graph audits — render the import graph, look for cycles and back-edges, file a chore on each.

The cost of this discipline is one config file per boundary contract. The benefit is that hierarchy violations show up in CI rather than after a refactor goes wrong.

## 10. Generic programming with Protocols

Python's `Protocol` (PEP 544) is the practical analog of an ML signature and the modern descendant of Liskov's function specification (§7.2). Used well, it lets a level name its dependencies as types without binding to implementations.

Rules:

- Define the `Protocol` in the level that *consumes* it, not in the level that implements it. The consumer owns the contract.
- Make `Protocol`s minimal. If `RiskAgent` only needs `evaluate_proposal`, the Protocol has one method, not five.
- Use `@runtime_checkable` only when you actually need `isinstance` checks at runtime. The decorator has cost; default to off.
- Prefer Protocols over `ABC` for every interface. ABCs require declaration at the implementation site, which couples the implementer to the interface; Protocols don't.
- A `Protocol` returning `dict[str, Any]` is a Protocol that has given up. Type the return.

Two implementations are the test of a Protocol. If you cannot write a second implementation (an `InMemory*`, a `Fake*`, a `Stub*`), the Protocol is leaking implementation assumptions.

## 11. Substitutability (Liskov Substitution Principle)

The LSP is a *separate and later* Liskov contribution — first stated in her 1987 OOPSLA keynote and formalized with Wing in 1994's *A Behavioral Notion of Subtyping*. It is **not** in the 1973 *Guidelines* report. This section is included because Python's structural typing makes the rule directly applicable, but it should be cited separately.

Statement: whenever a function takes a parameter of type T, every subtype of T must be safe to pass.

Concrete rules:

- A subtype's preconditions cannot be stronger than the supertype's. If `Repository.get(key: str)` accepts any string, a subtype that requires a UUID-formatted string violates LSP.
- A subtype's postconditions cannot be weaker. If `Repository.get` returns `T | None`, a subtype that may also raise `NotFoundError` violates LSP.
- A subtype must respect the supertype's invariants. If `AccountState` invariants require `equity >= 0`, a subtype that allows negative equity violates LSP.
- Side effects in a subtype must be a subset of those declared by the supertype. A "read-only" view that also writes a log file violates LSP.

In test code, LSP is the property that makes substitution safe: an `InMemoryStore` passed where `PostgresStore` is expected must produce indistinguishable behavior on the public surface.

## 12. Antipatterns

Each entry below is a specific failure mode this guide rejects. They are the mirror image of §§2–11.

- **The grab bag.** A module named `utils.py`, `helpers.py`, `common.py`, `core_utils.py`, `misc.py`. Already in `python.md`. The fix is to identify which of the kinds of useful abstraction in §2 each function belongs to and move it there.
- **The god object.** A single type or module that every other module reads from and writes to. The Elder pipeline's old `PipelineState` is the canonical example; the refactor splits it into typed events. The fix is to invert: each stage produces and consumes typed values; nothing is shared mutable.
- **The fat connection.** A level whose public surface is `dict[str, Any]`, `**kwargs`, or a base class that callers must downcast. The fix is to type the surface with a frozen dataclass, narrowing the contract.
- **The leaky resource.** A level that exposes its underlying resource (returns the `httpx.Response`, returns the database row tuple, returns the `Order` object from `ib_async`). The fix is to translate at the boundary into a domain type.
- **The reach-around.** Module A reaches into module B's internals (B's private attributes, B's private functions, B's module-level state). The fix is to add a public method on B that does what A needs, then audit whether A should be doing this at all.
- **The upward dependency.** A lower-level module imports from a higher level. Often appears as a "convenience" import. The fix is to invert: the higher level passes what the lower level needs as a parameter.
- **The cyclic dependency.** Two modules at the same level import each other. The fix is to extract the shared types into a third module both depend on, or to merge the modules if they were never separable in the first place.
- **The premature generalization.** A `Generic[T]` Protocol introduced before the second implementation exists. The fix is to inline. Re-extract when the third call site appears.
- **The inherited reuse.** A class that inherits from another class to reuse code. The fix is to compose: hold a reference instead of being one.
- **The exposed mutable.** A module that exposes a mutable list, dict, or dataclass instance for callers to modify. The fix is to expose immutable views (`tuple`, `Mapping`, `frozen=True`) and accept mutation requests as method calls.
- **The implicit precondition.** A function whose docstring or types do not name what it requires of its inputs. Typing the precondition usually surfaces a missing validation step, and per §1.3 implicit preconditions are multiplicative test cost.
- **The wrapping for its own sake.** A class with one method, an `__init__` that just stores a dependency, and no behavior. The fix is to delete it and pass the dependency directly.
- **The cross-process shared state.** A worker function that reads or writes anything that survives outside its arguments and return value (a global, a class attribute, a `Manager`-shared dict). The fix is to make the worker pure: bytes in, bytes out.

## 13. Self-audit checklist

Run this against any level (and every module that implements it) before declaring its design done. Each item is binary; partial credit does not exist.

1. The level's name precisely labels its single supported abstraction, with no conjunction.
2. The level has a written specification covering the seven items in §7.1.
3. Every externally accessible function has a specification covering the five items in §7.2.
4. Every public name has a typed signature; no `Any`, no `dict[str, Any]`.
5. The level imports only from levels at or below itself in the hierarchy (§3).
6. Each dependency appears either as a constructor parameter or as a level-permitted import — never as a direct instantiation of a sibling.
7. There is no module-level mutable state. Configuration is read once at module load into a frozen value, owned by the level whose abstraction it parameterizes — never in a shared constants module imported by multiple levels.
8. The level fits exactly one of the kinds of useful abstraction in §2; you can name which.
9. If the level declares a `Protocol`, a second implementation exists (real or in-memory).
10. If the level is parameterized over a `Protocol`, the Protocol is minimal — every method on it is actually called.
11. The level's public surface is ≤ 7 names per implementing module, or the module is a deliberate aggregate (a domain root, a bounded-context entry point) and the count is justified.
12. No private name from another level is referenced. No `_`-prefixed name is imported from outside its own module.
13. The connection-as-assumption list (§6.1) for each dependency is short enough to write on one line.
14. If the level participates in cross-process work, the boundary is a queue or typed event — not shared state (§4).
15. The end-of-design criteria in §7.3 are all satisfied.

A level that fails any item is not finished, no matter how green its tests are.

## 14. Ousterhout's complementary frame

Liskov's 1973 *Guidelines* and Ousterhout's *A Philosophy of Software Design* (2nd ed, 2018) reach the same destination from different starting points. Liskov starts from reliability and the test-budget arithmetic; Ousterhout starts from complexity as the operational tax that change amplification, cognitive load, and unknown-unknowns levy on a codebase. The vocabularies translate.

Ousterhout's operational definition: **complexity = obscurity + dependencies**, weighted by edit frequency. It manifests as change amplification (a small functional change requires edits in many places), cognitive load (a reader needs to hold many facts in mind to make a change correctly), and unknown unknowns (a reader cannot tell what they need to know). Liskov's connection-as-assumption rule (§6.1) and the reliability arithmetic (§1.3) are the same claim from the reliability side.

### 14.1 Depth as the operational measure of a level

> "Modules should be deep: their interfaces should be much simpler than their implementations." (Ousterhout, Ch 4.)

Depth equals functionality hidden divided by interface surface. A *deep* module hides substantial implementation behind a narrow interface. A *shallow* module's interface roughly mirrors what it wraps; the interface cost is paid without the encapsulation benefit. Small is not the same as simple — a tiny shallow class around one DB call is shallower than a fat one with rich behavior.

This is the operational test for Liskov's §1.3 reliability arithmetic. A level with a fat interface and thin implementation does not reduce the test-case count; it just renames the testing surface. The two framings converge: depth is what makes additive (rather than multiplicative) test budgets achievable.

### 14.2 Pass-through methods and pass-through variables

Ousterhout names two recurring shallow-module shapes:

- **Pass-through method.** A method that forwards args to another method with a near-identical signature. Two layers share a responsibility neither owns cleanly. Pick one: expose the lower layer directly, push real work into the wrapper, or merge.
- **Pass-through variable.** An argument threaded through three or more frames as a function argument that none of the intermediate frames inspect. Introduce a context object the caller injects; don't smuggle the value through ambient state.

Both shapes add interface surface without adding functionality. Liskov's connection rule (§6.1) flags them indirectly — the connection assumption "you must remember to forward this value" is part of the contract every intermediate frame depends on, even though it doesn't appear in any one signature explicitly.

### 14.3 Pull complexity downwards

> "Most modules have more users than developers, so it is better for the developers to suffer than the users." (Ch 8.)

The decision is about who pays the cost of a knob. Before exporting a configuration parameter or a constructor argument, ask whether a sensible default could be computed from observed behavior. Only export a knob when a runtime operator will tune it and the caller genuinely knows more than this module ever will.

Symptoms of leaked complexity upward: callers consistently passing the same value; defaults that "no one would change"; init signatures that read like API documentation rather than dependency lists.

This complements `code-structure.md`'s "configuration is part of the public API" — that rule says *expose* the knob when it's tunable at runtime; this rule says *don't invent* the knob when a default would do. The deciding question: would a different value of this knob change observed behavior in production? If yes, expose. If "no, we just want it configurable for testing," default it strongly and let tests override.

### 14.4 General-purpose interfaces are deeper

> "General-purpose modules are deeper." (Ch 6.)

Specialization in a middle-layer interface (`backspace(cursor)` vs `delete(range)`) leaks the caller's vocabulary down and creates information leakage. Push specialization up to the application boundary or down into a driver — not into the middle.

When sketching a new module, ask three questions:

1. What's the simplest interface that covers all my current needs?
2. In how many situations will this method be used?
3. Is the API still easy to use for today's caller?

If a method has one caller and its name encodes that caller's intent, refactor to a more primitive operation and move the intent up to the caller. This is the same move Liskov §2.3.1 calls "generalization via common functions" — Ousterhout sharpens it with the heuristic that the *name* is the diagnostic.

### 14.5 Don't decompose by execution order

> "Temporal decomposition." (Ch 5.)

Modules named `loader`, `parser`, `writer`, `validator` are red-flag verbs implying ordering. The orchestration spine is the legitimate place for time-ordering — it owns the sequence. Stage modules are organized by *what knowledge they encapsulate*, not *when they run*. If two stages share a domain concept, the concept lives in a third module owned by neither, called by both.

This is `code-structure.md`'s bounded-context rule from a different angle. A bounded context groups by domain knowledge; a temporal decomposition groups by sequence position. The first is stable across requirements changes; the second rots when the sequence changes.

### 14.6 Define errors out of existence

> "Define errors out of existence." (Ch 10.)

Each exception block answers "does the caller have a meaningful action?" The techniques in priority order:

1. **Redefine the operation** so the error case is the normal case. Removing an entry from a set succeeds whether or not it was present.
2. **Mask the exception** in a low-level module. TCP retransmits a lost segment; the application doesn't see it.
3. **Aggregate handlers** at the application boundary. One top-level catch translates many exception types to one outcome.
4. **Crash** for unrecoverable failures. A process whose invariants are violated should not continue.

A method dotted with `try/except` blocks is usually leaking abstraction — the cases the inner method exposes are cases the outer method has to translate. Consolidate.

Liskov §11 has the formal version: a method's post-condition includes its exception behavior, and a subtype's post-conditions must be no weaker. Ousterhout's frame asks the design-time question that Liskov's frame answers at the contract level: should this be an exception in the first place?

### 14.7 Comments and names as design diagnostics

> "Comments are a design tool." (Ch 15.)

Write the interface docstring before the body. If the docstring drifts past about four lines, leaks internal collaborators, or you can't pick a precise name for a variable, that's design feedback. Refactor the design, not the comment.

Two specific diagnostics:

- **"Hard to describe."** A function whose docstring requires "this method is called by X after Y has set Z" has a wrong boundary. The implementation context is leaking into the contract.
- **"Hard to pick a name."** When a name needs a class prefix to make sense (`File.fileBlock`), drop the prefix. When two variables of "the same kind" carry different invariants (logical block vs physical block, raw vs URL-decoded), encode the distinction in the name or in distinct types.

This is the operational complement to Liskov's §7 specification discipline. Liskov says the specification must exist; Ousterhout adds that the *act of writing the specification* is what tests the design.

### 14.8 Design every nontrivial module twice

> "Design it twice." (Ch 11.)

When the work runs more than a day, sketch a one-paragraph alternative — even a deliberately bad one — and compare. The act of contrast surfaces what makes the chosen design good. Ousterhout argues most engineers skip this because it feels wasteful; the contrast is exactly what gives you signal that the first design wasn't obvious-by-accident.

Capture the considered alternatives in the PR description or ADR. One paragraph each, with pros and cons. Pairs with `refactoring.md`'s preparatory-refactoring discipline — design-twice is how you tell whether the simpler design has painted into a corner before you commit to it.

### 14.9 Where Ousterhout and the rest of this pack disagree

Ousterhout is hostile to TDD (Ch 19.4) — he reads it as fragmenting design thinking into too-small steps. This pack reads `tdd.md` and `goos-guide.md` as the stronger position: tests are the design oracle, and the dialog between design-as-listening and design-as-thinking is more productive than either pole alone. Where the two methods conflict, the pack follows GOOS; Ousterhout's complexity vocabulary remains useful as the *judging* surface that test-pain alone can miss.

Strategic vs tactical programming (Ch 3) is covered indirectly by the pack's broader posture — most work in a chained-agent context is tactical (one story at a time), but the modularity, refactoring, and DDD rules force occasional strategic moves at scope boundaries.

## Sources

- Liskov, B. H. (1973). *Guidelines for the Design and Implementation of Reliable Software Systems.* MITRE Corporation, ESD-TR-72-164 / MTR-2345 / DTIC AD0757905. The spine of this guide; all section-by-section page citations refer to this report.
- Ousterhout, J. (2018). *A Philosophy of Software Design*, 2nd edition. Yaknyam Press. The §14 frame; Ch 4 (depth), Ch 6 (general-purpose), Ch 7 (different layer, different abstraction), Ch 8 (pull complexity downwards), Ch 10 (errors out of existence), Ch 11 (design twice), Ch 12-15 (comments and names).
- Parnas, D. L. (1972). *On the Criteria to Be Used in Decomposing Systems into Modules.* CACM. The connection-as-assumption definition that Liskov adopts.
- Liskov, B. H., Wing, J. (1994). *A Behavioral Notion of Subtyping.* ACM TOPLAS. The formal LSP statement used in §11. Distinct from *Guidelines.*
- MacQueen, D. (1984). *Modules for Standard ML.* LFP. Origin of signature/structure/functor.
- Leroy, X. (1995). *A Modular Module System.* Journal of Functional Programming. The design behind OCaml's module system.
- Minsky, Y., Madhavapeddy, A., Hickey, J. *Real World OCaml*, chapters on Functors and First-Class Modules.
- Odersky, M. et al. *Scala 3 Reference: Domain Modeling — Object-Oriented Modeling.*
