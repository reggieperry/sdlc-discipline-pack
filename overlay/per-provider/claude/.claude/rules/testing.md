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

## Canonical test shape — arrange, act, assert

Every test has three phases, visible in the body:

```text
arrange  # build the fixture and any stubs
act      # call the method under test — usually one line
assert   # verify the outcome
```

If the act phase has more than one line, the test exercises too much or the SUT has a workflow that should be its own method. Sometimes called setup-exercise-verify or given-when-then; the shape is what matters, not the names.

## Fresh fixture per test

Default to a fresh fixture in each test's setup. Don't share state across tests — non-determinism from shared state is one of the most common sources of flake. Share only when construction is genuinely expensive, and only when tests are verified non-mutating.

## What to test per feature

1. Happy path
2. Authorization or validation rejects the operation
3. Missing or malformed input
4. Edge case at a boundary the domain cares about

## Probe the boundaries

Happy path first, then deliberately the boundaries — empty collections, zero, negatives, types where collections were expected, strings where numbers were expected. Adopt an adversarial mindset: "how would I break this if I were trying to?" That surfaces cases the happy-path mindset misses.

## Verify the failing test fails

Before writing production code, run the test and watch it fail. Two checks:

1. **Right reason.** If it fails for an unexpected reason, the test setup or assumption is wrong — fix that before writing code.
2. **Clear diagnostic.** If the failure message doesn't explain what's wrong, improve the message *before* writing the fix. A year from now, that message is the only clue.

For existing code where the test already passes, *inject a fault* into production code and confirm the test fails. A test that won't fail when it should is not a test.

## Few assertions per test

One verify per test as the rule of thumb. When the first assertion fails, subsequent assertions don't run — useful information may be hidden. Tightly related assertions on the same fixture mutation can share a test; independent assertions should split. The same rule applies to mock expectations: many expectations means the test or the SUT is doing too much.

## Risk-driven coverage

Write tests where the risk lives. Don't test getters and setters with no logic; do test the calculation, the state transition, the failure mode, the boundary. *"It is better to write and run incomplete tests than not to run complete tests"* (Fowler).

When a PR description says "idempotent," "no-op on retry," "cancellation-safe," "bounded," or "degraded mode," treat the prose as a flag — find or write the test that proves the claim. Without the test, the claim is aspirational. See `tdd.md` for the listen-to-the-tests pattern; the same listening applies to documentation.

## Assertions
- Numerical comparisons that may have rounding error: `pytest.approx` with explicit tolerance, or exact-equality on a fixed-precision type for financial values.
- Domain-typed equality (`assert result == expected_value_object`) rather than field-by-field assertions where the value object exists.
- Assertion messages name the domain expectation: `assert decision.is_rejected, f"expected 2% rejection for {ticker}"` over bare `assert decision.is_rejected`.

## Migration testing (schema-evolving stores)

When a change touches a versioned schema (relational DB, search index, on-disk binary format):

- Apply the migration to a clean store from empty → success.
- Apply on a populated copy when the migration touches existing data — verify data is preserved correctly.
- Apply the downgrade and re-apply the upgrade. A downgrade that has never been exercised is a stub.
- Exercise the new schema with the new code path that consumes it (write a row / index a document / round-trip a record) — proves the migration produces what callers expect.
- Don't mark migration work done until upgrade + downgrade + upgrade has been verified end-to-end against a real store.

The integration test runs against the real store with migrations applied, not against an in-memory shortcut that builds the schema from the application model.

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
