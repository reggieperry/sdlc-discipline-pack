---
paths:
  - "**/*.py"
---

# Python exceptions and error handling

Python signals errors with exceptions; handling them precisely is core to reliability. Sources: PEP 8, PEP 20 ("errors should never pass silently"), the Google Python Style Guide. The design principle behind several of these rules — define errors out of existence — is from `craft-complexity.md`.

> See `craft-complexity.md` for defining errors away, `python-style.md` for EAFP control flow, and `python-llm.md` for surfacing validation errors back to the model.

## Catch precisely

- **Catch specific exception types; never write a bare `except:` or `except Exception` to swallow everything.** A bare except hides bugs, including `KeyboardInterrupt`/`SystemExit`. Catch the narrowest type you can actually handle.
- **Keep each `try` block to the minimum code that can raise** — wrapping a wide block makes it unclear which statement failed and risks catching an exception you didn't anticipate.
- **Never let an error pass silently.** An `except` that does nothing must be a deliberate, commented decision (`except KeyError: pass  # absent key means default`), not an accident.

## Raise well

- **Derive custom exceptions from `Exception`, never `BaseException`**, and suffix the class with `Error`. Define a small exception type when callers need to distinguish a failure mode; otherwise raise a built-in (`ValueError`, `KeyError`, `TypeError`).
- **Chain with `raise NewError(...) from cause`** when re-raising as a different type, so the original traceback is preserved. Don't catch an error only to re-raise a vaguer one that loses context.
- **Don't leak secrets** (tokens, keys, credential paths) into an exception message — they surface in logs and tracebacks.

## Handle once, and don't use exceptions as flow

- **Handle each error once — don't both log it and re-raise it** (that produces duplicate, confusing logs). Log where you handle, or propagate with context, not both.
- **Don't use exceptions for ordinary control flow across module boundaries.** EAFP within a function is idiomatic; an exception that routinely crosses public APIs as a signal is a design smell — return a value or model the outcome in the type instead.

## Define errors out of existence

- **Prefer redefining an operation so the error case becomes the normal case** over adding another exception — an `ensure_absent` that returns quietly when the thing is already gone needs no error. Where you can't, handle the error as low as possible (mask it) or aggregate many handlers into one high in the call path. Fewer raise/except sites means simpler, more reliable code. (`craft-complexity.md`.)
