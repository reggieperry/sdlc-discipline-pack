---
paths:
  - "**/*.py"
---
# Python standards

## Style
- Protocol with @runtime_checkable for interfaces. Not ABC.
- @dataclass for structured data. Not Pydantic, TypedDict, or NamedTuple.
- class Foo(str, Enum) for domain constants. The str mixin makes them serializable.
- f-strings only. pathlib.Path only. Absolute imports only.
- logger = logging.getLogger(__name__) at module top. Never print() in production.
- async def for I/O. Plain def for computation.
- Built-in generics: list[str], dict[str, int]. X | None, not Optional[X].
- Decimal for money. Never float for currency or financial calculations.
- match/case for enum branching and sealed-type dispatch.
- Guard clauses at function top, then happy path. Not deep nesting.

## Comments and docstrings
- Comments explain WHY, never WHAT. Delete any comment where removing it loses zero information.
- Docstrings always start with prose explaining what the function does and why, never a name-repeating restatement.
- All functions — public and internal — use prose-only docstrings. Type hints carry the parameter and return type information; restating that in prose is forbidden.
- Never write Args:/Returns:/Raises: structured sections. Type hints carry parameter and return type information; if a parameter's meaning isn't obvious from name + type, explain it in the prose inline.
- Length scales with what the type hints can't convey. A self-named function with self-explanatory hints gets a one-liner. A function with non-obvious algorithm or design rationale gets a paragraph explaining the WHY.
- For domain exceptions (ValidationError, ConnectionError) raised by a function, mention them in the prose where they're contextually relevant — not as a structured Raises: block.

## Function and module design
- Max 25 lines per function body. Extract a helper if longer.
- One return type. Returns Trade or raises — never Trade | None | str.
- No boolean parameters that change behavior. Use two functions.
- One responsibility per module. All external services injected via constructor.
- Keep __init__ boring: assign fields, no logic.

## Naming
- Verbs for functions: compute_ema, fetch_holdings, check_risk.
- No Manager/Handler/Processor/Service suffixes unless genuinely warranted.
- No data/info in variable names. trade_data is just trade.
- No get_ prefix on property access. account.equity not account.get_equity().
- Constants: UPPER_SNAKE at module top. Private helpers: underscore prefix.

## Abstraction
- No class when a function will do. No state → no class.
- No abstract base classes until you have two implementations.
- No utils.py, helpers.py, common.py, or constants.py with one constant.
- No wrapping standard library. pathlib.Path doesn't need a FileManager.
- If a class has only __init__ and one method, make it a function.

## Error handling
- No bare except: or except Exception: that silences errors.
- Specific exceptions defined near the code that raises them: ValidationError, ConnectionError.
- raise X from Y to chain exceptions.
- Never use try/except for control flow. Check conditions explicitly.

## Imports
- Absolute: from myapp.core.state import AppState
- Never relative imports.
- Import specific names: from datetime import datetime, timedelta.
- Lazy import heavy libraries if only used in one function.

## Algorithmic complexity
- Membership tests inside a loop use set or dict. Never `if x in some_list` inside another loop.
- Build strings with `"".join(parts)`, not `s += ...` in a loop.
- Sort once outside the loop. Don't re-sort the same sequence on every iteration.
- Use generator expressions when feeding any, all, sum, min, max, next — they short-circuit and don't allocate.
- Materialize to a list when you need length, indexing, or two passes. Don't iterate a generator twice.
- In hot paths, bind `dict.keys()` / `.values()` / `.items()` to a name once; don't recompute the view per iteration.
- Double loops over the same collection need a hashed lookup or a sort+merge — never O(n²) by default.

## Iteration
- Use `enumerate(seq)` instead of `range(len(seq))`. Use `zip(a, b)` instead of parallel indexing.
- Use `dict.items()` to iterate key/value pairs. Don't index back into the dict in the loop body.
- Use `zip(a, b, strict=True)` when length equality is an invariant. Silent truncation is a bug surface.
- Never mutate a list or dict while iterating it. Iterate `list(d)` or build a new collection via comprehension.
- Use `itertools.pairwise(seq)` for consecutive-element comparisons (price bars, time series).
- Use `itertools.chain.from_iterable` to flatten one level. Don't nest comprehensions for flattening.
- `for ... else` is reserved for "loop completed without break." If you have to look it up, refactor.

## Asyncio
- Never `time.sleep()` in main process. Use `await asyncio.sleep()`.
- Never sync blocking I/O (`requests.get`, `open().read` on network, sync DB drivers) in the event loop.
- Default to `async with asyncio.TaskGroup()` for concurrent sibling tasks. Use `asyncio.gather(*, return_exceptions=True)` only when partial failure is acceptable and siblings must continue (e.g., scanning many tickers where some can fail).
- Use `asyncio.as_completed` only when streaming earliest-first results matters.
- Every `asyncio.create_task()` keeps a strong reference — named variable, module-level set, or a `TaskGroup`. Unreferenced tasks can be garbage-collected mid-flight in 3.12+.
- Catch `asyncio.CancelledError`, run cleanup in `finally`, and re-raise. Never swallow `CancelledError`.
- Bound concurrent fan-out with `asyncio.Semaphore`. For unbounded or streaming inputs, use `asyncio.wait(..., return_when=FIRST_COMPLETED)` over a rolling task set rather than `gather` over semaphore-wrapped coroutines.
- CPU-bound work goes through `loop.run_in_executor(pool, fn, *args)`. The target function is module-level (picklable) and arguments are picklable.
- Sync helpers called from a coroutine are fine if and only if they don't do I/O and aren't CPU-heavy.

## Self-review gate (run before declaring ANY task done)
1. `uv run pytest tests/ -v` — passes with ≥60% coverage
2. `uv run ruff check .` — no violations
3. `uv run mypy .` — no type errors
4. `uv run lint-imports` — no boundary violations (project-specific tool)
5. Functions under 25 lines
6. Type hints and docstrings on public functions
7. No bare except blocks
8. If domain-specific parameters changed: verify against authoritative source
9. Slop check: remove restating comments, name-repeating docstrings, unnecessary abstractions
