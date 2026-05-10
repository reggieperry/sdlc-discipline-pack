# Refactoring — guide

> Elder examples in this guide are illustrative — they show what Fowler's
> discipline looks like applied to a non-trivial codebase. The principle
> applies across projects; substitute your project's domain vocabulary.

A principal-engineer reference for using Fowler's discipline and catalog to safely improve the design of existing code (Elder is the illustrative codebase here). The thesis is Fowler's: refactoring is a controlled technique for improving the design of existing code without changing its observable behavior. The mechanics are recipes — each refactoring has explicit steps that minimize the chance of breaking the system. The vocabulary names the moves so the team and the chain can communicate precisely about transformation. The spine of this guide is *Refactoring: Improving the Design of Existing Code*, 2nd edition (Fowler, Addison-Wesley, 2018), with citations by chapter and page where the position depends on the primary text. Where Liskov's modularity guide, the DDD guide, or the GOOS guide already cover a point, this guide cites them rather than restating.

A vocabulary note before anything else. **Refactoring** in Fowler's sense is *not* "rewriting" or "cleanup as you go" or "improving code quality." It is a specific technique with a definition that excludes most of what people loosely call "refactoring" in practice:

> "Refactoring (noun): a change made to the internal structure of software to make it easier to understand and cheaper to modify without changing its observable behavior."
> "Refactor (verb): to restructure software by applying a series of refactorings without changing its observable behavior."
> (Chapter 2)

Two consequences. First, if behavior changes, it isn't refactoring — it's a feature change or a bug fix. Second, a refactoring is composed of multiple smaller refactorings each of which preserves behavior. The discipline is in the chain of small steps, not in any single transformation.

## 1. Foundations

### 1.1 Why Fowler for Elder

Elder's standards stack already commits to design discipline at three layers. Liskov's modularity guide tells the team what good module structure looks like (level ownership, narrow interfaces, opaque types). Evans's DDD guide tells the team how domain concepts get expressed (entities, aggregates, ubiquitous language). Freeman and Pryce's GOOS guide tells the team how tests drive design discovery (walking skeleton, listening to tests, mocks as design feedback).

What's missing — and what Fowler supplies — is the discipline of **safe transformation**. Modularity tells you the destination; Fowler tells you the road. DDD tells you what aggregates should look like; Fowler tells you the mechanics of moving an existing entity-like dictionary into a proper aggregate without breaking the system. GOOS tells you to listen to test pain; Fowler tells you what to do once you've heard it.

The leverage is concentrated in places where Elder will be modifying existing code rather than writing it from scratch:

- **Build item #1 (pipeline engine refactor)** — the largest refactoring effort in the build plan. Replacing `core/pipeline.py` with a typed-event coordinator is a series of refactoring moves: Extract Function, Move Function, Replace Constructor with Factory, Replace Conditional with Polymorphism. Fowler's catalog gives the chain the moves to name and the mechanics to apply.
- **Phase E1 final extraction pass** — lifting the project-agnostic `.claude/` content into a Gas City pack involves Move Field (across files), Rename, and Extract Class at the directory level.
- **The patch loop scope-creep we hit on PR #117** — the chain found mypy errors during review, made fixes in production code, bundled them with the chore. Fowler's Two Hats discipline says: refactoring and feature work are separate modes; never combine. A rule that names this would have caught the scope creep before it shipped.
- **Cleanup chores like #78** (adding `from __future__ import annotations` to multiple modules) — these are refactorings (specifically, mechanical Move-equivalent moves at file scope). The chain executes them well today but without the vocabulary; with it, the work is shaped explicitly as a series of named moves.

### 1.2 The thesis

Fowler's chapter 2 opens with three claims that everything else builds on:

