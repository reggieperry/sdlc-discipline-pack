---
paths:
  - "**/*.go"
---

# Go types and interfaces

Encode invariants in the type system so the compiler catches wrong calls before the code runs, and keep interfaces small and consumer-defined. Sources: Go Code Review Comments, the Google and Uber style guides, *100 Go Mistakes*; the substitutability discipline is from Liskov (`craft-abstraction.md`).

> See `craft-abstraction.md` for abstract data types and the substitution principle, `go-style.md` for zero values and the typed-nil trap, and `go-llm.md` for the structured-output struct as the typed model boundary.

## Interfaces

- **Accept interfaces, return concrete types.** Callers keep full access to the concrete type's methods and can still pass it where an interface is wanted.
- **Keep interfaces small and define them in the package that *consumes* the values, not the one that implements them.** Consumer-defined interfaces stay minimal and avoid coupling. A one-to-three-method interface is the norm.
- **Create an interface only when a consumer needs it** — speculative interfaces are interface pollution. Don't add an interface "for mocking"; design the real API so it's testable through its concrete surface, and define the small consumer interface where the test lives. (`craft-tdd.md`.)
- **Pass interfaces as values, not pointers to interfaces** — you almost never need `*SomeInterface`.

## Static guarantees

- **Verify a type satisfies an interface at compile time with `var _ Iface = (*T)(nil)`** — this catches drift the moment a method signature changes.
- **Avoid `any`/`interface{}` unless the value truly may be of any type** — `any` says nothing and defeats static checking. At a boundary that must carry arbitrary data, convert to a concrete type as early as possible.
- **Never let a typed nil pointer escape through an interface return** — return a literal `nil` (see `go-style.md` and `go-errors.md`).

## Encode invariants in the type

- **Prefer a named type over a bare primitive when the value has a domain meaning** — `type OrderID string`, `type Severity int` — so the checker rejects passing the wrong string or int. This is the Go form of "make illegal states unrepresentable."
- **Use a small closed set of constants (`iota`, typed string constants) for a finite domain**, and validate at construction (`NewStatus(s string) (Status, error)`) so an invalid value can't exist downstream. (`craft-domain-modeling.md`.)
- **Use generics with type constraints for genuinely type-parametric code** (a bounded `LlmCall[T]`, a container) — state the constraint as the smallest interface the code actually uses, rather than reaching for `any` plus reflection.

## Substitutability

- **A type that satisfies an interface must honor the interface's full contract — behavior, not just signatures.** Matching method names and signatures is not enough; a substitute that violates the documented behavior breaks every caller written against the interface. Decide "is-a" by the substitution test, and use composition and small interfaces rather than any inheritance-style hierarchy. (`craft-abstraction.md`.)

## Domain types (DDD in Go)

The Go expression of `craft-domain-modeling.md`'s building blocks:

- **Value objects → small immutable structs, or defined `string`/`int`/enum types, built via a validating constructor (`NewStatus(s string) (Status, error)`) and returned by value** — no mutating pointer receivers; "change" means construct a new one. State enums, lifecycle phases, routing targets, and status codes are value objects.
- **Entities → a type with an explicit `ID` field that is the basis of equality** — keep the struct spare; don't lean on pointer identity.
- **Repositories → a narrow interface over the store** (`type OrderRepo interface { Find(id) (Order, error); … }`) that hides whether data comes from a database, an external CLI, or a test double — the pattern that makes domain logic unit-testable with no running backend.
- **Services → stateless functions or focused packages named for the verb** (validation, routing, state transition); keep the `LlmCall` and its structured-output schema on the service side as the typed boundary. (Package layout and the bounded-context seams: `go-modules.md`.)
