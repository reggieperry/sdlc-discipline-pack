---
paths:
  - "tests/**"
---

> See python.md and code-structure.md for general language and architecture rules.
> See tdd.md for the TDD discipline these tests support.
> Project-specific testing playbooks (test commands, project-specific mocks,
> domain invariants) live in the project's overlay at `.claude/rules/project/`.

# Testing

## Structure
- Tests are async with pytest-asyncio when the system under test is async. Never require a live external service or API key to pass.
- Mock external APIs (LLMs, third-party services): return canned responses. Mock infrastructure (databases, brokers, queues): inline data structures.
- Build helpers parameterize the data each test needs. Prefer chainable builders over fixed factories — see tdd.md "Test data builders, not Object Mother."

## What to test per feature
1. Happy path
2. Authorization or validation rejects the operation
3. Missing or malformed input
4. Edge case at a boundary the domain cares about

## Assertions
- Numerical comparisons that may have rounding error: `pytest.approx` with explicit tolerance, or `Decimal` equality for exact financial values.
- Domain-typed equality (`assert result == expected_value_object`) rather than field-by-field assertions where the value object exists.

## Property tests (Hypothesis)

Reach for property tests when:
- The function has a wide input space and an invariant that must hold across it (output bounded, monotone, idempotent, equivalent under transformation).
- Serialization round-trips: `decode(encode(x)) == x`.
- A safety-critical rule must hold for *any* input. Use `max_examples=1000` for safety-critical invariants; `max_examples=200` for general ones.

### Pattern: domain-typed strategies

Build a strategy that constructs valid domain values, then reuse it across tests. For a record-shaped domain object with field-level constraints:

```python
my_record = st.fixed_dictionaries({
    "field_a": st.floats(min_a, max_a, allow_nan=False),
    "field_b": st.integers(min_b, max_b),
    # ...
}).filter(lambda d: d["field_a"] >= d["field_b"])  # cross-field invariants
```

Filter for invariants the data must respect; let Hypothesis explore the rest. Define the strategy once and reuse across property tests for that domain.
