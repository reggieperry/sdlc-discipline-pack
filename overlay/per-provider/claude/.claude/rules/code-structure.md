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
