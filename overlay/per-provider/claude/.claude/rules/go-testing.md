---
paths:
  - "**/*_test.go"
---

# Go testing

How to write Go tests: table-driven structure, the standard assertion idioms, test doubles, fuzzing, and property-based testing. Sources: the `testing` package docs, the Go blog (subtests, fuzzing), the Go Test Comments wiki (go.dev/wiki/TestComments), the Google style guide, `google/go-cmp`, and `pgregory.net/rapid`. The TDD cadence and design discipline are in `craft-tdd.md`; this rule is the Go mechanics.

> See `craft-tdd.md` for red-green-refactor and "listen to the tests", `go-types.md` for the small consumer interfaces that make fakes trivial, and `craft-refactoring.md` for self-testing code as the refactoring prerequisite.

## Structure and naming

- **Use table-driven tests when cases share logic; use separate functions when cases need different logic.** Structure cases as a slice of structs where only the name, input, and want vary, and specify field names in each literal (`{name: ..., want: ...}`), never positional.
- **Wrap each case in `t.Run(tc.name, func(t *testing.T){…})`** so one failure doesn't hide the rest and each case is independently runnable; give subtests human-readable names.
- **Keep logic out of tests** — no branching or loops that compute the expected value. One behavior per test, arrange-act-assert in order, the act step a single call to the unit under test.

## Failure messages and assertions

- **Write failure messages got-before-want and name the call:** `t.Errorf("Split(%q) = %v, want %v", in, got, want)` — not a bare `got/want`.
- **Default to the standard library: `if got != want { t.Errorf(...) }` for scalars, and `cmp.Diff` (`github.com/google/go-cmp/cmp`) for structs, slices, and maps.** Never use `reflect.DeepEqual` for test equality — `cmp` is Go-team-maintained and produces stable diffs. Format the message `"Fn() mismatch (-want +got):\n%s"` with `cmp.Diff(want, got)`, and use `cmpopts` rather than pre-massaging values.
- **Disagreement, named:** the Google guide and Go Test Comments advise *against* assertion libraries (the ideal failure point is the `Test` function itself); `stretchr/testify` (`assert` continues, `require` aborts) is widely used for concise assertions. Default: **stdlib + `cmp`**; if a package adopts testify, use it consistently within that package — never mix the two in one package.

## Helpers, lifecycle, and parallelism

- **Call `t.Helper()` first in any helper that takes `*testing.T` and can fail**, so failures point at the caller's line.
- **Register teardown with `t.Cleanup(...)` rather than `defer` in the test body** — cleanups fire correctly even when subtests call `t.Parallel()`, where a parent `defer` can run before paused subtests finish. Use `t.TempDir()` for scratch dirs.
- **Use `t.Error`/`t.Errorf` to report and keep going; reserve `t.Fatal`/`t.Fatalf` for setup failures that make continuing pointless.** In a table loop without `t.Run`, use `t.Error` then `continue`, never `t.Fatal`. Never call `t.Fatal` from a goroutine other than the test's — use `t.Error` and return.
- **Add `t.Parallel()` to independent subtests** that share no mutable state.

## Test doubles

- **Prefer small interfaces plus hand-written fakes over generated mocks** — fakes give compile-time type safety, readable call sites, and no regeneration step, and Go's structural typing makes them cheap. Reserve `go.uber.org/mock` for large interfaces you own with strict call-ordering needs.
- **Don't mock what you don't own.** Wrap the model SDK, external CLIs (for example a linter or `git`), and the filesystem behind your own narrow interface and fake that; exercise the real thing only in integration tests. (`craft-tdd.md`.)

## Golden files, fuzzing, coverage

- **Put fixtures and expected outputs under `testdata/`** (the Go tool ignores it). Make golden files regenerable with a `-update` flag, then **review the git diff of the golden file before committing** — never blindly accept regenerated output.
- **Add a `FuzzXxx(f *testing.F)` for code that parses or decodes wide-ranging or untrusted input** (parsers, decoders, the schema/JSON boundary). Seed with `f.Add(...)`, keep the target fast and deterministic, and commit the regression corpus under `testdata/fuzz/`. Fuzzing complements table tests; it doesn't replace them.
- **Measure coverage with `go test -coverprofile=...`, treat it as a signal, not a target** — never write assertion-free tests to raise the number.
- **Gate slow/external tests behind `testing.Short()` (`t.Skip` under `-short`) or a `//go:build integration` tag**, keeping the default `go test ./...` fast and hermetic.

## Property-based testing (use `pgregory.net/rapid`)

Property tests assert invariants over a large generated input space and **shrink** any failure to a minimal counterexample — more robust than example tests alone. The chosen library is **`pgregory.net/rapid`** (generics, fully automatic shrinking, native state-machine testing, seed/fail-file reproduction; fallback `leanovate/gopter`; avoid the frozen, shrink-less `testing/quick`).

- **Write a property as `rapid.Check(t, func(t *rapid.T){…})`, drawing every variable input through `gen.Draw(t, "label")`.** Favor strong properties — round-trip equality (`decode(encode(x)) == x`), idempotence (`f(f(x)) == f(x)`), a conservation law, or comparison against a slow-but-obvious oracle — not trivially-true ones.
- **Nest a property beside table cases with `rapid.MakeCheck`** under `t.Run` — table cases pin specific regressions and documented edges; properties cover the space you didn't enumerate.
- **Use rapid's state-machine testing (`t.Repeat`) for a stateful component's lifecycle** — generate random valid action sequences against the real component, check invariants after each step (the `""` action), guard preconditions with `t.Skip`; rapid shrinks a failing *sequence* to the shortest reproducer.
- **Reproduce a failure deterministically with `-rapid.seed=...` and commit the persisted fail file as a regression artifact.**
- **Pitfalls:** keep properties pure functions of their drawn inputs (no wall-clock, real IO, or `rand` — inject doubles); constrain generators at generation time (`IntRange`, `Custom`) rather than discarding inside the body; never reseed inside the test.

## Anti-weakening (what the differential gate forbids)

Treat any of these versus the merge-base as test-suite weakening — do not introduce them:

- A test function deleted with no equivalent replacement, or a previously-running test newly gated behind `t.Skip`/`t.Skipf`/`t.SkipNow`.
- A net drop in assertion sites for a package (removed `t.Error`/`t.Fatal`/`cmp.Diff`/`require.*`/`assert.*`), or table cases removed.
- A `want` loosened to a wildcard or an always-true comparison; an assertion turned into a no-op; an error swallowed with `_ = err` where it was previously asserted.
- A `t.Fatal`/`t.Error` downgraded to `t.Log` (failure becomes invisible).
- A deleted fuzz seed under `testdata/fuzz/` (re-admits a known-bad input).
