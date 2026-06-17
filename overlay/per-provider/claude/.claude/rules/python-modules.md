---
paths:
  - "**/*.py"
  - "pyproject.toml"
---

# Python project and package layout

How to organize a Python package and its metadata. Sources: the Python Packaging User Guide (src layout, `pyproject.toml`), PEP 621, and mypy's re-export rules.

> See `craft-complexity.md` for deep, cohesive modules, `craft-domain-modeling.md` for packages named in domain terms and the bounded-context seams, and `python-style.md` for import conventions.

## Use the src layout

- **Put importable packages under `src/<package>/`, with config and tests at the project root.** This keeps the cwd off `sys.path` so an installed copy can't be shadowed, and editable installs import only real package files.
- **Require an editable install (`pip install -e .`) before running or testing**, so tests run against the *installed* package and surface packaging/metadata bugs a flat in-tree layout hides. *This is strong consensus, not a PEP mandate — a trivial script can stay flat.*

## Packages and boundaries

- **One import package per directory** (a directory with `__init__.py`); never split a package across directories or create an import cycle.
- **Mark internal modules with a leading underscore and re-export the public API explicitly from the package `__init__`** — with `--no-implicit-reexport` on, only deliberately re-exported names are public.
- **Name packages after the domain concept they provide, not by technical layer or pattern type** (`billing`, `scheduling`, `inventory` — not `utils`, `models`, `services`). Draw the hardest package boundaries along bounded-context seams, with explicit translation across each. (`craft-domain-modeling.md`.)
- **Keep packages deep and cohesive — a small public surface over substantial internals — rather than many shallow modules** (`craft-complexity.md`).

## pyproject.toml (PEP 621)

- **Declare project metadata in `[project]`: `name` (the one field that can't be `dynamic`), plus `version`, `dependencies`, `requires-python`, and `optional-dependencies` for extras.** List anything computed elsewhere (a git-tag version, `__version__`) under `dynamic = [...]` rather than hardcoding it.
- **Declare the build in `[build-system]` with `requires` and `build-backend`** (PEP 517/518) — without it, tools can't build the project.
- **Keep each tool's config in its own `[tool.*]` table** (`[tool.mypy]`, `[tool.ruff]`, `[tool.pytest.ini_options]`) — never in `[project]`, which is standardized metadata only.
