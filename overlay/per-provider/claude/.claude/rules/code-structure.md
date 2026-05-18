---
paths:
  - "**/*.py"
---
# Code structure

## Domain design (Tell Don't Ask)
- Objects decide using their own state. Callers tell, don't ask.
  Don't: `if account.balance - amount < 0` → Do: `account.charge(amount)`
  Don't: `if permission.level >= required` → Do: `permission.allows(action)`
- Functions return typed output. Never write to a shared state object.
  No `state.foo = bar`. No `state.items.append(item)`. Return the data; let the caller wire it.
- Enums and dataclasses carry domain logic about their own data.
  Status.is_terminal(). Schedule.book(request). Account.charge(amount).
- Protocols return typed objects, never dict[str, Any].
  If a repository returns account data, it returns a typed Account object, not a dict with string keys.
- Don't reach across domain boundaries to assemble data.
  If a downstream stage needs context from an upstream stage, receive it as a typed object.
  Don't reach into shared context to pull fields from multiple unrelated sources.
- Domain invariants live on the owning aggregate, not in a separate pipeline stage.
  A ConfirmedReservation is only constructable through Schedule.book(request).
  The validation logic is arithmetic on the aggregate's own fields — no external decision-making.

## Bounded contexts
- Each module serves one bounded context. Don't mix contexts in one file.

## Object style (peers, composition, context independence)

- **Peer stereotypes.** Every collaborator is one of: *dependency* (required service, constructor parameter, no default), *notification* (fire-and-forget listener, default to no-op), *adjustment* (policy/strategy, default to sensible value). A bloated constructor usually conflates the three — re-categorize before splitting.
- **Composite simpler than the sum of its parts.** A composite's public surface is narrower than the union of its components'. `editor.set_value(money)`, not `editor.set_amount_field(...).set_currency_field(...)`. Exposing the parts means the composite is a leaky wrapper.
- **Context independence.** An object holds no built-in knowledge of the system it runs in. Whatever it needs about the larger environment is passed in (construction or method argument). A class using vocabulary from two domains is probably violating this — exceptions are bridging adapters whose stated purpose is translation.
- **No And's, Or's, or But's.** Describe what an object does in one sentence without a conjunction. "Loads documents AND parses them" is two objects. "Dispatches OR caches" is two objects. When the description needs an "and," split.
- **Encapsulation and information hiding are different.** Encapsulation bounds the blast radius of a change (all interaction through the API). Information hiding conceals *how* behind *what* so callers reason at the level of intent. Getters/setters on every field give the first without the second.

## Simplicity calibration

"As simple as possible, but no simpler." Two failure modes the principle guards against:

- *Over-engineering* — frameworks, indirection layers, configuration knobs added without a concrete need.
- *Under-engineering* — happy-path-only code that skips idempotency, error isolation, or audit needed for production.

When proposing an approach, name the simpler option first. Add complexity only when the cheaper alternative paints into a corner. The "but no simpler" half is load-bearing — idempotency, optimistic concurrency, error isolation, and audit are the floor, not optimizations.

## Module depth and complexity (Ousterhout)

- **Judge a module by depth.** Functionality hidden divided by interface surface. A deep module exposes a small interface that hides substantial implementation. A shallow module's interface roughly mirrors what it wraps. Small ≠ simple; shallow modules pay an interface cost without earning the encapsulation benefit.
- **Kill pass-through methods.** A method that just forwards args to another with a near-identical signature is two layers sharing a responsibility neither owns. Pick one: expose the lower layer, push real work into the wrapper, or merge the layers.
- **Kill pass-through variables.** An argument threaded through three or more frames is a pass-through variable. Introduce a context object the caller injects; don't thread it by signature or smuggle it via globals.
- **Pull complexity downwards.** A module has more callers than implementers; the implementer suffers so callers don't. Don't export a knob when a strong default would do. Export only when a runtime operator will tune the value.
- **General-purpose interfaces are deeper.** Specialization in a middle-layer interface leaks the caller's vocabulary down. Push specialization up to the application boundary or down into a driver, not into the middle. Ask: "what's the simplest interface covering all current needs; in how many situations will this method be used?"
- **Decompose by knowledge, not by execution order.** Modules named `loader`, `parser`, `writer`, `validator` are red-flag verbs implying ordering. Order belongs in the orchestrator that owns the sequence; stage modules organize by what knowledge they encapsulate. If two stages share a domain concept, the concept lives in a module both call.
- **Define errors out of existence.** In priority order: redefine the operation so the error case is the normal case; mask the exception in a low-level module; aggregate handlers at the boundary; crash for unrecoverable failures. A method dotted with `try/except` is usually leaking abstraction; consolidate.
- **Comments and names as design diagnostics.** Write the interface doc before the body. If the doc drifts past four lines, leaks internal collaborators, or you can't pick a precise name for a variable, that's design feedback — refactor the design, not the comment.
- **Design every nontrivial module twice.** When work runs more than a day, sketch a one-paragraph alternative — even a deliberately bad one — and contrast. The act of contrast surfaces what makes the chosen design good. Capture the considered alternatives in the PR description or ADR.

## Testing principles
- Coverage floor: 60% (enforced by pytest-cov). Target: 80% for core logic.
- Every new public function gets at least one test.
- Shared fixtures in conftest.py. Deterministic data only (use Hypothesis for controlled randomness, not ad-hoc randint).
- Assertions include a message: `assert x > 0, "Expected positive value"`
- Assert output data and state mutations, not just a return signal.
- Use property tests for mathematical invariants and safety boundaries. Use example tests for specific scenarios and integration flows.
- `# pragma: no cover` only for TYPE_CHECKING blocks and abstract methods.

## Database conventions
- NUMERIC for money columns, never FLOAT; mapped to Python Decimal.
- GENERATED ALWAYS AS for computed columns. The database enforces correctness — the application can't write wrong values.
- Table names: snake_case, plural (`trades`, `costs`, `open_positions`).
- Column names: snake_case (`entry_price`, `risk_per_share`).
- TIMESTAMPTZ (not TIMESTAMP) for dates with times. Store in UTC.
- DATE for date-only columns.
- All queries with parameterized queries. Never string interpolation.
- Schema file (e.g., `schema.sql`) is the source of truth.
