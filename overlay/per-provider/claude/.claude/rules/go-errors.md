---
paths:
  - "**/*.go"
---

# Go errors as values

Errors are ordinary values in Go; handling them well is the heart of a program's reliability. Sources: Go Code Review Comments, the Go blog "Working with Errors in Go 1.13" (go.dev/blog/go1.13-errors), the Google and Uber style guides, Dave Cheney, and *100 Go Mistakes*. The design principle behind several of these rules — define errors out of existence — comes from `craft-complexity.md`.

> See `craft-complexity.md` for defining errors away, `go-style.md` for the early-return control flow, and `go-llm.md` for feeding validation errors back to the model.

## Never silently drop an error

- **Never discard an error with `_` to silence it** — handle it, return it, or (only in a truly exceptional, unrecoverable case) panic. A dropped error is a hidden failure.
- **When you do intentionally ignore one, use `_` with a comment** explaining why (`n, _ := buf.Write(p) // bytes.Buffer.Write never returns an error`).
- **Handle each error exactly once.** Don't both log it and return it — double handling produces duplicate, confusing logs. Log at the top where the error is handled, or return it with context, not both.

## Error strings and context

- **Write error strings lowercase with no trailing punctuation** — they get concatenated into larger messages. Never leak secrets (tokens, keys, full paths to credentials) into an error string.
- **Add only non-redundant context when wrapping** — don't repeat a filename the underlying `os` error already carries.

## Wrapping, `Is`, and `As`

- **Wrap with `fmt.Errorf("doing X: %w", err)` only when callers should inspect the underlying error** via `errors.Is`/`errors.As`. `%w` makes that error part of your package's public API forever.
- **Use `%v` (not `%w`) at system boundaries or when the wrapped error is an implementation detail** you want to stay free to change. Place `%w` at the end of the format string, except put a wrapped sentinel first to lead with the category.
- **Compare sentinels with `errors.Is(err, ErrX)` and extract typed errors with `errors.As(err, &target)` — never `==` or a bare type assertion.** These traverse the whole wrap chain; `==` does not.

## Choosing the error mechanism

- **Match the minimum machinery to the need:** `errors.New` for a static message with no matching; `fmt.Errorf` for a dynamic message with no matching; a package-level `var ErrFoo = errors.New(...)` for a static, matchable sentinel; a custom `type FooError struct{…}` for a dynamic, matchable error.
- **Name sentinels `ErrFoo` and custom error types `FooError`** — the mechanically recognizable convention.

## Panic discipline

- **Don't use `panic` for ordinary error flow.** Only `main`/entrypoints may `log.Fatal`/`os.Exit`, and a panic must never cross a package boundary — recover at the goroutine/boundary if a dependency can panic. Panics are for irrecoverable programmer bugs, not control flow.

## Define errors out of existence

- **Prefer redefining an operation so the error case becomes the normal case** over adding another error return — an `ensureAbsent` that succeeds when the thing is already gone needs no error. Where you can't define the error away, handle it as low as possible (mask it) or aggregate many handlers into one high in the call path. Fewer error sites means simpler, more reliable code. (`craft-complexity.md`.)
