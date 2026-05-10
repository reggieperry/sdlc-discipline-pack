---
paths:
  - "**/*.py"
---

> Full reasoning, citations (Liskov 1973, Parnas, ML/OCaml/Scala), and worked examples: `.claude/sdlc-discipline/guides/modularity-guide.md`.
> Elder examples in this rule are illustrative — they show what level ownership
> looks like in a non-trivial codebase. The principle applies across projects.

# Modularity rules

## Vocabulary

- **Level** — design unit. One or more files implementing one abstraction with shared resources.
- **Module** — implementation file or package. A level may be implemented by several modules.

## The kinds of useful abstraction

A new level fits exactly one. If it fits none, it's a grab bag, not a level.

- **Resource** — wraps an external substrate (broker, LLM, DB, network). Owns it exclusively. Translates failures to domain exceptions at the boundary.
- **Data** — hides layout/encoding behind operations on a frozen value type. Constructed only through validated entry points.
- **Generalization** — extracts an algorithm two or more levels share. Wait for the third call site before extracting.
- **Information-limiting** — pushes complexity down so the higher level can't see it. The hidden data becomes a resource of the lower level.
- **Change-encapsulation** — hides what's expected to change behind a stable signature. Test by writing a second implementation; if the signature has to change, it was leaking.

## Hierarchy

- Each level owns its resources exclusively. No other level reads or writes them directly.
- Lower levels do not reference higher levels. Imports go down only.
- Data connections between levels: explicit args and returns only. No implicit common data across level boundaries.
- The import graph is acyclic. Cycles are design failures, not style nits.

## Elder's levels (lowest to highest)

| Level | Implementing files |
| ----- | ------------------ |
| Domain primitives | `core/state.py`, `risk_parameters.py`, `indicators/elder.py` |
| Resource & data abstractions | `core/llm.py`, `core/ib_*.py` (deferred), `knowledge/store.py`, `db/` |
| Workers | `agents/scanner_agent.py`, `agents/analysis_agent.py`, `agents/risk_agent.py`, `agents/diary_agent.py` |
| Domain managers (planned) | `ScanManager`, `TradeManager`, `ReviewManager` |
| Pipeline primitives | `core/pipeline.py`, `core/events.py`, `core/agent.py` |

## Cross-process boundaries

- Worker functions are pure: bytes in, bytes out. No globals, no class attributes, no shared state.
- No pickled DataFrames across processes. Arrow IPC only.
- Crossing processes coincides with crossing levels. Don't fan out inside a level just for parallelism.
- Worker functions are module-level (not methods, not lambdas) — already in `concurrency.md`.

## Module shape

- ≤ 7 public names per module. Above that, audit; either split or justify the aggregate.
- A module under 30 lines is a Protocol declaration or a fragment that belongs in a parent.
- No mutable module-level state. Config is read once at module load into a `Final[...]` value or frozen dataclass.
- Constants and tunables are data resources owned by the level whose abstraction they parameterize. No shared constants module across levels. If another level needs the value, expose a function — don't import the constant.
- Don't re-export dependency names (`Decimal`, `Path`). Widens the surface for nothing.
- Underscore-prefix every name not part of the contract.

## Connections

For every dependency A → B, the connection includes: names, types, pre/post conditions, exceptions, side effects, performance envelope. If the list is long, the connection is fat.

- Hide vendor protocols, encoding/storage layout, algorithms, mutable state.
- Don't hide named domain invariants (the 2% Rule), stable stdlib (`pathlib.Path`, `Decimal`), or the shape of typed events.
- A `Protocol` returning `dict[str, Any]` has given up. Type the return.

## Composition > parameterization > inheritance

- **Default: composition.** Dependencies are constructor parameters. Never instantiated inside, never imported and used as a singleton.
- **Type-parameterized levels:** minimal `Protocol`, closed (refers only to its parameters). Define the `Protocol` in the consuming level, not the implementing one.
- **Inheritance only for LSP-substitutable subtypes** (preconditions no stronger, postconditions no weaker, invariants preserved, side effects a subset). Forbidden for code reuse.

## Specifications

Every level has a written spec covering: name; abstraction supported (one sentence, *what* not *how*); resources owned (external + data); hierarchy placement and process assignment; externally accessible functions; internally accessible functions.

Every externally accessible function specifies: name; level + external/internal status; args and returns *with legal bounds*; what it does (not how) including error handling; resource state expectations on entry and effect on exit.

## Antipatterns

- **Grab bag** — `utils.py`, `helpers.py`, `common.py`, `misc.py`. Already in `python.md`.
- **God object** — one type/module that everything reads and mutates. (Old `PipelineState`.)
- **Fat connection** — public surface is `dict[str, Any]`, `**kwargs`, or a downcast base.
- **Leaky resource** — returns raw `httpx.Response`, DB rows, `Order` from `ib_async`.
- **Reach-around** — caller touches another module's private attrs/funcs/state.
- **Upward dependency** — lower-level module imports from higher.
- **Cyclic dependency** — peer-level files import each other.
- **Premature generalization** — `Generic[T]` before the second implementation exists. Inline; re-extract on the third call site.
- **Inherited reuse** — extends to share code. Compose instead.
- **Exposed mutable** — public list/dict/dataclass mutable from outside. Use `tuple`, `Mapping`, `frozen=True`.
- **Implicit precondition** — signature doesn't name what it requires of inputs.
- **Wrapping for its own sake** — one-method class that just stores a dependency.
- **Cross-process shared state** — worker reads/writes globals, class attrs, or `Manager`-shared dicts.

## Self-audit (binary, no partial credit)

1. Name precisely labels its single abstraction. No conjunctions.
2. Level spec exists (name, abstraction, resources, hierarchy, externals, internals).
3. Every external function has a spec (args + legal bounds, behavior, state expectations).
4. Public names typed. No `Any`, no `dict[str, Any]`.
5. Imports only at or below itself in the hierarchy.
6. Dependencies via constructor or level-permitted import. No sibling instantiation.
7. No mutable module-level state.
8. Fits exactly one kind of useful abstraction. You can name which.
9. If declares a `Protocol`, a second implementation exists.
10. If parameterized, the `Protocol` is minimal — every method actually called.
11. ≤ 7 public names per module (or justified aggregate).
12. No `_`-prefixed name imported from outside its own module.
13. Connection-as-assumption list per dependency fits on one line.
14. Cross-process boundaries are queues or typed events, not shared state.
15. End-of-design criteria met: hierarchy known, structured program exists, specs exist, interfaces defined, test cases identified per level.

A level failing any item is not finished, no matter how green its tests are.
