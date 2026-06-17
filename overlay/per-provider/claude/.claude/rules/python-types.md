---
paths:
  - "**/*.py"
---

# Python type system

Encode invariants in the type system so a checker catches wrong calls before runtime. Sources: the `typing` module docs, the mypy docs (`--strict`), the typing PEPs (484, 526, 585, 604, 612, 646, 695, 655), and Viafore's *Robust Python*. The substitutability discipline is from Liskov (`craft-abstraction.md`).

> See `craft-abstraction.md` for abstract data types and the substitution principle, `python-style.md` for dataclass-vs-Pydantic choices, and `python-llm.md` for the Pydantic model as the typed model boundary.

## Annotate, and run mypy strict

- **Annotate every function signature (parameters and return) and every public attribute**, and run `mypy --strict`. Treat its flag set as the floor — `--disallow-untyped-defs`, `--disallow-any-generics`, `--disallow-untyped-calls`, `--warn-return-any`, `--warn-unused-ignores`, `--strict-equality`, `--check-untyped-defs`, and the rest. Add `--warn-unreachable` explicitly — `--strict` does not enable it.
- **Avoid `Any` at boundaries; reach for `object` when the value is genuinely unknown** — with `Any`, `item.magic()` type-checks and hides the bug; with `object` it errors and forces a narrowing.

## Encode invariants

- **Encode closed value sets with `Literal` or `Enum`, distinct-but-int-shaped ids with `NewType`, and never-reassigned names with `Final`** — `Literal["r", "w"]` rejects typos, `NewType("UserId", int)` blocks passing a raw `int`, `Final` flags reassignment at zero runtime cost. This is the Python form of "make illegal states unrepresentable."
- **Use `TypedDict` (with `Required`/`NotRequired` or `total=False`) for a fixed-shape dict payload**, and type money as `Decimal`, never `float` (binary floats can't represent cents exactly — a correctness rule).

## Interfaces and generics

- **Prefer `Protocol` (structural) for the abstractions you consume; use `ABC` (nominal) only when you need explicit inheritance or shared implementation.** A Protocol needs no inheritance and matches duck-typed callers — the Python form of "accept a small consumer-defined interface." Gate `isinstance()` on a Protocol behind `@runtime_checkable`, and know it checks attribute presence only, not signatures.
- **Use the PEP 695 syntax (`class C[T]`, `def f[T]()`, `type Alias = ...`)** instead of importing `TypeVar`/`Generic`. Annotate decorators and callable-forwarders with `ParamSpec` (`*args: P.args, **kwargs: P.kwargs`); reserve `TypeVarTuple`/`Unpack` for genuinely variadic shapes.

## Sharper tools

- **Return `Self` from chaining methods, classmethod constructors, and `__enter__`** so subclasses get their own type back. Use `@overload` when the return type depends on the argument types. Prefer `TypeIs` (3.13+) over `TypeGuard` when the narrowed type is a subtype (it narrows both branches).
- **Keep static-only checks in code with `assert_type(x, T)`; use `reveal_type(x)` while debugging inference** — both are checker-only.

## Substitutability

- **A type that satisfies a Protocol must honor its full behavioral contract, not just the attribute shapes** — a substitute that violates the documented behavior breaks every caller written against the Protocol. Decide "is-a" by the substitution test, and prefer composition and small Protocols over deep inheritance hierarchies. (`craft-abstraction.md`.)
