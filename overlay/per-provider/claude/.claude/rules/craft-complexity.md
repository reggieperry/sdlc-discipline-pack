---
paths:
  - "**/*.py"
  - "**/*.go"
  - "**/*.sh"
  - "**/tests/**"
---

# Complexity and module design

The discipline of keeping a system understandable and cheap to change. Sources: John Ousterhout, *A Philosophy of Software Design* (2nd ed) — complexity is the one thing to fight, and every rule below is a move against it — together with the module-level structuring discipline grounded in Parnas's information hiding and Liskov's data abstraction. Full reasoning, citations, and worked examples: `.claude/sdlc-discipline/guides/modularity-guide.md`.

> See `craft-abstraction.md` for the specification-and-substitutability theory underneath deep modules and information hiding (the same idea from the data-abstraction side — LSP, polymorphism by constraint, why encapsulation buys local reasoning), `craft-domain-modeling.md` for value objects, aggregates, bounded contexts, and the modeling side of Tell-Don't-Ask, `craft-refactoring.md` for removing complexity from existing code, `craft-documentation.md` for the comment and doc-comment discipline, and the active language overlay (`go-*.md`, the `python-*` set) for the language-specific expression of these rules.

## What complexity is

- **Treat complexity as anything structural that makes the system hard to understand or change** — not feature count, not lines of code. Judge a design by how hard the *next* change is.
- **Hunt its two causes: dependencies and obscurity.** A dependency is code that can't be understood or changed in isolation; obscurity is important information that isn't obvious. Every design move either reduces one or adds one.
- **Watch for the three symptoms: change amplification** (one decision forces edits in many places), **cognitive load** (how much a developer must hold in their head), and **unknown unknowns** (it isn't even clear what must change). Unknown unknowns are the worst — design so that the system is *obvious*.
- **Complexity is incremental — sweat the small stuff.** It accumulates from many small dependencies and obscurities, so adopt zero tolerance: a little added complexity now is a debt paid forever.

## Strategic, not tactical

- **Make a great design that also works, not working code that you'll clean up later.** Tactical programming (just ship the feature) is how systems rot; the cleanup never comes.
- **Invest continuously — roughly 10–20% of effort — in design.** Small, constant improvements; the payoff arrives in months, not years. Avoid the big up-front design (waterfall) and the never (tactical) alike.
- **When you modify existing code, leave the design as if it had been built with this change in mind from the start.** Resist the minimal local patch that buys a feature at the cost of a new special case.

## Simplicity calibration

"As simple as possible, but no simpler." Two failure modes the principle guards against:

- *Over-engineering* — frameworks, indirection layers, configuration knobs added without a concrete need.
- *Under-engineering* — happy-path-only code that skips idempotency, error isolation, or audit needed for production.

When proposing an approach, name the simpler option first. Add complexity only when the cheaper alternative paints into a corner. The "but no simpler" half is load-bearing — idempotency, optimistic concurrency, error isolation, and audit are the floor, not optimizations.

## Deep modules

- **Make modules deep: a simple interface over a powerful implementation.** The interface is the cost a module imposes on the rest of the system; the functionality is the benefit. Maximize benefit, minimize interface.
- **Judge a module by depth — functionality hidden divided by interface surface.** A deep module exposes a small interface that hides substantial implementation; a shallow module's interface roughly mirrors what it wraps. Small ≠ simple: a shallow module pays an interface cost without earning the encapsulation benefit.
- **Reject classitis — more, smaller modules is not better.** Many shallow modules each add interface and boilerplate; the system-level complexity is the sum of all those interfaces. Depth beats length: make functions deep first, short second, and never split a function into conjoined halves that can only be understood together.
- **It is more important that a module's interface be simple than that its implementation be simple** — most modules have more users than developers, so push the suffering onto the implementer.

## Information hiding

- **Make each module hide a design decision** — a data structure, an algorithm, a file format, a wire protocol — so that decision can change without touching anything else. Hiding information is what makes a module deep.
- **Treat information leakage as a top red flag: the same knowledge embedded in two modules.** It can leak through an interface or, worse, through a back door (two modules that both know a file format). When you see it, reorganize so the knowledge lives in exactly one place.
- **Avoid temporal decomposition.** Modules named `loader`, `parser`, `writer`, `validator` are red-flag verbs that structure code by the order operations run rather than by the knowledge each holds. Order belongs in the orchestrator that owns the sequence; stage modules organize by what knowledge they encapsulate. If two stages share a domain concept, the concept lives in a module both call. Temporal decomposition is the most common source of leakage — design around knowledge, not time.
- **Hide expected change behind an abstraction.** Think about what is likely to change and encapsulate it so the change stays local.
- **Encapsulation and information hiding are different.** Encapsulation bounds the blast radius of a change (all interaction through the API). Information hiding conceals *how* behind *what* so callers reason at the level of intent. Getters and setters on every field give the first without the second.

## The kinds of useful abstraction

A new module-level abstraction (a *level* — one or more files implementing one abstraction over shared resources) fits exactly one of these. If it fits none, it's a grab bag, not a level.

- **Resource** — wraps an external substrate (a broker, the LLM, a database, the network). Owns it exclusively. Translates failures to domain exceptions at the boundary.
- **Data** — hides layout or encoding behind operations on a frozen value type. Constructed only through validated entry points.
- **Generalization** — extracts an algorithm two or more levels share. Wait for the third call site before extracting (premature generalization is its own red flag).
- **Information-limiting** — pushes complexity down so the higher level can't see it. The hidden data becomes a resource of the lower level.
- **Change-encapsulation** — hides what's expected to change behind a stable signature. Test by writing a second implementation; if the signature has to change, it was leaking.

## Generality and special cases

- **Make modules somewhat general-purpose: functionality for today's need, interface general enough for more.** Over-specialization is the single greatest cause of complexity; a general interface is usually simpler, deeper, and smaller than the special-purpose one. Ask: "what's the simplest interface that covers all current needs, and in how many situations will this method be used?"
- **Eliminate special cases in code.** Design the normal path so it handles the edges with no extra `if` (an empty selection rather than a "no selection" flag). Fewer special cases means simpler, faster, more obvious code.
- **Push specialization to the top or bottom of the stack**, keeping the middle layers general — the way device drivers isolate device-specific code below a general interface. Specialization in a middle-layer interface leaks the caller's vocabulary downward; push it up to the application boundary or down into a driver, not into the middle.

## Layering, pulling down, and errors

- **Give each layer a different abstraction.** Adjacent layers with the same abstraction signal a problem. The sharpest symptom is the **pass-through method** (does nothing but call another with the same signature — pick one: expose the lower layer, push real work into the wrapper, or merge the layers) and the **pass-through variable** (an argument threaded through three or more frames that the intervening frames don't use — introduce a context object the caller injects rather than threading it by signature or smuggling it through globals).
- **Pull complexity downward.** A module has more callers than implementers, so the implementer suffers so the callers don't. When you hit unavoidable complexity, handle it inside the module rather than exporting it as configuration parameters or exceptions for every caller to deal with. Don't export a knob when a strong default would do — export only when a runtime operator will tune the value.
- **Define errors out of existence.** The best exception handling is none. In priority order: redefine the operation so the error case becomes the normal case (an `unset` that succeeds when the value is already gone); mask the exception in a low-level module; aggregate handlers at the boundary; crash for unrecoverable failures. A method dotted with `try`/`except` is usually leaking abstraction — consolidate. (Language-specific expression: the overlay's error rules.)

## Tell, don't ask

Behavior lives with the data it depends on. Keep decision-making inside the object that owns the state, and keep stage outputs typed.

- **Objects decide using their own state; callers tell, don't ask.**
  - Don't: `if account.balance - amount < 0` → Do: `account.charge(amount)`
  - Don't: `if permission.level >= required` → Do: `permission.allows(action)`
  - Don't: `if status == TERMINAL and ...` → Do: `status.is_terminal()`
- **Functions return typed output; never write to a shared state object.** No `state.foo = bar`, no `state.items.append(item)`. Return the data and let the caller wire it. A god object that everything reads and mutates is the antithesis of deep modules.
- **Interfaces return typed objects, never an untyped map (`dict[str, Any]`, `map[string]any`).** If a repository returns account data, it returns a typed `Account`, not a string-keyed bag. An interface that returns an untyped map has given up — type the return. (Domain identity and value-equality semantics: `craft-domain-modeling.md`.)
- **Don't reach across module or context boundaries to assemble data.** If a downstream stage needs context from an upstream one, receive it as a typed object — don't reach into a shared context to pull fields from several unrelated sources.
- **Domain invariants live on the owning aggregate, not in a separate orchestration stage.** A `ConfirmedReservation` is constructable only through `Schedule.book(request)`; the validation is arithmetic on the aggregate's own fields, with no external decision-making. (The aggregate and value-object machinery: `craft-domain-modeling.md`.)

## Object style — peers, composition, context independence

- **Categorize every collaborator as exactly one peer stereotype:** *dependency* (a required service — constructor parameter, no default), *notification* (a fire-and-forget listener — default to no-op), or *adjustment* (a policy or strategy — default to a sensible value). A bloated constructor usually conflates the three; re-categorize before splitting.
- **Make a composite simpler than the sum of its parts.** A composite's public surface is narrower than the union of its components'. `editor.set_value(money)`, not `editor.set_amount_field(...).set_currency_field(...)`. Exposing the parts means the composite is a leaky wrapper.
- **Keep objects context-independent.** An object holds no built-in knowledge of the system it runs in; whatever it needs about the larger environment is passed in at construction or as a method argument. A type drawing vocabulary from two domains is probably violating this — the exception is a bridging adapter whose stated purpose is translation.
- **No And's, Or's, or But's.** Describe what a module does in one sentence with no conjunction. "Loads documents *and* parses them" is two modules; "dispatches *or* caches" is two modules. When the description needs an "and," split.

## Module structure and hierarchy

- **Each level owns its resources exclusively.** No other level reads or writes them directly.
- **Imports go down only.** Lower levels never reference higher ones; the import graph is acyclic. A cycle is a design failure, not a style nit. Data connections between levels are explicit arguments and returns — no implicit common data across a boundary.
- **Default to composition over parameterization over inheritance.** Dependencies are constructor parameters — never instantiated inside, never imported and used as a singleton. A type-parameterized level depends on a *minimal* interface, defined in the consuming level (not the implementing one) and closed over its parameters. Use inheritance only for behaviorally substitutable subtypes (preconditions no stronger, postconditions no weaker, invariants preserved); never for code reuse — compose instead. (The substitutability test itself: `craft-abstraction.md`.)
- **Keep the public surface small.** Cap public names per module (roughly seven — above that, audit and either split or justify the aggregate). No mutable module-level state: read config once at load into an immutable value or frozen type. Underscore-prefix (or otherwise mark unexported) every name that isn't part of the contract, and never import another module's private names. Don't re-export dependency types just to widen the surface for nothing.
- **Constants and tunables are data resources of the level whose abstraction they parameterize.** No shared constants module across levels. If another level needs the value, expose a function — don't import the constant.

## Connections between modules

For every dependency A → B, the connection-as-assumption list includes: names, types, pre- and post-conditions, exceptions, side effects, and the performance envelope. If that list runs longer than a line, the connection is fat — narrow it.

- **Hide vendor protocols, encoding and storage layout, algorithms, and mutable state** behind the connection.
- **Don't hide named domain invariants, stable standard-library types** (a filesystem path, an arbitrary-precision decimal), **or the shape of the typed events** that flow between stages — those are the contract, not implementation detail.

## Cross-process boundaries

- **Worker functions are pure: bytes in, bytes out.** No globals, no class attributes, no shared state. They are top-level functions (not methods, not closures) so they can cross the boundary.
- **Pass a typed serialization format across the boundary, never a language-native pickled object.** Crossing processes coincides with crossing levels — don't fan out inside a level just for parallelism.
- **Cross-process handoff is a queue or a typed event, never shared state.**

## Obviousness, names, and comments

- **Design it twice.** For any significant interface, or any module whose work runs more than a day, sketch a second genuinely different approach — even a deliberately bad one — before committing; the contrast teaches you what makes the chosen design good. Capture the considered alternatives in the PR description or design record.
- **Choose names that are precise and consistent.** A vague or overloaded name is a latent bug. If a name is hard to pick, the underlying thing probably lacks a clean design.
- **Comments are governed by `craft-documentation.md`** — describe what isn't obvious from the code (units, invariants, who-frees-what, rationale), never restate it; write the interface comment *first*, before the body, and treat a long or hard-to-write one (drifting past a few lines, or leaking internal collaborators) as the canary for a shallow or muddled abstraction. That's design feedback — refactor the design, not the comment.

## Red flags — stop and redesign when you see one

Shallow module · classitis · information leakage · temporal decomposition (`loader`/`parser`/`writer` verbs) · overexposure (a common feature forces awareness of rare ones) · pass-through method · pass-through variable · repetition · special-general mixture · conjoined methods · god object (one type everything reads and mutates) · fat connection (public surface is an untyped map, splatted kwargs, or a downcast base) · leaky resource (returns a raw HTTP response, DB rows, or a vendor order object) · reach-around (a caller touches another module's private attrs, funcs, or state) · upward dependency · cyclic dependency · premature generalization (a generic before the second implementation exists) · inherited reuse (extends to share code) · exposed mutable (a public collection or field mutable from outside) · implicit precondition (a signature that doesn't name what it requires of inputs) · wrapping for its own sake (a one-method type that just stores a dependency) · comment repeats code · implementation detail in an interface comment · vague name · hard-to-pick name · hard-to-describe (the doc must be long to be complete) · nonobvious code.

## Self-audit (binary, no partial credit)

A module failing any item is not finished, no matter how green its tests are.

1. The name precisely labels its single abstraction — no conjunctions.
2. It fits exactly one kind of useful abstraction, and you can name which.
3. Its interface is deep — small surface over substantial hidden implementation; no pass-through methods or variables.
4. Public names are typed: no untyped maps, no escape-hatch `Any`.
5. Imports run only at or below it in the hierarchy; the import graph is acyclic.
6. Dependencies arrive via constructor or a level-permitted import — no sibling instantiation, no singletons.
7. No mutable module-level state.
8. If it declares an interface for others to implement, a second implementation (or a test double standing in for one) exists, and the interface is minimal — every method is actually called.
9. Public names stay within the cap (or the aggregate is justified); no private name is imported from outside its own module.
10. The connection-as-assumption list for each dependency fits on one line.
11. Cross-process boundaries are queues or typed events, not shared state.
12. A short written spec exists for the level (name; abstraction supported — one sentence, *what* not *how*; resources owned; hierarchy placement and process assignment; externally vs. internally accessible functions), and every externally accessible function specifies its arguments and returns *with legal bounds*, what it does (not how, including error handling), and resource-state expectations on entry and effect on exit.
