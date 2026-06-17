---
paths:
  - "**/*.go"
  - "**/*.py"
  - "**/*_test.go"
  - "tests/**"
---

# Abstraction, specification, and substitutability

The theory of data abstraction that makes modules safe to build and change independently. Sources: Barbara Liskov & Stephen Zilles, *Programming with Abstract Data Types* (1974); Barbara Liskov, *Data Abstraction and Hierarchy* (1987, the origin of the substitution principle); David Parnas, *On the Criteria To Be Used in Decomposing Systems into Modules* (1972).

> Full reasoning, citations, and worked examples: `.claude/sdlc-discipline/guides/modularity-guide.md`.
>
> See `craft-complexity.md` for deep modules, information hiding, module shape, and naming (the same ideas from the design side); `craft-domain-modeling.md` for value objects and intention-revealing interfaces; `craft-refactoring.md` for extracting abstractions from existing code; `decoupling.md` for dependency direction across components; and the language overlay (`go-types.md`, `go-modules.md`, the `python-types.md` / `python-modules.md` set) for encoding these abstractions in the type system and package layout.

## An abstraction is a specification, not an implementation

- **Define every abstraction by what it does, not how — a specification separate from its representation.** A data abstraction is a set of objects characterized completely by the operations on them; the representation ("rep") is hidden behind those operations.
- **Permit many implementations behind one specification.** An implementation is correct if it provides the behavior the specification describes; correct implementations may differ in algorithm and performance and remain freely substitutable. The specification says what is important; everything else is free to change. Test this directly: write a second implementation behind the same signature. If the signature has to change to accommodate it, the rep was leaking.
- **Encapsulate the representation so no other module can depend on it.** Using code calls operations and never touches the rep. This is what lets a module be reimplemented without breaking its callers — the practical payoff of abstraction.

## The kinds of useful abstraction

