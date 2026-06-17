---
paths:
  - "**/*.py"
---

# Python style and idioms

The base layer of Python discipline: naming, idioms, and the data-structure choices. Sources: PEP 8, PEP 20 (the Zen of Python), the Google Python Style Guide, The Hitchhiker's Guide to Python, Brett Slatkin's *Effective Python*, and Patrick Viafore's *Robust Python*. Where the major guides disagree, the disagreement is named with a default.

> See `craft-complexity.md` for why precise names and obvious code matter, `craft-documentation.md` for the comment-and-docstring discipline, `python-errors.md` and `python-types.md` for the exception and typing idioms referenced here, and `python-modules.md` for package layout.

## Formatting

- **Let a formatter own layout — run Black or Ruff; never hand-format.** Indent with 4 spaces, never tabs. **Disagreement:** PEP 8 says 79 columns, Google 80, Black/Ruff default to 88 — pick one project-wide limit and let the tool enforce it. Break long lines inside parentheses, never with a trailing backslash.

## Naming

- **`snake_case` for functions, methods, variables, modules; `CapWords` for classes and exceptions; `CAPS_WITH_UNDER` for module-level constants.** Suffix an exception class with `Error`.
- **Reserve single-character names for iterators (`i`), caught exceptions (`e`), and file handles (`f`); never use `l`, `O`, or `I`** (indistinguishable from `1`/`0`).
- **One leading underscore marks a non-public name; reach for two only when you genuinely need name-mangling against subclasses.** Don't encode the type in the name (`names`, not `id_to_name_dict`) — that belongs in the annotation.

## Imports

- **All imports at the top, after the module docstring; grouped stdlib → third-party → local with a blank line between groups; one import per line.** Prefer absolute imports; never `from x import *` — it makes the namespace unknowable.

## Idioms

- **Compare to `None` and other singletons with `is`/`is not`, never `==`.** Use `isinstance(obj, T)`, not `type(obj) is T`.
- **Test emptiness with `if seq:` / `if not seq:`, never `len(seq) == 0`; test a flag with `if flag:`, never `== True`.** For an integer not derived from `len()`, compare explicitly against `0`.
- **Prefer unpacking over indexing** (`first, second = pair`, `a, b = b, a`); iterate with `enumerate`/`zip`, not `range(len(...))`; look up with `in` and `dict.get(key, default)`. Never mutate a list while iterating it — build a new one.
- **Use comprehensions over `map`/`filter`, but keep them simple** — at most one `for` plus one optional `if`; promote anything more to a loop or helper. Return a generator when the result is consumed once; a list comprehension when it's reused.
- **Use a `with` statement for any scoped resource** (files, locks, connections) — it guarantees cleanup on exceptions.
- **Favor EAFP (`try`/`except`) when the success path dominates** over pre-checking with LBYL. (`python-errors.md`.)

## Functions

- **Avoid mutable default arguments — the default is evaluated once at definition time and shared across calls.** Use `None` and build the real default inside: `def f(x=None): x = [] if x is None else x`.
- **Define a named function with `def`, never bind a `lambda` to a name** (a name gives usable tracebacks); reserve `lambda` for short inline callables. **Keep returns consistent** — either all paths return a value or none do; return early for guards to flatten nesting.

## Docstrings

- **Give every public module, class, and function a docstring; PEP 257 mandates the public API and is silent on non-public members** — document a private helper only where the logic is non-obvious. The module docstring leads the file (imports follow it); a package docstring lives in `__init__.py` and names what the package exports.
- **Open with a one-line summary in the imperative mood, ending in a period** (`"""Return the resolved config path."""`, not `"""Returns..."""`). For a multi-line docstring, follow the summary with a blank line, then the detail, and close `"""` on its own line.
- **Write docstrings as prose — let type hints carry the parameter and return types, and use the docstring to explain *what* the function does and *why*.** Do not use structured `Args:`/`Returns:`/`Raises:` sections; the annotations already name the types, so repeating them is duplication that drifts out of date. Where a function raises a domain exception the caller should handle, name it inline in the prose. State behavior and guarantees, not the implementation. (`craft-documentation.md`.)

## Data structures

- **Reach for `@dataclass` first for data-holding classes; `@dataclass(frozen=True)` for an immutable value object** (pair it with immutable field types). Give a list/dict/set field a `field(default_factory=...)`, never an inline mutable literal — the same shared-default trap.
- **Prefer a frozen dataclass over `NamedTuple`** for a new immutable type (a `NamedTuple` compares equal to a plain tuple, hiding type bugs); escalate to a plain class only when behavior exceeds data storage.
- **Treat a bare `dict` as a transient format for unvalidated external data — convert to a typed structure immediately.** When you need *runtime-enforced* validation (not just structure), use Pydantic; stdlib dataclass/`NamedTuple` hints are not enforced at runtime. (`python-types.md`, `python-llm.md`.)

## Properties, enums, filesystem

- **Expose attributes directly; add a `@property` only for a trivial computed value or as a no-break migration path when a public attribute later needs logic** — no Java-style getters/setters. Avoid `@staticmethod` (prefer a module function); reserve `@classmethod` for alternative constructors.
- **Use `Enum` (or `StrEnum`/`IntEnum`) for a fixed set of related constants** so annotations are explicit; `auto()` when only identity matters, `@unique` to forbid duplicate aliases. Prefer plain `Enum` and reach for `StrEnum`/`IntEnum` only when serialization or int interop demands it. For a bare value restriction, use `typing.Literal`, not an enum.
- **Use `pathlib.Path` over `os.path`** for filesystem work, and avoid module-level mutable state — pass state explicitly or encapsulate it.

## PEP 20

- **Explicit over implicit, flat over nested, simple over complex, readable over clever; one obvious way to do it.** Never let errors pass silently unless the silencing is explicit. If an implementation is hard to explain, it's a bad design.
