---
paths:
  - "**/*.go"
---

# Go style and idioms

The base layer of Go discipline: naming, control flow, and the value/pointer/slice/map idioms. Sources: Effective Go, Go Code Review Comments (go.dev/wiki/CodeReviewComments), the Google Go Style Guide, the Uber Go Style Guide, Dave Cheney's *Practical Go*, and *100 Go Mistakes*. Where the major guides disagree, the disagreement is named with a stated default.

> See `craft-complexity.md` for why precise, consistent names matter, `craft-documentation.md` for the language-neutral comment discipline these doc-comment mechanics implement, `go-errors.md` and `go-concurrency.md` for the error and goroutine idioms referenced here, and `go-modules.md` for package naming and layout.

## Formatting

- **Run `gofmt`/`go fmt` on every file; never hand-format** — the machine owns indentation and alignment, so the style debate doesn't exist. Put the opening brace on the same line (semicolon insertion breaks a brace on the next line).

## Naming

- **Use `MixedCaps`/`mixedCaps`, never underscores, for multiword names.**
- **Give packages short, lowercase, single-word names; never `util`, `common`, `helper`, `base`, `misc`, `types`, or `api`** — grab-bag names signal no design. Name a package for what it *provides*.
- **Omit the package name from its exported identifiers** — `widget.New`, not `widget.NewWidget`; `db.Load`, not `db.LoadFromDatabase`. The qualifier already supplies context; stutter is noise.
- **Scale local-variable name length with scope** — `c` over `lineCount`, `i` for a short loop; longer names the farther a name is used from its declaration. Don't encode the type in the name (`users`, not `usersMap`).
- **Keep initialism case uniform** — `URL`, `ID`, `HTTP`, `appID`, `ServeHTTP`; never `Url`, `Id`, `appId`.
- **Name getters for the noun, not `GetX`** (`Owner()`, not `GetOwner()`); name a single-method interface with the `-er` suffix (`Reader`, `Writer`) and a string method `String()`.
- **Use a short, consistent receiver name that abbreviates the type; never `me`, `this`, or `self`.**

## Control flow

- **Handle the error first and return early; never wrap the happy path in `else`.** Line of sight: the success path stays unindented and reads top to bottom. Collapse `if/else` assignment to a default plus a conditional override.
- **Avoid naked returns outside a few lines; name results only when several share a type or the name documents required caller action** — not merely to enable a naked return.
- **Avoid variable shadowing in inner blocks** — a shadowed name silently rebinds and you operate on the wrong value.

## Values, pointers, slices, maps

- **Design types so the zero value is usable** (`var b bytes.Buffer` just works) — model after `sync.Mutex`, `bytes.Buffer`.
- **Don't pass a pointer to "save bytes"** — pass the value if the function only reads it. Use a pointer receiver when the method mutates the receiver or the struct holds a `sync.Mutex` or other non-copyable field, and keep a type's receivers all-pointer or all-value.
- **Treat `nil` and empty slices as equivalent: return `nil`, not `[]T{}`, and test emptiness with `len(s) == 0`.**
- **Never assume map iteration order; sort the keys when order matters** (Go randomizes ranging deliberately).
- **Copy slices and maps you store from a caller, and copy those you return from internal state** — otherwise the caller aliases your internals.
- **Use the comma-ok form for map lookups and type assertions** — `v, ok := m[k]`, `s, ok := i.(string)` — to distinguish absent from zero and to avoid an assertion panic.

## Composition and embedding

- **Prefer composition with named fields over embedding in exported structs.** *Disagreement:* Effective Go encourages struct embedding broadly; Uber and *100 Go Mistakes* restrict it in *public* structs because it leaks the inner type's surface into your API. Default: embed freely in unexported types, expose behavior through named fields in exported ones.
- **Never return a typed nil pointer through an interface return type** — a `(*T)(nil)` boxed in an interface is non-nil to the caller; return a literal `nil`. (`go-errors.md`.)

## Comments and `defer`

- **Give every exported top-level name a doc comment that starts with the identifier and is a full declarative sentence** (`// Encode writes the JSON encoding of req to w.`), and give every package one package comment introducing what it provides. Document significant sentinel/typed errors a package returns. (`craft-documentation.md`.)
- **Document what the type system leaves implicit: a non-obvious zero value, any goroutine-safety beyond the single-goroutine default, and a `// Deprecated:` line on an obsolete exported symbol.** Add a runnable `Example` function for a non-trivial public API — it documents usage and is compiler-checked.
- **Don't `defer` inside a loop expecting per-iteration cleanup** — extract the loop body into a function so the defer fires each iteration. `defer` evaluates its arguments immediately; capture later values with a closure. Handle (or explicitly discard with a comment) errors from a deferred `Close`.

## Per-iteration loop variables

- **Each loop iteration creates fresh variables (behavior available since Go 1.2x), so capturing a loop variable in a closure or goroutine is safe.** On a recent Go toolchain, do *not* insert the legacy `v := v` shadow copy — it is now redundant. On older toolchains that predate this change, keep the `v := v` copy.
