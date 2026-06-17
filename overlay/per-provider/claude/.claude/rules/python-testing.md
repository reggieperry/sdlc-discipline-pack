---
paths:
  - "**/*_test.py"
  - "**/test_*.py"
  - "tests/**/*.py"
---

# Python testing

How to write Python tests: pytest structure, fixtures, mocking, and property-based testing with Hypothesis. Sources: the pytest docs (fixtures, parametrize, monkeypatch, skipping, good practices), Brian Okken's *Python Testing with pytest*, the `unittest.mock` docs, `coverage.py`, and Hypothesis. The TDD cadence and design discipline are in `craft-tdd.md`.

> See `craft-tdd.md` for red-green-refactor and "listen to the tests", `python-types.md` for the small Protocols that make fakes trivial, and `craft-refactoring.md` for self-testing code as the refactoring prerequisite.

## Layout, structure, and naming

- **Name test files `test_*.py` (or `*_test.py`) and functions/methods with the `test` prefix; group with `Test`-prefixed classes that have no `__init__`** — pytest silently skips anything else. Keep tests in a top-level `tests/` dir against a `src/` layout, and prefer the `importlib` import mode for new projects.
- **Structure each test Arrange-Act-Assert, one behavior per test, with plain `assert`** — pytest rewrites `assert` into rich failure messages, so `unittest`-style helpers add noise. "One behavior" is not "one assert" — several asserts that jointly verify one behavior are fine.
- **Push setup into fixtures** so a broken Arrange step reports as an error, distinguishable from an assertion failure.

## Fixtures

- **Prefer `yield` fixtures for teardown** (code after `yield` runs as teardown) and keep each fixture to one state-changing action bundled with its own teardown.
- **Choose the narrowest scope that works** (`function` default; widen to `class`/`module`/`session` only for genuinely expensive setup — broader scope leaks mutable state). Put cross-module fixtures in `conftest.py` (auto-discovered, no import). Use `autouse=True` sparingly — it hides what a test depends on.
- **Use the built-in `tmp_path` for filesystem tests and `capsys` for captured stdout/stderr.** Know the setup-failure rule: if a fixture raises before `yield`, its own teardown doesn't run, but already-completed fixtures are still torn down.

## Parametrize

- **Use `@pytest.mark.parametrize` for table-style cases** — each row becomes a separately reported, independently failing test. Stack decorators for the Cartesian product; mark a single row with `pytest.param(..., marks=..., id="...")` rather than disabling the whole table.

```python
@pytest.mark.parametrize("text, sep, want", [
    ("a/b/c", "/", ["a", "b", "c"]),
    ("abc",   "/", ["abc"]),
    pytest.param("", "/", [""], id="empty"),
])
def test_split(text, sep, want):
    assert split(text, sep) == want
```

## Mocking discipline

- **Patch where the name is *looked up*, not where it is defined** — the single most common mocking error. If `b.py` does `from a import C`, patch `b.C`, not `a.C`.
- **Always give a mock a spec — prefer `autospec=True`/`create_autospec`** so a wrong-signature call raises instead of silently passing; use `spec_set` to reject attributes the real object lacks.
- **Don't mock what you don't own — wrap a third-party library (the LLM SDK, an external CLI like `git`) in a thin interface you own and mock that.** Mocks of external APIs drift from real behavior and give green tests that fail in production.
- **Prefer a hand-written fake over a deep `Mock` tree** — a fake is a working simplified implementation, so tests assert on behavior, not on a brittle script of expected calls.
- **Use `monkeypatch` for env vars, attributes, dict items, cwd** (all auto-undone), but don't monkeypatch builtins like `open`. In pytest, prefer the `mocker` fixture over the `patch` decorator/context manager.

## Coverage

- **Measure branch coverage (`--branch`), not just statements** — statement coverage marks an `if` covered even when its false path never runs. **Treat coverage as a signal pointing at untested lines, never a target to hit** (a percentage mandate gets gamed into assertion-free tests). Use `# pragma: no branch` only for branches partial by design.

## Property-based testing (use Hypothesis)

Property tests assert invariants over a generated input space and **shrink** any failure to a minimal counterexample — far more robust than example tests alone (an empirical study found a property test catches ~50× the mutations of an average unit test). Hypothesis is the de-facto standard; there is no serious competitor.

- **Drive a property with `@given` and one strategy per argument**, reaching for primitives first (`st.integers()`, `st.text()`, `st.lists(...)`); build domain objects with `st.builds(...)` or a `@composite` function. Favor strong invariants — round-trip equality, idempotence, a conservation law, or agreement with a slow oracle — not trivially-true ones (`Counter(result) == Counter(input)`, not just "is sorted").
- **Pin known-tricky inputs with `@example(...)`** stacked above `@given` (they run first, free of the `max_examples` budget) and keep concrete example tests alongside the properties.
- **Use a `RuleBasedStateMachine` to test a stateful object's lifecycle** — Hypothesis generates and shrinks whole *action sequences*. Seed once in `@initialize`, model each operation as a `@rule`, gate illegal transitions with `@precondition`, check cross-cutting properties with `@invariant`, and pass generated values between rules through a `Bundle`. Prefer the model/oracle shape (run the real component and a simple in-memory model in lockstep and assert they agree), and export `TestX = MyMachine.TestCase` so pytest collects it.
- **Set `deadline=None` for tests that touch the network, a subprocess, or an external CLI** (the 200 ms default makes I/O-bound tests flaky), and `derandomize=True` when you need bit-reproducible runs; suppress a *named* `HealthCheck`, never all of them. Trust the automatic example database (`.hypothesis/`) to replay a failing case first next run.
- **Keep properties pure and deterministic** (no wall-clock, real I/O, or external randomness) and constrain at the strategy (bounds, `@composite`) rather than discarding inputs with heavy `.filter()`/`assume()`.

## Anti-weakening (what an anti-weakening gate forbids)

Treat any of these versus the merge-base as test-suite weakening — do not introduce them:

- A test deleted or commented out with no equivalent replacement, or a previously-running test newly gated behind `@pytest.mark.skip`/`skipif` to dodge a failure (use `xfail(strict=True)` only for a genuinely-expected failure, so an unexpected pass fails the suite).
- A removed or weakened assertion, or a row dropped/loosened from a `parametrize` table (diff the case count, not just pass/fail).
- An assertion replaced by a `Mock` assertion that can't fail (asserting a mock was called while dropping the check on its result).
- A deleted Hypothesis `@example` regression or a `deadline`/`max_examples` change that masks a real failure.
