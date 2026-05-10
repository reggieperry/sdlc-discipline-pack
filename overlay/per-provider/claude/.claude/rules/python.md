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

## Asyncio
- Never time.sleep() in main process. Use await asyncio.sleep().
- Never sync blocking I/O (requests.get, open().read on network) in the event loop.

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
