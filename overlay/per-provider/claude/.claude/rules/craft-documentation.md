---
paths:
  - "**/*.go"
  - "**/*.sh"
  - "**/*.py"
---

# Documentation and comments

The discipline of recording what the code cannot say — the designer's intent, the contract, and the design. Sources: John Ousterhout, *A Philosophy of Software Design* (2nd ed), the comments chapters; Steve McConnell, *Code Complete* (2nd ed), ch. 32 (self-documenting code); Robert C. Martin, *Clean Code*, ch. 4 (comments); the language doc conventions (Go Doc Comments, PEP 257, Javadoc, rustdoc); and *Software Engineering at Google*, ch. 10 (documentation). Code expresses the *what*; a comment exists for what code structurally cannot.

> See `craft-complexity.md` for the obviousness-and-naming side of this discipline and the design budget that funds it (this rule is the canonical home for comments), `craft-abstraction.md` for the specification-vs-implementation split a contract comment captures, `craft-tdd.md` for the comment-first/test-first parallel, and the active language overlay (`go-style.md`, the `python-*` set) for the comment and docstring mechanics — syntax, summary mood, and the Args/Returns/Raises/Errors/Panics/Safety sections this rule deliberately leaves to them.

## Why document at all

- **A doc comment is load-bearing: without it there is no abstraction.** If a caller must read a function's body to use it, the function has no abstraction — comments are the mechanism that hides complexity. Write every module and public function so a caller can use it correctly from the description alone — effect, inputs, outputs, special cases — without ever opening the body.
- **Write comments to capture what was in the designer's mind but had no code form** — rationale, units, invariants, the abstraction's informal half. A comment that paraphrases the adjacent code adds no information and a second thing to keep in sync. Before writing one, ask what a competent reader could *not* deduce from the names, types, and structure beside it, and write only that.
- **Split every comment by audience.** An *interface* (contract) comment serves the caller — what the unit does, its inputs, outputs, side effects, and constraints; *implementation* comments serve the maintainer — the strategy and the why. Put the contract above the declaration and the strategy inside the body; conflating them harms both readers.

## Module and package documentation

- **Give every module/package an overview of what it provides and its role** — the problem it solves and how its pieces fit, not a catalog of its functions (the symbol docs give those). Open with one sentence naming what the package provides, then the orienting context a newcomer needs to choose it and enter it.
- **Record a cross-module design decision once, in a central findable place, and reference it from the code.** A decision that spans modules has no single declaration to attach to; documenting it at each site causes drift and the information leakage `craft-complexity.md` warns against. Keep cross-cutting rationale in one conceptual doc, versioned and reviewed like code, and link to it rather than copy it.

## Public-API documentation — the caller's contract

- **Document every public/exported module, type, and function with a contract usable without reading the body.** Every major guide converges here — Go (every exported name), PEP 257 (every exported function and class), Javadoc and rustdoc (every public item). Write it from the caller's standpoint, in the vocabulary of the abstraction, not the representation: a type's doc states what an instance represents; a function's states what it returns or, for a side-effecting one, what it does.
- **Describe the contract — behavior and guarantees — not the current implementation.** An interface comment must not leak the algorithm ("implementation documentation contaminates interface" — Ousterhout); documenting behavior rather than mechanism is exactly what keeps the implementation substitutable (`craft-abstraction.md`). Performance a caller must plan around — complexity class, stability, allocation — is part of the contract; the algorithm that achieves it is not.
- **Make the contract complete: effect, inputs, outputs, failure modes, preconditions/invariants, and any concurrency or ownership the caller must honor.** Enumerate the obligations on both sides of the call boundary — what the caller guarantees going in, what the function guarantees coming out, and how it can fail — omitting a part only when it genuinely does not apply. (The overlay names the per-language failure-mode tag: `Raises:`, `# Errors`/`# Panics`, `@throws`, or a documented sentinel error.)

## Private and internal documentation — concise intent