1. **Refactoring improves design.** Without refactoring, the design of a program decays as people make changes; programmers stop understanding the code, lose track of where things are, and start working around the existing design rather than improving it. (Chapter 2, "Why Should We Refactor?")
2. **Refactoring is the way you understand code.** Fowler argues that the best way to understand unfamiliar code is to refactor it: the act of trying to extract a function or rename a variable forces you to confront what the code actually does, and the resulting structure makes future readers' lives easier. (Chapter 2, "Comprehension Refactoring")
3. **Refactoring helps you find bugs.** Not because refactoring fixes bugs (it doesn't change behavior) but because refactoring forces you to read code carefully enough to notice that something is wrong. The improved structure also makes bugs more visible.

> "Programming is in many ways a conversation with a computer. I write code that tells the computer what to do, and it responds by doing exactly what I tell it. In time, I close the gap between what I want it to do and what I tell it to do. Programming is all about saying exactly what I want." (Chapter 2, "What's in a Name?")

The operative claim: a refactored codebase is one where saying exactly what you want is easier next time.

### 1.3 The Two Hats — the most important rule

Fowler's most consequential discipline rule, and the one most easily violated in practice:

> "When I use refactoring to develop software, I divide my time between two distinct activities: adding functionality and refactoring. When I add functionality, I shouldn't be changing existing code; I'm just adding new capabilities. ... When I refactor, I make a point of not adding functionality; I only restructure the code." (Chapter 2, "When Should We Refactor?")

You wear one hat or the other. Never both at once. The reason: combining refactoring with feature work doubles the failure surface. If a test breaks during a combined change, you can't tell whether it's the refactor or the feature that broke it. If you keep them separate, a broken test under the refactoring hat means you broke the refactor; a broken test under the feature hat means you broke the feature. Each is recoverable in isolation.

For Elder, this lands directly on three patterns surfaced during ADW operation:

- **The patch-loop scope-creep on PR #117.** The chain wore both hats simultaneously: feature ("switch verify.sh to fail-on-error") and refactoring ("fix mypy errors blocking the strict mode"). The result was a single PR mixing the chore with production-code edits in a sensitive file. Two-Hats discipline says: file the mypy fixes as separate work, ship the verify.sh change in isolation. The chain currently has no rule that says this.
- **Build item #1 (pipeline refactor) approached as a single deliverable.** Treated as one big task, the refactor mixes "extract typed events" (refactoring) with "add checkpointing infrastructure" (feature) with "wire migration of 4 agents" (mixed). The Two Hats discipline forces the team to break this into a sequence: refactor the pipeline shape first under one hat, then add checkpointing under the other, then migrate agents under the first hat again.
- **Cleanup PRs that smuggle small features.** A PR titled "rename for clarity" that also adjusts the function's behavior. Two Hats catches this on review.

The chain enforcement: when a refactoring rule fires on a PR, the rule asks "is this PR pure refactoring? Pure feature? Or both?" If both, escalate. If pure refactoring, behavior tests should pass without modification. If pure feature, no existing function signatures should change without explicit reason.

### 1.4 When to refactor

Fowler distinguishes four refactoring modes (Chapter 2, "When Should We Refactor?"):

- **The Rule of Three** — refactor on the third occurrence. The first time you do something, just do it. The second time, you wince at the duplication but do the same thing again. The third time, refactor. This is a calibration against premature abstraction (Knuth: "premature optimization is the root of all evil"). Two cases of duplication may be coincidental; three suggests a real pattern.
- **Preparatory refactoring** — refactor before adding a feature, to make the feature easy to add. Fowler's signature line:

  > "When you have to add a feature to a program but the code is not structured in a convenient way, first refactor the program to make it easy to add the feature, then add the feature." (Chapter 1)

  This is the most common refactoring trigger in disciplined development. The feature requirement reveals that the existing structure isn't quite right for what's coming; reshape the structure first, then the feature drops in cleanly.
- **Comprehension refactoring** — refactor to understand code. As you read, you notice things you don't immediately grasp. Renaming a variable to its actual purpose, extracting a method to give a chunk of logic a name, clarifying a magic number with a constant — these are reading-as-writing moves. The understanding goes from your head into the code.
- **Litter-pickup refactoring** — small fixes during normal work. You're touching a function for an unrelated reason; you notice a bad name or a tangled conditional; you fix it on the spot if it's quick. The boundary between this and Two-Hats is judgment: a true litter-pickup is two minutes and clearly improves clarity; anything bigger should be a separate refactoring commit or a deferred ticket.

For Elder, the Rule of Three explains why we don't extract abstractions on the first chain dispatch — even when we suspect a pattern. Phase E0's decoupling rule waited for the second pattern instance before being authored. The five decomposition templates in E2 will be calibrated against accumulated story-decomposition data, not authored speculatively.

Preparatory refactoring is the right framing for build-plan #19 (deployable unit + T7920 setup) — before items #1-#13 can ship a paper-trading system, the ground needs to be prepared. The structural work is the precondition for the feature work.

### 1.5 Solid tests as a precondition

Refactoring requires solid, self-checking tests. Without them, the chain has no way to verify behavior preservation, and the discipline collapses.

> "Whenever I do refactoring, the first step is always the same. I need to ensure I have a solid set of tests for that section of code. The tests are essential because even though I will follow refactorings structured to avoid most of the opportunities for introducing bugs, I'm still human and still make mistakes." (Chapter 1, "The First Step in Refactoring")
> "Before you start refactoring, make sure you have a solid suite of tests. These tests must be self-checking." (Chapter 1)

For Elder this composes with the GOOS guide. GOOS's binding-tests discipline (RISK-001 through RISK-012, plus the indicator invariants in `docs/elder-invariants.md`) is the foundation that makes refactoring of the risk and indicator domains possible. Build item #1's pipeline refactor is safe to attempt because the existing agents have unit-level invariants the refactor must preserve.

If you find yourself wanting to refactor code without tests, write **characterization tests** first (Fowler chapter 4, plus Michael Feathers' *Working Effectively with Legacy Code* for the canonical treatment). A characterization test captures the current behavior — whatever that is, including bugs — so that subsequent refactoring can verify the behavior hasn't changed. Once the characterization tests pass on the original code, the refactor is safe; if a characterization test then exposes a bug, that's a separate fix under the feature hat.

## 2. Vocabulary

- **Refactoring (verb)** — to restructure software without changing behavior, applying a sequence of refactorings.
- **Refactoring (noun)** — a single named transformation with a recipe: e.g., Extract Function, Rename Variable, Move Field. Each entry in Fowler's catalog is a noun-form refactoring.
- **Two Hats** — refactoring mode versus feature mode; never both at once. The single most-violated discipline rule.
- **Preparatory refactoring** — restructure before adding a feature, to make the feature easy to add.
- **Comprehension refactoring** — restructure to understand code, persisting that understanding into the structure.
- **Litter-pickup refactoring** — small fixes during normal work; bounded by judgment about scope.
- **Bad smell** — a characteristic of code that suggests refactoring is warranted. Bad smells are intuitions made explicit; the catalog in chapter 3 is the team's shared vocabulary.
- **Self-checking test** — a test whose result is binary (pass/fail) without human inspection. Required for refactoring.
- **Characterization test** — a test that captures current behavior (including bugs) as a baseline for refactoring code that lacks tests.
- **The Rule of Three** — refactor on the third occurrence of duplication; not the first or second.
- **The compile-test-commit cycle** — make one refactoring move, compile, run tests, commit. Repeat.
- **Mechanics** — the explicit step-by-step recipe for a named refactoring. Each catalog entry has its own mechanics.
- **Local variable** — a variable whose scope is the function being refactored. Treatment differs from parameters and globals during Extract Function.
- **Tested vs untested change** — Fowler distinguishes "change you've tested" from "change you intend." Behavior-preservation requires the former; intent without test verification is hope.

## 3. The mechanics

The recipes that make refactoring safe rather than risky.

### 3.1 Small steps

> "Refactoring changes the programs in small steps, so if you make a mistake, it is easy to find where the bug is." (Chapter 1)

The smaller the step, the less surface area for error. A single Extract Function on a five-line block is a small step. A "rewrite this 200-line function into seven smaller functions all at once" is not — it's a rewrite, not a refactor. The chain should always prefer the smallest-recognizable refactoring move that makes progress.

Per-step pattern: identify the target, name the move (from the catalog), apply the recipe, run tests, commit. Five named operations, in that order. Never skip the test step. Never skip the commit step before the next move.

### 3.2 Tests passing at each step

After every refactoring move, run the tests. If they pass, commit and move on. If they fail, you have one of three situations:

- **You broke the refactor.** Revert to the previous commit. Reapply the move more carefully (perhaps in even smaller sub-steps).
- **The test was wrong about expected behavior.** This means you discovered a bug in the existing code or a bug in the test. Stop refactoring; switch to the feature hat to fix the underlying issue (or file it as a separate bug); then resume refactoring.
- **The test was order-dependent or non-deterministic.** Fix the test (under the feature hat or as a separate cleanup) before resuming refactoring.

Never proceed with refactoring while a test is failing. Never "fix" a test by changing its assertion to match the new (broken) behavior. The first move is to understand why the test failed.

### 3.3 Compile-test-commit rhythm

Each refactoring move is followed by:

1. **Compile** — make the language's type checker happy (where applicable). For Python, this is `mypy --strict` if available, plus running the test loader. For statically typed languages, this is the actual build.
2. **Test** — run the relevant test suite. For Elder, this means at minimum `uv run pytest tests/<related-area> -v`. For changes that cross modules, the full suite.
3. **Commit** — make a local commit with a descriptive message naming the refactoring move. Examples:
   - `refactor: extract calculate_position_size from RiskAgent.evaluate`
   - `refactor: rename Trade.amt to Trade.notional_value`
   - `refactor: move ImpulseColor enum from agents/scanner_agent.py to indicators/elder.py`

The local commits are the safety net. If something goes wrong three moves later, you can always `git reset --hard` to the most recent good commit and try again.

For long sequences of refactoring moves that the team is happy with, squash before pushing: `git rebase -i` with squash directives consolidates the per-move commits into a single conceptually-coherent commit (or a small number of them) on the branch. The squash commit message should still name the work as refactoring, distinct from feature commits.

### 3.4 Naming the move

> "I have a procedure for doing it that minimizes my chances of getting it wrong. I wrote down this procedure and, to make it easy to reference, named it Extract Function (106)." (Chapter 1)

Fowler's catalog gives the team a shared vocabulary. When a chain says "I'm going to refactor this function," that's vague. When the chain says "I'm going to apply Extract Function on lines 45–52, then Inline Variable on lines 38–39," that's a precise contract: the reader knows exactly what to expect, the mechanics are documented, and the move is verifiable.

The chain should always name the moves it's making. The audit phase should verify that each named move was actually applied (not a different move with the same name). PR descriptions for refactoring work list the moves applied in sequence.

### 3.5 The two-hats commit pattern

One commit per hat-pair. Don't interleave commits like:

- `refactor: extract calculate_size`
- `feat: add new short-selling logic`
- `refactor: rename calculate_size to compute_position_size`

Instead, complete one hat's work, then switch:

- `refactor: extract calculate_size`
- `refactor: rename calculate_size to compute_position_size`
- `feat: add new short-selling logic`

Or even better, two separate PRs. The hat-pair separation makes each PR easier to review and easier to revert independently if needed.

## 4. Bad smells in code (chapter 3)

Code smells are the team's shared diagnostic vocabulary. Each smell is a heuristic — not a hard rule, but a signal that warrants attention. Fowler's chapter 3 catalogs 24 smells. The selection below covers the ones that show up most in the kind of code Elder produces, with the refactoring move(s) that typically address each.

| Smell | What it looks like | Typical refactoring response |
| ----- | ------------------ | ---------------------------- |
| **Mysterious Name** | A variable, function, or class whose name doesn't communicate what it is. `tmp`, `data`, `process_it`, `Manager` | Rename Variable, Rename Function, Rename Field |
| **Duplicated Code** | The same expression in two or more places — exact or near-exact | Extract Function (and call it from both sites); Pull Up Method (when the duplication is across subclasses); Form Template Method |
| **Long Function** | A function long enough that you can't see the whole thing on screen, or that requires comment headers to explain its sections | Extract Function (split by section); Replace Temp with Query; Decompose Conditional; Replace Loop with Pipeline |
| **Long Parameter List** | A function with so many parameters that callers struggle to remember the order | Replace Parameter with Query; Preserve Whole Object (pass the containing object instead of its fields); Introduce Parameter Object |
| **Global Data** | Modifying global state from many sites | Encapsulate Variable (wrap the global behind a function); for module-level mutable state, refactor to dependency injection |
| **Mutable Data** | Data that's modified in many places, where the mutation drives bugs | Encapsulate Variable; Split Variable; Replace Derived Variable with Query (compute on demand instead of caching); Combine Functions into Class |
| **Divergent Change** | One module changes for many different reasons | Split Phase; Move Function (separate the responsibilities into different modules); Extract Class |
| **Shotgun Surgery** | One change requires many small edits across many modules | Move Function; Move Field; Combine Functions into Class; Inline Function (collapse trivial dispatchers); Split Phase |
| **Feature Envy** | A function in module A spends more time accessing module B's data than its own | Move Function (move the function to module B); Extract Function then Move (when only part of the function is envious) |
| **Data Clumps** | The same group of fields appearing together in many places | Extract Class (the clump is a value type); Introduce Parameter Object (the clump is a parameter group) |
| **Primitive Obsession** | Strings and ints used where a domain type would be clearer | Replace Primitive with Object; Replace Type Code with Subclasses; Extract Class |
| **Repeated Switches** | The same `switch`/`if` chain on the same field appearing in many places | Replace Conditional with Polymorphism; Replace Type Code with Subclasses |
| **Loops** | Loops that mix concerns (filter + transform + accumulate in one block) | Replace Loop with Pipeline (split into composable stages: filter, map, reduce) |
| **Lazy Element** | A class or function whose only purpose is delegation | Inline Function; Inline Class |
| **Speculative Generality** | Abstractions added "in case" they're needed, never actually used | Collapse Hierarchy; Inline Function; Inline Class; Remove Dead Code |
| **Temporary Field** | A field that's only used in some circumstances | Extract Class (the field belongs in a state-specific subclass); Introduce Special Case (when the absence has a domain meaning) |
| **Message Chains** | Long sequences like `a.getB().getC().getD().getE().getF()` | Hide Delegate (each level wraps the next); Extract Function |
| **Middle Man** | A class whose methods all delegate to another class | Remove Middle Man; Inline Function |
| **Insider Trading** | Modules that share too much private state via "getter" workarounds | Move Function; Move Field; Hide Delegate; Replace Bidirectional Association with Unidirectional |
| **Large Class** | A class with too many fields or methods to hold in your head | Extract Class; Extract Subclass; Extract Interface |
| **Alternative Classes with Different Interfaces** | Two classes that do similar things but with different APIs | Rename Function (align the names); Move Function; Extract Superclass |
| **Data Class** | A class with only fields and accessors, no behavior | Move Function (push behavior from clients into the class); Encapsulate Record; Encapsulate Collection |
| **Refused Bequest** | A subclass that doesn't use most of its parent's interface | Replace Subclass with Delegate; Replace Superclass with Delegate |
| **Comments** | Comments used to explain complicated code | Extract Function (give the chunk a name); Rename Function; Introduce Assertion (when the comment expresses an invariant) |

The "Comments" smell deserves special note. Fowler's argument: comments are sometimes necessary, but often they're "deodorant" applied to code that smells. A comment explaining why a block of code does what it does is often a candidate for Extract Function with a clarifying name. The comment becomes the function name, and the code becomes self-explanatory.

For Elder, this aligns with the existing CLAUDE.md guidance: "default to writing no comments. Only add one when the WHY is non-obvious." The smells catalog gives the chain a structured way to recognize when a comment is actually requesting a refactoring.

### 4.1 Reading code smells

A code smell is not by itself a defect. It's a hypothesis worth testing. The proper response when you encounter a smell:

1. **Name it.** Use the catalog name. "This function smells of Long Function" or "These three sites are Duplicated Code."
2. **Diagnose.** Why is the smell present? Is it intrinsic to the problem, or is it accidental complexity? Some Long Functions are genuinely the simplest form of a complicated calculation; others are tangled flow control that wants to be extracted.
3. **Choose a refactoring move.** From the catalog. Name the move and the target.
4. **Check Two Hats.** Are you currently in refactoring mode? If you're under the feature hat, file the smell as a deferred refactoring or pause feature work to do preparatory refactoring under the refactoring hat.
5. **Apply the mechanics.** Small steps, tests passing, commits.

### 4.2 Living with smells

Not every smell gets fixed immediately. The team has a finite refactoring budget; some smells are not yet worth fixing because the code in question doesn't change often, or because the right refactoring requires understanding the code more deeply, or because the team is focused on other work.

Fowler's advice: **track smells but don't reflexively fix them**. The Rule of Three applies — a smell appearing once might be tolerable; appearing three times in code that's actively being modified is a clear signal. The chain's audit phase should flag smells but not block on them; the operator and the team prioritize.

## 5. The refactoring catalog (chapters 5–12)

Fowler's catalog organizes refactorings by theme. The selection below covers the most generally-applicable moves. Each is named, briefly described, and given a representative trigger smell. For full mechanics, consult Fowler's catalog directly — each catalog entry has explicit step-by-step recipes.

### 5.1 The basics (chapters 6, 7)

- **Extract Function (Fowler p.106)** — Take a code fragment with a coherent purpose and turn it into a function with a name that describes what it does. Trigger: Long Function, Comments. Most-used refactoring in any practitioner's toolkit.
- **Inline Function (p.115)** — Replace a function call with the function's body. Trigger: Lazy Element, Middle Man. The inverse of Extract Function — sometimes a function is so trivial that the indirection costs more than it saves.
- **Extract Variable (p.119)** — Take an expression and assign it to a name. Trigger: hard-to-read inline calculations, Mysterious Name on a complex expression.
- **Inline Variable (p.123)** — Replace a variable with its expression. Trigger: a variable that doesn't add to readability over the expression itself.
- **Change Function Declaration (p.124)** — Add, remove, rename, or reorder parameters. Mechanics include "Migration" form (parallel functions; deprecate old) for cases where direct renaming would break too much.
- **Encapsulate Variable (p.132)** — Wrap a directly-accessed variable (often a global or module-level field) behind a getter and setter. Foundation for further refactoring — once encapsulated, you can change the underlying representation.
- **Rename Variable (p.137)** — Change a variable's name. Most common comprehension-refactoring move.
- **Introduce Parameter Object (p.140)** — Replace a recurring group of parameters with a class containing them. Trigger: Data Clumps, Long Parameter List.
- **Combine Functions into Class (p.144)** — When several functions share data, package them as methods on a class. Trigger: functions that always pass the same parameters around.

### 5.2 Encapsulation (chapter 7 continued)

- **Combine Functions into Transform (p.149)** — When data needs derived values computed many ways, pipe it through a single transform that adds all the derived values. Trigger: scattered enrichment of the same input.
- **Split Phase (p.154)** — When code mixes two concerns (e.g., parsing data and computing on it), separate them into sequential phases with a clear hand-off between. Trigger: Divergent Change, Long Function with section comments.
- **Encapsulate Record (p.162)** — Replace a record (dict, struct, plain dataclass) with a class that controls access to its fields. Trigger: Primitive Obsession, Data Class.
- **Encapsulate Collection (p.170)** — Don't expose a collection field directly; provide methods that mediate access. Trigger: clients mutating an "owned" collection from outside.
- **Replace Primitive with Object (p.174)** — Replace a primitive (string, int) representing a domain concept with a small class. Trigger: Primitive Obsession.
- **Replace Temp with Query (p.178)** — Replace a local temporary variable with a function call that computes the value. Trigger: temp variable used in many places, holding a value that could be computed.
- **Extract Class (p.182)** — Pull related fields and methods out of a large class into a smaller one. Trigger: Large Class, Data Clumps.
- **Inline Class (p.186)** — Merge a small class back into another when the abstraction isn't pulling its weight. Trigger: Lazy Element.
- **Hide Delegate (p.189)** — When client code accesses a delegate through a holder (`a.getB().doSomething()`), wrap it on the holder (`a.doSomethingViaB()`). Trigger: Message Chains.
- **Remove Middle Man (p.192)** — When a class delegates so much that clients should just talk to the delegate directly, expose the delegate. Trigger: Middle Man.

### 5.3 Moving features (chapter 8)

- **Move Function (p.198)** — Move a function from one module/class to another. Trigger: Feature Envy, Shotgun Surgery, Insider Trading.
- **Move Field (p.207)** — Move a field from one class to another. Trigger: a field accessed primarily by another class.
- **Move Statements into Function (p.213)** — Move statements that always precede or follow a function call into the function itself. Trigger: setup/teardown logic duplicated at every call site.
- **Move Statements to Callers (p.217)** — The inverse: when statements inside a function vary by caller, push them out. Trigger: a function whose callers all need slightly different surrounding logic.
- **Replace Inline Code with Function Call (p.222)** — When code does something a function already does, call the function. Trigger: Duplicated Code where one site already exists as a function.
- **Slide Statements (p.223)** — Reorder statements so that related code is together. Foundation move for many other refactorings (especially Extract Function — slide first, then extract a contiguous block).
- **Split Loop (p.227)** — When a loop does two things, split it into two loops each doing one thing. Trigger: Long Function, comments inside loops naming distinct phases.
- **Replace Loop with Pipeline (p.231)** — Replace an imperative loop with a chain of collection operations (filter, map, reduce in Python). Trigger: Loops smell.
- **Remove Dead Code (p.237)** — Delete code that's never executed. Trigger: Speculative Generality, Dead Code.

### 5.4 Organizing data (chapter 9)

- **Split Variable (p.240)** — When one variable serves multiple purposes, split it into purpose-specific variables. Trigger: Mutable Data smell, variable reused for unrelated values within a function.
- **Rename Field (p.244)** — Change a field name. Mechanics distinct from Rename Variable when the field is widely accessed (need migration form).
- **Replace Derived Variable with Query (p.248)** — Don't cache values that can be computed. Trigger: a field maintained in sync with other fields, with synchronization bugs.
- **Change Reference to Value (p.252)** — Replace a mutable reference with an immutable value. Trigger: shared mutability, aliasing bugs.
- **Change Value to Reference (p.256)** — The inverse, when many copies of "the same" value should actually be one shared object. Trigger: changes to one copy not propagating to others.

### 5.5 Simplifying conditionals (chapter 10)

- **Decompose Conditional (p.260)** — Extract the conditional, the then-branch, and the else-branch into named functions. Trigger: complex conditional whose branches are hard to read.
- **Consolidate Conditional Expression (p.263)** — When several checks lead to the same result, combine them into a single check (often via an extracted predicate function). Trigger: a chain of ifs with identical bodies.
- **Replace Nested Conditional with Guard Clauses (p.266)** — Flatten nested ifs by handling early-exit cases at the top with guard clauses (`if not valid: return`). Trigger: deep nesting, the right-drift smell.
- **Replace Conditional with Polymorphism (p.272)** — When a conditional dispatches on a type field, replace with subclasses (or strategy pattern). Trigger: Repeated Switches.
- **Introduce Special Case (p.289)** — When `null` checks are scattered, introduce a Null Object that handles the absent case and removes the conditionals. Trigger: pervasive null-handling, NullPointerException-prone code.
- **Introduce Assertion (p.302)** — Make implicit assumptions explicit with assertions. Trigger: comments naming preconditions, repeated defensive checks.

### 5.6 Refactoring APIs (chapter 11)

- **Separate Query from Modifier (p.306)** — A function should either return a value (query) or modify state (command), not both. Trigger: a function that returns something AND has side effects, particularly when test setup ignores the return.
- **Parameterize Function (p.310)** — Two functions doing the same thing with different values become one function with a parameter. Trigger: Alternative Classes with Different Interfaces (when the difference is just a value).
- **Remove Flag Argument (p.314)** — A boolean parameter that switches behavior should be split into two functions, one per branch. Trigger: callers always passing a literal `true`/`false`; complex internal branching on the flag.
- **Preserve Whole Object (p.319)** — Pass an object instead of pulling fields out and passing them. Trigger: Long Parameter List with all fields from one object.
- **Replace Parameter with Query (p.324)** — When a parameter's value can be derived from other parameters, drop it. Trigger: callers computing the parameter's value just to pass it.
- **Replace Query with Parameter (p.327)** — The inverse: extract dependency on hidden state into an explicit parameter. Trigger: function depends on global or module-level state.
- **Remove Setting Method (p.331)** — When a field shouldn't change after construction, remove its setter. Trigger: setters used only in tests; immutable-after-construction values.
- **Replace Constructor with Factory Function (p.334)** — Wrap a constructor in a factory function for cases where the construction logic is complex or the type isn't known until runtime. Trigger: large constructors, conditional construction.

### 5.7 Dealing with inheritance (chapter 12)

- **Pull Up Method (p.350)** — When two subclasses have the same method, move it to the superclass. Trigger: Duplicated Code across siblings.
- **Pull Up Field (p.353)** — Same as Pull Up Method but for fields.
- **Push Down Method (p.359)** — Move a superclass method into the subclass that actually uses it. Trigger: Refused Bequest.
- **Replace Type Code with Subclasses (p.362)** — Replace a type field with actual subclasses. Trigger: Repeated Switches on a type field.
- **Remove Subclass (p.369)** — Inline a subclass back into its parent when the subclass isn't pulling its weight. Trigger: Lazy Element at the class level.
- **Extract Superclass (p.375)** — When two classes have similar features, extract a common superclass. Trigger: Alternative Classes with Different Interfaces; Duplicated Code.
- **Replace Subclass with Delegate (p.381)** — When inheritance isn't fitting (e.g., the subclass is more about wrapping than specializing), use composition instead. Trigger: subclass-specific behavior that overrides too many parent methods.
- **Replace Superclass with Delegate (p.399)** — The class-level inverse. Trigger: a class inheriting from a class that's mostly unrelated to its actual behavior.

This is not an exhaustive list. Fowler's catalog has more entries; the selection above covers the moves the chain is most likely to encounter on Elder code and ADW infrastructure. For any move not listed, consult Fowler chapter 5–12 directly — the page numbers above are 2nd ed for cross-referencing.

## 6. Building tests for refactoring (chapter 4)

Refactoring requires tests. Chapter 4 of Fowler is dedicated to building the test foundation. The summary:

- **Self-checking tests.** Tests must produce a binary pass/fail result without human inspection. Tests that print output for the operator to look at are not self-checking and don't support refactoring.
- **Run tests often.** Fowler says "I run the tests as often as I can — typically every few minutes when I am coding." For Elder, the chain runs `uv run pytest tests/<area> -v` between each refactoring move.
- **Test coverage matters less than test value.** It's better to have a few tests that catch real bugs than 100% line coverage of trivial paths.
- **Keep tests fast.** Slow tests don't get run between refactoring moves. The test suite should run in seconds, not minutes. For Elder, this is why integration tests and paper-TWS tests are in separate suites — the unit tests must stay fast.
- **Characterization tests for legacy code.** When refactoring code that lacks tests, write tests that capture the current behavior — whatever it is — first. Then refactor.

For Elder, the binding-tests catalog at `docs/elder-invariants.md` is the foundation. Each binding test is self-checking, fast, and captures a domain invariant. As Elder's surface grows, the catalog grows; refactoring is safe wherever the catalog covers.

The chain's compile-test-commit rhythm composes with the GOOS guide's red-green-refactor cycle as follows:

- The GOOS cycle creates new behavior by writing a failing test, making it pass with minimum code, and refactoring under green.
- The Fowler cycle preserves existing behavior while improving structure: tests should already pass; you change structure; tests should still pass.
- They overlap during the refactor step of GOOS: that step is a Fowler refactoring move (often Extract Function or Rename Variable) applied while tests stay green.

## 7. Refactoring and design — boundaries with other principles

Fowler's discipline composes with the existing standards stack. The boundaries are worth making explicit so the rules don't fight.

### 7.1 vs GOOS (TDD)

GOOS treats tests as the design oracle: write a failing test, listen to the test's pain, refactor the production code in response. Refactoring in GOOS is reactive — driven by test pain.

Fowler treats refactoring as a discipline applicable in many modes, only one of which is GOOS-style. Preparatory, comprehension, and litter-pickup refactoring all fire without test pain as the trigger. Bad smells are independent of test pain — code can smell while tests pass.

Together: GOOS gives you the "refactor under green" step in the red-green-refactor cycle. Fowler gives you the catalog of moves to apply during that step, plus the discipline of refactoring outside the cycle.

### 7.2 vs Modularity (Liskov, Parnas)

Modularity tells you what well-designed modules look like: level ownership, narrow public interfaces, opaque types at boundaries, dependency injection. It's prescriptive about structure.

Fowler tells you how to safely move from current structure to better structure. It's prescriptive about transformation.

Together: modularity gives you the destination; Fowler gives you the road. A module that violates modularity principles is a candidate for refactoring; the catalog gives you the moves (Extract Class, Move Function, Encapsulate Variable) to fix it.

### 7.3 vs DDD (Evans)

DDD tells you how to express domain concepts: entities, aggregates, value objects, ubiquitous language, bounded contexts. It's prescriptive about domain modeling.

Fowler's catalog includes moves that map directly onto DDD operations: Extract Class corresponds to identifying a new value object or entity; Replace Primitive with Object corresponds to lifting a primitive into a domain type; Replace Conditional with Polymorphism corresponds to identifying a domain concept that warrants its own type.

Together: DDD gives you the vocabulary for what to model; Fowler gives you the mechanics for how to introduce or evolve the model. Evans's "supple design" chapter even uses Fowler's vocabulary explicitly.

### 7.4 vs Decoupling (Phase E0 rule)

The decoupling rule (`.claude/rules/decoupling.md`) tells the chain how to recognize and avoid project-coupling in `.claude/` infrastructure. Its six patterns and refactor mappings are themselves a small Fowler-style catalog: each pattern has a recognize-and-fix recipe.

Together: decoupling is a domain-specific application of Fowler's "Move Field across boundary" + "Replace Project-Specific Reference with Parameter" pattern, narrowed to the chain-content layer. The rule's mechanics align with Fowler's small-steps + tests-passing discipline.

## 8. Project mapping summary

| Fowler concept | Status in Elder | Notes |
| -------------- | --------------- | ----- |
| Definition of refactoring (behavior preservation) | Standing rule once this guide ships | Today the chain does refactoring without naming it; the rule formalizes |
| Two Hats | Standing rule | Direct response to PR #117 scope-creep evidence |
| Rule of Three | Implicit in current ADW design | E0 decoupling rule waited for second instance before authoring |
| Preparatory refactoring | Implicit in build-plan ordering | Item #19 (deployable unit) is preparatory for items #1–#13 |
| Comprehension refactoring | Standing rule once this guide ships | The chain can name "I'm refactoring to understand X" rather than just doing it |
| Litter-pickup refactoring | Standing rule | Bounded by Two Hats; small enough to ship in the same commit, but distinct from feature work |
| Solid-tests precondition | Implemented | Binding-tests catalog covers the risk and indicator domains |
| Self-checking tests | Implemented | Pytest with explicit assertions; no operator-inspection patterns |
| Characterization tests for legacy | Standing rule | Apply when refactoring deferred infrastructure (e.g., `core/ib_*.py`) |
| Extract Function (the most common move) | Standing rule | Used implicitly today; should be named explicitly in PR descriptions |
| Move Function / Move Field | Standing rule | Apply during build item #1 (pipeline refactor) |
| Replace Conditional with Polymorphism | Standing rule | Apply during item #11 (capability gates) and #12 (execution policy) |
| Encapsulate Record | Standing rule | The Decimal migration in item #10 is partly an Encapsulate-Record exercise |
| Bad smells catalog | Standing rule | Reviewer agent should flag smells by name, not by ad-hoc complaint |
| Compile-test-commit rhythm | Standing rule | Add to chain's BUILD phase output: name the move, run tests, commit |

## 9. Antipatterns

Each entry is a specific failure mode this guide rejects. They are the mirror of §§3–7.

- **Refactoring while changing behavior.** "Just a small tweak" is the most common Two-Hats violation. If the diff includes both structural and behavioral changes, it's not refactoring.
- **Skipping tests.** Refactoring without solid self-checking tests is hope, not discipline. If tests don't exist, write characterization tests first.
- **Skipping the commit step.** Stacking five refactoring moves in a single commit means a bug in any of them requires reverting all five and re-doing the work. Per-move commits are the safety net.
- **Premature abstraction.** Refactoring on the first occurrence of duplication, before the pattern is real. Wait for the third occurrence.
- **Speculative generality.** Adding "extension points" no one needs. Refactoring is for current pain, not anticipated future flexibility.
- **Renaming under the feature hat.** Rename Variable mid-feature change conflates structural and behavioral diffs.
- **Untested change to legacy code.** "It's just a rename" — except in legacy code without tests, even renames can break things (e.g., reflection-based access). Characterization tests first.
- **Mocking the system under test to make a refactor feel safe.** Mocks don't validate behavior preservation; only real tests do.
- **Refactoring on a Friday afternoon before a release.** The discipline is sound; the timing breaks the calibration. Refactoring during high-stress periods is the recipe for the Friday-afternoon merge crisis.
- **Refactoring without recording the move name.** A PR titled "improvements to X" with no named refactorings is unreviewable. Each move gets a name; the PR description lists them.
- **Stopping mid-sequence.** Starting an Extract Function and leaving it half-done leaves the codebase in a worse state than before. Either complete the sequence or revert.
- **Mixing two refactorings in one commit.** Even when both are pure refactoring (no feature work), separating them by commit makes review and revert easier.

## 10. Self-audit checklist

Run this against any refactoring change before declaring it done. Each item is binary; partial credit does not exist.

1. **Behavior is preserved.** Tests that passed before pass after. No new failing tests; no test-modification to make broken behavior pass.
2. **The change is pure refactoring.** No feature work mixed in. If the diff includes both, split into two PRs or two commits.
3. **The moves are named.** PR description lists the refactorings applied (Extract Function, Rename Variable, etc.) by name. Each commit message names its move.
4. **Tests existed before refactoring started.** Or characterization tests were written first. Refactoring without tests is hope.
5. **Tests are self-checking.** Binary pass/fail; no operator inspection required.
6. **Each step is small.** No single commit applies more than ~3 refactoring moves. If one commit does more, it should have been split.
7. **Tests pass at each commit.** Not just at the end. The history should be a sequence of green commits.
8. **The smell that triggered the work is named.** "I'm refactoring this Long Function" or "These three sites are Duplicated Code." If you can't name the smell, you're not refactoring; you're rewriting.
9. **The catalog name matches the actual move.** "Extract Function" should be an actual extraction, not a rewrite. Each named move follows Fowler's mechanics.
10. **No speculative generality.** Every abstraction added has a current consumer. No "this might be useful later" code.
11. **The codebase is at least as understandable as before.** A cleanup that makes the code less clear is not a refactoring; it's a rewrite under a refactoring hat.
12. **Comments tell the story or are removed.** If the refactoring made comments redundant (because functions now have clear names), delete them. If a comment is still load-bearing, the function name probably needs more work.

A change failing any item is not finished, no matter how green its tests are.

## 11. What this guide does not cover

- **JavaScript syntax and tooling.** Fowler's 2nd edition is in JavaScript; the patterns translate to Python, but specific tooling (Babel, IDE refactoring shortcuts in WebStorm) does not. Where Python has equivalents (`mypy`, `ruff`, IDE refactoring in VSCode/PyCharm), they are mentioned in the rule file; otherwise the principle is what carries.
- **Refactoring databases.** Fowler has a separate book with Pramod Sadalage on database refactoring. Elder's PostgreSQL schema work (build items #14, #2) will draw on it directly when those items land. The current guide focuses on application-code refactoring.
- **Refactoring across deployment boundaries.** Refactoring within a deployable unit is the discipline this guide covers. Refactoring across services or across deployment boundaries (the so-called Strangler Fig pattern at architecture scale) is its own domain. Elder is currently a single deployable unit; this guide is enough.
- **Performance refactoring.** Fowler explicitly excludes optimization from his definition of refactoring. Performance work is a separate discipline. If a refactoring incidentally makes code faster, fine; if a change makes code faster at the cost of clarity, it's optimization under the feature hat.
- **Large-scale architectural refactoring.** Fowler covers individual code-shape moves. Breaking up a monolith or restructuring across bounded contexts is at a scale that mixes refactoring with strategic design — Evans's chapter 14 ("Maintaining Model Integrity") is the better starting point.

## Sources

- Fowler, M. (2018). *Refactoring: Improving the Design of Existing Code*, 2nd edition. Addison-Wesley. The spine of this guide; all chapter and page citations refer to this edition. The 1st edition (1999, in Java) covers the same ground; chapter mapping is similar but page numbers differ.
- Beck, K. *Smalltalk Best Practice Patterns*. Cited by Fowler for naming conventions (e.g., the indefinite-article parameter naming convention adopted in chapter 1).
- Beck, K. *Test-Driven Development by Example*. Cited by Fowler for the red-green-refactor cycle and the discipline of small steps.
- Feathers, M. *Working Effectively with Legacy Code*. The canonical treatment of characterization tests and refactoring code without test coverage. Complementary to Fowler chapter 4.
- Cunningham, W. *Wiki and the Wiki Pattern*. Cited indirectly by Fowler for "this understanding is in my head — a notoriously volatile form of storage" — the comprehension-refactoring justification.
- `docs/modularity-guide.md` — Liskov-grounded modularity discipline; pre-requisite to many catalog entries (especially Move Function, Extract Class, Hide Delegate). The destination this guide's road leads to.
- `docs/ddd-guide.md` — Evans-grounded domain modeling; complementary to Fowler at the design-vocabulary layer (entities/values/services map naturally to Replace Primitive with Object and Extract Class).
- `docs/goos-guide.md` — Freeman/Pryce-grounded TDD; covers refactoring as test-pain response. This guide covers refactoring as a broader discipline including modes outside the test-pain cycle.
- `.claude/rules/decoupling.md` — a small Fowler-style catalog applied to the chain-content layer; demonstrates the same "name the pattern, name the fix, run the tests" structure scoped to project-portability.
- CLAUDE.md "Design philosophy" — the operating rules that codify several patterns from this guide (Tell, Don't Ask is a Move-Function-shaped insight; "default to writing no comments" composes with the Comments-as-deodorant smell).