A new unit of abstraction fits exactly one of these. If it fits none, it is a grab bag, not an abstraction. (Grab bags — `utils`, `helpers`, `common`, `misc` — are governed by the language overlay's module rules.)

- **Resource** — wraps an external substrate (a message broker, an LLM, a database, a network endpoint). Owns it exclusively. Translates substrate failures to domain errors at the boundary so callers never see the raw vendor type.
- **Data** — hides layout/encoding behind operations on an immutable value type. Constructed only through validated entry points, so an invalid instance cannot exist.
- **Generalization** — extracts an algorithm that two or more sites share. Wait for the third call site before extracting; before that, inline.
- **Information-limiting** — pushes complexity down so the higher level cannot see it. The hidden data becomes a private resource of the lower level.
- **Change-encapsulation** — hides what is expected to change behind a stable signature. (The "write a second implementation" test above is how you verify it.)

## Encapsulation buys local reasoning

- **Reason about one module at a time, against specifications.** With encapsulation you can implement, understand, or modify a module knowing only its own spec, the specs of the modules it calls, and nothing about its callers — because callers depend on the spec, not the code, and callees are summarized by their specs. Specifications are far smaller than implementations, so this is an enormous saving.
- **Encapsulate the decisions most likely to change** — storage layout, data structures, vendor protocols, machine or platform differences — so a change to that decision stays inside one module. A good design organizes itself around expected modifications.
- **Prefer enforced encapsulation over manual discipline.** Encapsulation guaranteed by the language (an unexported field, a sealed package boundary) or by a checked boundary (a linter rule) can be relied on without reading any code; encapsulation maintained only by convention degrades as the system is modified.

## Hierarchy and the direction of dependencies

- **Each abstraction owns its resources exclusively.** No other module reads or writes them directly. A caller that reaches around the interface to touch another module's private state has broken the abstraction, however convenient it looked.
- **Dependencies point one way: lower levels never reference higher ones.** Imports go down only. An upward import — a lower-level module importing from a higher one — is a design failure, not a style nit.
- **The import graph is acyclic.** A cycle between peers means the two share a hidden concept that wants its own abstraction; extract it rather than letting them import each other.
- **Connect levels only through explicit arguments and returns.** No implicit shared mutable state across a boundary. Where a boundary also crosses a process, connect through a typed message or queue, not a shared object — and let crossing the process coincide with crossing the abstraction, rather than fanning out inside one level purely for parallelism.

## Connections carry assumptions — keep them thin

For every dependency A → B, the connection is not just the function name. It is the full set of assumptions A makes about B: names, types, pre/post-conditions, errors raised, side effects, and performance envelope. If that list is long, the connection is fat and the two modules are not really separable.

- **Hide what is volatile or incidental** behind the connection: vendor protocols, encoding and storage layout, algorithms, mutable state.
- **Do not hide what is stable and load-bearing:** named domain invariants (a documented account-risk ceiling), stable standard-library types, and the shape of the typed events that flow between stages. Hiding these only widens the surface for nothing.
- **A contract that returns an untyped bag has given up.** An interface whose return type is `any` / `dict[str, Any]` / `interface{}` carries no specification; type the return so the connection actually constrains both sides.

## Subtyping and the substitution principle

- **Honor substitutability: a subtype's objects must be usable everywhere the supertype is expected, with the program's behavior unchanged.** A subtype must provide all the supertype's operations *and* the same behavior for them — matching names and signatures is not enough (a stack is not a subtype of a queue though both `add` and `remove`).
- **State the behavioral conditions precisely.** A valid subtype weakens no precondition (it accepts everything the supertype accepted), strengthens no postcondition (it promises everything the supertype promised), preserves every supertype invariant, and confines its side effects to a subset of the supertype's. A type that violates any of these is not substitutable, whatever the compiler says.
- **Distinguish a subtype (a semantic, behavioral relationship) from a subclass (a code-reuse mechanism).** Use the substitution test to decide subtyping; never assume that inheriting or embedding code makes one type a subtype of another.
- **Do not abuse inheritance to share an implementation.** Implementing one type using another as its representation achieves reuse without claiming a subtype relationship, and without the encapsulation violations that implementation inheritance invites. Keep "is-a-subtype-of" and "is-implemented-using" separate. (The language overlay expresses this with composition and small interfaces over any inheritance hierarchy.)

## Composition over parameterization over inheritance

- **Default to composition.** Dependencies arrive as constructor parameters (or explicit fields wired at construction). Never instantiated deep inside the module, never reached for as an imported singleton.
- **Reach for type parameters only when a real second case exists.** A type-parameterized abstraction depends on a minimal, closed contract — one that refers only to its own type parameters — and that contract is defined in the *consuming* module, not the implementing one, so it requires exactly the operations the consumer uses and no more (a sort needs its elements comparable, nothing else). Introducing `Generic[T]` / type parameters before the second implementation exists is premature; inline it and re-extract on the third call site.
- **Use inheritance only for genuinely LSP-substitutable subtypes** (preconditions no stronger, postconditions no weaker, invariants preserved, side effects a subset). Inheritance purely for code reuse is forbidden; compose instead.

## Build abstractions incrementally

- **Discover abstractions as the design progresses, one decision at a time.** You will know only some of an abstraction's operations early; add operations as using code reveals the need. Build the program one decision at a time and delay each until you have the information to make it well.
- **Introduce a type to limit the spread of information.** When a representation detail threatens to leak across modules, wrap it in a new abstract type whose operations are the only access — confine the blast radius of a future change to one cluster.

## Antipatterns

- **God object** — one type or module that everything reads and mutates. A shared mutable state-bag threaded through every stage is the canonical case; replace it with typed values passed explicitly.
- **Fat connection** — the public surface is an untyped bag (`any`, `dict[str, Any]`, `**kwargs`) or a downcast base type, so the connection constrains nothing.
- **Leaky resource** — a resource abstraction returns the raw substrate type (an HTTP response object, a database row, a vendor SDK order) instead of a domain value, so callers depend on the substrate.
- **Reach-around** — a caller touches another module's private fields, functions, or state instead of going through the interface.
- **Upward dependency** — a lower-level module imports from a higher one.
- **Cyclic dependency** — peer modules import each other.
- **Premature generalization** — a type parameter or shared abstraction introduced before the second implementation exists.
- **Inherited reuse** — a type extends another only to share code. Compose instead.
- **Exposed mutable** — a public collection or struct is mutable from outside; return an immutable/read-only view instead.
- **Implicit precondition** — the signature does not name what it requires of its inputs, so the requirement lives only in the caller's head.
- **Wrapping for its own sake** — a one-method type that only stores a dependency and forwards to it adds an interface and buys nothing.

## Self-audit (binary, no partial credit)

1. The name labels a single abstraction precisely. No conjunctions.
2. A written spec exists: name; abstraction supported (one sentence, *what* not *how*); resources owned; placement in the hierarchy; externally vs. internally accessible operations.
3. Every externally accessible operation has a spec: arguments and returns with their legal bounds, what it does (including error handling), and the resource-state expectations on entry and effect on exit.
4. Public names are typed. No `any`, no untyped bag returns.
5. Imports point only at or below this module in the hierarchy.
6. Dependencies arrive via constructor or a level-permitted import. No sibling instantiation, no singleton reach.
7. No mutable module- or package-level state. Configuration is read once into an immutable value.
8. The module fits exactly one kind of useful abstraction, and you can name which.
9. If it declares an interface, a second implementation exists (or is imminent and named).
10. If it is type-parameterized, the contract is minimal — every operation in it is actually called.
11. The public surface is small (the language overlay sets the threshold); above it, either split or justify the aggregate.
12. No private (underscore- or lower-case-unexported) name is reached from outside its own module.
13. The connection-as-assumptions list for each dependency fits on one line.
14. Cross-process boundaries are typed messages or queues, not shared state.
15. End-of-design criteria met: the hierarchy is known, the structure exists, specs exist, interfaces are defined, and test cases are identified per abstraction.

A module failing any item is not finished, no matter how green its tests are.