- **Document private/internal code concisely — intent and design for a maintainer who will read the body, not a full external contract.** The asymmetry is built into the guides: PEP 257 mandates the public API and is silent on non-public members; Google requires public-API docstrings and otherwise only where the logic is non-obvious. A one-line statement of the function's job, plus — where the approach is non-obvious — a short note on the strategy and any invariant it maintains, and nothing that merely narrates the code.
- **Comment for non-obviousness, not for coverage — make the code obvious first, then comment only the residue.** "Good code is its own best documentation; as you are about to add a comment, ask how to improve the code so the comment isn't needed" (McConnell). Try a better name or an extracted function first; if that removes the need, fix the code; if no construct can carry the information, the comment has earned its place.

## Implementation and design comments — the body's strategy

- **Comment at the level of intent — say *why* and the design strategy, the thing a reader cannot recover from the code.** Code says how it works; a comment says why and what for. The test: a comment answers "what is this trying to achieve," never "what does this line do."
- **Lead a non-obvious body with a short comment naming the approach** — the algorithm's phases, the loop's invariant, why this structure — so the body reads as an instance of a stated plan. This is the bar to hold: a reader should follow the implementation from a description of *what it does and how it is designed*, without reverse-engineering it.
- **Pitch comments at two altitudes: lower-level for precision, higher-level for intuition; a same-level comment just repeats the code.** Lower-level comments pin what syntax can't — units, valid ranges, whether an interval is inclusive, who frees what, whether a value may be absent. Higher-level comments give the gist of a block the reader would otherwise reconstruct line by line. When a comment feels redundant, move it up (state the block's intent) or down (pin a detail) — or delete it.
- **Document what code structurally cannot — invariants, preconditions, units, ownership, and non-default concurrency guarantees.** A type is assumed single-threaded unless its doc states a stronger guarantee; document a non-obvious zero/empty value, a maintained invariant, a lifetime, or a panic condition — wherever the type system leaves it implicit.

## Self-documenting code vs comments — the partition

- **Let code carry the *what* and comments carry the *why*; the two camps are complementary once responsibilities are split.** Improve the code before reaching for a comment ("a comment compensates for our failure to express ourselves in code" — Martin); but write the comment when no name, type, or structure can carry the information — intent, rationale, contract, invariant. The twin anti-patterns are a comment papering over unclear code, and refusing all comments on a self-documenting-code dogma.
- **Reject the four excuses for not commenting.** "Self-documenting code" — names cannot carry rationale, units, or the abstraction's informal half. "No time" — a small, constant fraction of effort pays back across every later reader (`craft-complexity.md`'s design budget). "Comments rot" — keep them higher-level than the code and beside it, under the same review. "Every comment I've seen is worthless" — an argument for writing good ones, not none. Treat the contract as a deliverable.

## What not to comment — the anti-patterns

- **Never restate the code.** A comment that paraphrases the adjacent line adds reading cost and a second thing to keep in sync, with no information; delete it — or, if it was masking an unclear name, fix the name.
- **Delete the bad-comment catalog: noise and mandated boilerplate, commented-out code, position markers, attribution bylines, and changelog/journal entries.** Names carry intent, the type system carries shape, and version control carries history and authorship — a comment that duplicates any of these is cost without information.
- **Keep comments accurate and adjacent — a drifted comment is worse than none, because it misleads with authority.** Change the comment in the same commit and review as the code; document each fact once (DRY); keep history in version control, never in a comment.

## Write the comment first

- **Write the interface comment before the body and use it as a design tool — a hard-to-write comment is a design signal, not a chore.** If the public description won't come out crisply and implementation-independently, the abstraction is shallow or muddled — redesign before writing the code (the test-first parallel of `craft-tdd.md`, and the canary `craft-complexity.md` names). The diagnostic is the value.

## Red flags (stop and fix the cause)

Comment repeats code · implementation detail in an interface comment · a comment compensating for a bad name · commented-out code · a changelog kept in comments · a hard-to-write interface comment (a shallow abstraction). Each names a different fix — rewrite the code, move the detail into the body, rename, delete, or redesign — never an edit to the comment in place.
