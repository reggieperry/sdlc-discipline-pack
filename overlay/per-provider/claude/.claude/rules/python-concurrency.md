---
paths:
  - "**/*.py"
---

# Python concurrency and idempotency invariants

Two bodies of discipline meet in Python concurrency: the **systems invariants** that govern any code touching durable state under concurrency, retries, or distribution (write skew, fencing tokens, compare-and-set, idempotency keys, monotonic clocks, safety vs liveness), and the **`asyncio` idioms** Python gives you to express concurrent I/O safely. The first set is language-agnostic in spirit — the craft holds in any language — but it lands here with Python mechanics; the second set is pure Python. Both make docstring claims like "idempotent," "thread-safe," or "exactly-once" carry weight instead of decoration.

Sources: DDIA (Kleppmann) on the invariants; the asyncio docs (tasks, the event loop), Python 3.11+ `TaskGroup`/`timeout`, and the GIL on the idioms.

> See `craft-domain-modeling.md` for the aggregate-as-consistency-boundary framing (long-form: `sdlc-discipline/guides/ddd-guide.md`), `python-security.md` for the OWASP-shaped boundary defenses, `craft-tdd.md` for separating functionality from concurrency policy so logic stays unit-testable (long-form: `sdlc-discipline/guides/goos-guide.md`), `python-llm.md` for timeouts on model calls, and `python-style.md` for the surrounding idioms.

---

## Part 1 — Invariants for durable state under concurrency

### Name the idempotency invariant

"Idempotent" is shorthand for three distinct invariants. Pick the one that matches the retry semantic; don't conflate.

- **Don't crash on duplicate** — retry of the same input must not error, but the existing state is correct as-is. Set-if-absent shapes; insert-or-ignore.
- **Last writer wins on identity** — the new attempt's data should supersede the old. Upsert-with-replace shapes.
- **Reset to pristine on retry** — the caller-facing semantic is "rerun the operation from scratch." Upsert that explicitly clears stale failure columns; not "leave the old failure row visible until the new write overwrites it."

Wrong choice is observable. Ask: if a row exists with stale failure state and we re-run, what does a status view show during the retry? If "stale failure," the answer is wrong.

The same three patterns apply outside upserts: file writes (skip vs replace vs truncate-then-write), HTTP POSTs (skip vs replace vs reset), message handlers (ack-and-skip vs replay vs reset). Name the invariant in the docstring next to the implementation that supports it.

### Write skew is its own category

When a function reads a set of rows to decide whether to act, then writes a different row whose presence changes the set, that's write skew. Single-row defenses (row locks, conditional inserts on the written row) do not cover it.

Name the invariant in plain English ("no two workers claim the same job," "at least one approver per record"). Pick one of three defenses:

- **Unique or exclusion constraint** covering the invariant — best when the invariant is a property of the table.
- **Lock the anchor row** whose presence guards the whole set — best when there's a natural parent (an order guards its line items; an account guards its open positions).
- **Serializable isolation on the code path** — best when neither constraint nor anchor row exists. Heavier; reach for case-by-case, not as a default.

A row-lock on an empty result set locks nothing — it doesn't prevent inserts that would have changed the set.

### Snapshot scope is one transaction wide

If a function's correctness depends on "the state I read at line 10 still describes the state at line 30," wrap it in repeatable-read isolation explicitly. Default read-committed gives each statement a fresh snapshot, so multi-statement functions read a moving target. Repeatable-read pins one snapshot for the whole transaction.

When the default isolation is fine for a function, say so in the docstring — silent assumption of consistency is what produces the "I read it and then it was different" surprises.

### Compare-and-set asserts the row count

Every state-machine update includes the prior-state check in the predicate, returns the affected rowcount, and the caller treats `rowcount == 0` as "someone beat us" — not as success.

```text
UPDATE x SET state = new WHERE id = :id AND state = :seen
-- if rowcount == 0: raise ConcurrentTransitionError; do not retry blindly
```

The unit test drives two concurrent updates against the same row and asserts exactly one wins. Reachable today via an integration test against the real store; an in-memory fake can hide the race.

### Retries with external side effects need an idempotency key

