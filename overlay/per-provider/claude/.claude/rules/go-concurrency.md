---
paths:
  - "**/*.go"
---

# Go concurrency

Context propagation, goroutine lifecycle, and channel ownership for a program that shells out to external commands (`git`, a linter, and similar) and calls an LLM API. Sources: the Go blog on context (go.dev/blog/context-and-structs), pipelines (go.dev/blog/pipelines), the `errgroup` package, the race-detector blog, and the Uber/Google style guides.

> See `craft-tdd.md` for separating functionality from concurrency policy so logic stays unit-testable, `go-llm.md` for the context timeout on model calls, and `go-style.md` for the goroutine-at-the-API-surface rules.

## Context

- **Take `ctx context.Context` as the first parameter of any function that does I/O, blocks, or spawns goroutines** — it makes cancellation and deadlines propagate transparently across call boundaries.
- **Never store a `context.Context` in a struct field; pass it per call.** Storing it intermingles lifetimes, hides what it governs, and removes per-call deadline control. (The only sanctioned exception is retrofitting a legacy API, and even then prefer a `Context`-suffixed function.)
- **`defer cancel()` for every `context.WithCancel`/`WithTimeout`/`WithDeadline`** — the cancel func releases the context tree's resources; skipping it leaks them.
- **Put a timeout on every external call — subprocesses, network calls, and the model API** — via `context.WithTimeout`. An unbounded shelled-out or network call can hang the process; the model call especially needs a bounded budget. Use plain `WithCancel` only for sibling cancellation, not wall-clock bounds.
- **Pass `ctx` into `exec.CommandContext`, never bare `exec.Command`**, so cancelling the context kills the child process instead of orphaning it.
- **Check `ctx.Err()` before expensive work and `select` on `ctx.Done()` inside any loop or blocking send/receive** — a goroutine blocked on a channel with no `ctx.Done()` arm never exits.

## Goroutine lifecycle

- **Give every goroutine a known owner and a guaranteed exit path before you write the `go` statement.** If you start concurrent work, you must know when and how it ends and where its errors go.
- **Use `errgroup.WithContext` instead of bare `go` plus `sync.WaitGroup` whenever goroutines can fail** — it captures the first error, cancels the shared context on that error, and waits for all goroutines.
- **Bound fan-out with `g.SetLimit(n)`** rather than one goroutine per work item — cap concurrent subprocesses and API calls so the program can't exhaust file handles or rate limits. Never mutate the `Group` (including `SetLimit`) while goroutines are active, and never reuse a `Group` across independent tasks.
- **Use `sync.WaitGroup` correctly: `Add` before the `go` statement, `defer wg.Done()` inside, never `Add` after `Wait` has started.**

```go
// Bounded, cancellable fan-out over external commands.
func runAll(ctx context.Context, cmds [][]string) error {
    g, ctx := errgroup.WithContext(ctx)
    g.SetLimit(4) // cap concurrent subprocesses
    for _, c := range cmds {
        g.Go(func() error {
            cctx, cancel := context.WithTimeout(ctx, 30*time.Second)
            defer cancel()
            return exec.CommandContext(cctx, c[0], c[1:]...).Run()
        })
    }
    return g.Wait() // first error; ctx already cancelled the rest
}
```

## Channels and shared state

- **Senders close channels; receivers never do; a channel has exactly one closing owner** — sending on a closed channel panics, so closing is the sole responsibility of the side that knows all sends are finished.
- **Express channel direction in signatures** — `chan<- T` for producers, `<-chan T` for consumers — so the compiler enforces who sends and who receives.
- **In a pipeline, a stage closes its outbound channel when its sends are done (`defer close(out)`) and keeps receiving until its inbound channel is closed or it is signalled to stop** — this prevents blocked-sender leaks. When several goroutines send on one channel, coordinate the close with a separate `wg.Wait(); close(out)` goroutine.
- **Prefer one synchronization mechanism per piece of shared state** — a channel that passes ownership, or a mutex that guards it, not both. Reach for `sync.Mutex` to guard a small shared field; reach for a channel to hand off ownership of a value.

## The race detector

- **Run `go test -race ./...` in CI on the concurrent-path tests.** It only finds races actually exercised at runtime, so the suite must drive the concurrent code (integration and load tests are best). Accept the ~10x cost as test-only overhead, never a production default.