Every external side effect that costs money or has at-least-once semantics (a paid API, an email, a webhook, an order placement, a payment) carries an idempotency key derived deterministically from the caller's identity for that attempt. Reuse the same key across every retry layer for the same attempt; bump the key only when bumping the attempt number.

Five failure modes for naive retries: request succeeded but ack lost; transaction aborted after the side effect fired; partial in-flight retry from a network blip; process restart resumes mid-flight; reconciliation reclaims and re-fires. All five bill the external service.

Derive the key from a tuple that uniquely identifies the attempt — e.g. `(run_id, item_key, attempt_number)`. Persist it next to the work record. The runtime retry layer reuses the same key. The reconciliation layer produces a new key only when it bumps the attempt counter.

### Monotonic clock for elapsed time

Within a process, elapsed time uses a monotonic source — `time.monotonic()`, not a wall clock. Cross-process ordering uses an authoritative source: the database's server-side timestamp, a monotonic sequence, or an event ID — not `datetime.now()` from two different machines.

Wall clocks drag backward under NTP correction; in VMs they can jump forward by tens of milliseconds. Last-writer-wins on `datetime.now()` silently drops writes when two clocks disagree.

For heartbeat staleness, compare two server-written timestamps from the same database (single clock source). Reject any code that subtracts `datetime.now()` calls from different processes.

### Fencing tokens — the resource enforces the lock

A lease/lock holder cannot trust its own belief. A process can pause arbitrarily (GC, VM steal, host suspend) between "lease is valid" and the protected write. The lease expires; another holder takes over; the paused process resumes and writes anyway.

The defense: a monotonically increasing fencing token, granted with the lease, included in every write from the holder. The resource (not the holder) rejects writes whose token is below the highest seen.

```text
UPDATE resource SET ... WHERE id = :id AND token = :token AND state IN ('claimed','running')
-- if rowcount == 0: stale write rejected — superseded by a newer token
```

The schema change is real — capture as a follow-up when the safety property warrants production hardening, not a same-PR fix in pure-functional work.

### Safety vs liveness — different invariants, different tests

In any docstring that touches concurrency, write two labeled bullets:

- **Safety:** nothing bad ever happens (uniqueness, monotonicity, no double-commit). Must hold under all conditions. The test drives the function past the boundary (crash mid-write, double-claim, token rollback) and asserts the invariant.
- **Liveness:** something good eventually happens (a stranded record gets re-kicked, a cooldown elapses, the queue drains). May be caveated ("only if the reconciliation loop is running," "only if the network eventually recovers"). The test asserts eventual progress under the named assumption.

Safety violations cannot be undone. Liveness violations can be diagnosed and recovered. Conflating them is how "idempotent" claims slip past review.

Claims like "no-op on retry," "degraded mode," "exactly-once" — all safety. Claims like "eventually consistent," "reconciliation reclaims stranded runs" — liveness.

### Optimistic vs pessimistic concurrency — pick by contention

Each persistence function picks one defense and names it. Default to optimistic at low contention; reach for pessimistic only where waiting is correct and contention is real.

- **Optimistic** — add a `version_number` to the aggregate root. Update predicates the version (`WHERE id = :id AND version = :seen_version`); rowcount-zero raises `StaleAggregateError`; caller retries from a fresh read. Cheaper at low contention, harder to deadlock.
- **Pessimistic** — `SELECT FOR UPDATE` on the row or its parent. Correct when serial access is the desired semantic (a worker claiming a job).

Optimistic and pessimistic are not redundant when both surfaces exist. A worker's lease protects *who gets to run*; the version number protects *what the data looks like* against re-attempts and operator overrides.

### Defense-selection cheat sheet

| Invariant shape | Defense |
| --------------- | ------- |
| At most one writer on a single row | Row lock on that row |
| At most one row matching this predicate | Unique or partial-unique constraint |
| Multi-row property the writes break (write skew) | Exclusion constraint, anchor-row lock, or serializable |
| Transition iff prior state = X | CAS with predicate + rowcount check |
| External call shouldn't double-bill on retry | Idempotency key |
| Stale lease-holder shouldn't corrupt resource | Fencing token |
| Two reads in same handler must agree | Repeatable-read isolation |
| Time-based decision across processes | Server-side timestamp or monotonic sequence |

### Algorithmic complexity is part of the spec

When a loop is worst-case O(n²) and only an input bound keeps it safe, name both — the worst case and the cap that makes it acceptable. Future engineers may raise the cap and need to know.

```text
# O(n²) worst case (n = number of entities). Bounded acceptable because
# MAX_ENTITIES caps the walk at ~40K lookups. If MAX_ENTITIES rises,
# memoize via an ancestor_cache lookup table.
```

---

## Part 2 — `asyncio` idioms

Structured concurrency with `asyncio` for code that does I/O and calls external services.

### Structured concurrency

- **Use `asyncio.TaskGroup` (3.11+) for concurrent subtasks, not bare `gather`** — a TaskGroup cancels the remaining tasks on the first failure and raises an `ExceptionGroup`, where `gather` lets siblings run on. Handle failures with `except*` against the `ExceptionGroup`.
- **Reserve `asyncio.gather(..., return_exceptions=True)` for the one niche where you deliberately want per-task results-or-errors with no cancellation.**
- **Use `asyncio.run(main())` as the single program entrypoint** — it creates and tears down the loop correctly.

### Deadlines and cancellation

- **Set deadlines with `async with asyncio.timeout(delay):` (3.11+) over `wait_for`** — it is nestable, reschedulable, and raises `TimeoutError`. Put a timeout on every external call (the model API, a subprocess, the network).
- **Catch `asyncio.CancelledError` only to clean up, then re-raise it — never swallow it.** Suppressing cancellation breaks `TaskGroup` and `timeout`; call `task.uncancel()` only in the rare case you truly absorb a cancellation.
- **Keep a reference to every `asyncio.create_task()` result** — a task with no live reference can be garbage-collected mid-flight. Reserve `asyncio.shield()` for the rare operation that must survive caller cancellation.

### Don't block the loop

- **Never call a blocking function inside a coroutine** — use `await asyncio.sleep()` not `time.sleep()`, and async I/O libraries, not blocking sockets. One blocking call freezes every task on the loop.
- **Offload unavoidable blocking calls with `await asyncio.to_thread(fn, *args)`** (IO-bound only — it propagates `contextvars`). The GIL is the reason threads help I/O but not CPU work; push CPU-bound work to a `ProcessPoolExecutor`.

---

## Antipatterns

- Idempotent claim without naming which invariant (don't-crash vs replace vs reset).
- Update without prior-state predicate and rowcount check.
- Retry layer that bumps the idempotency key — the *whole point* is reuse across the same attempt.
- `datetime.now()` differences across processes for ordering.
- Lock on what you read, write somewhere else, no anchor — classic write skew.
- "Eventually" claim with no test driving the function past the failure boundary.
- Lease check, then unguarded write — needs a fencing token.
- `time.sleep()` or a blocking socket inside a coroutine — freezes the whole loop.
- Bare `gather` where first-failure cancellation was wanted — siblings run on.
- Swallowing `CancelledError` — breaks `TaskGroup` and `timeout`.
- An external call with no `asyncio.timeout` — one hung peer hangs the operation.

## Self-audit

For any function that touches durable state under concurrency:

1. The aggregate / consistency boundary is named.
2. The idempotency invariant (don't crash / replace / reset) is named.
3. Safety and liveness claims are labeled separately in the docstring.
4. State-machine updates use CAS with rowcount checks.
5. External side effects carry an idempotency key derived from the attempt identity.
6. Elapsed time within a process uses a monotonic clock; cross-process ordering uses an authoritative source.
7. A safety test drives the function past the boundary (concurrent writers, crash mid-write, stale lease).
8. Algorithmic worst case is named when it depends on an input cap.

For any coroutine that does I/O:

9. Every external call is wrapped in `asyncio.timeout`.
10. Concurrent subtasks use `TaskGroup`, not bare `gather`, unless results-or-errors with no cancellation is the deliberate intent.
11. No blocking call runs on the loop; unavoidable blocking is offloaded (`to_thread` for I/O, `ProcessPoolExecutor` for CPU).
12. `CancelledError` is re-raised after cleanup, never swallowed.

A change failing any item is not finished, no matter how green its tests are.
