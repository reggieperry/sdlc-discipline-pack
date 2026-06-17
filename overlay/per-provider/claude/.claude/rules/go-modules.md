---
paths:
  - "**/*.go"
  - "go.mod"
  - "go.sum"
---

# Go module and package layout

How to organize packages and the module tree. Source of record: the official Go layout guide (go.dev/doc/modules/layout). The popular `golang-standards/project-layout` repo is **not** a Go standard and is explicitly contested — prefer the official shape.

> See `craft-complexity.md` for deep, cohesive modules, `craft-domain-modeling.md` for naming packages in domain terms, and `go-style.md` for package-name conventions.

## Use the official layout, start flat

- **Treat `go.dev/doc/modules/layout` as authoritative, not `golang-standards/project-layout`.** The latter is community-run and not endorsed by the Go team; Russ Cox (the Go tech lead) filed issue #117 on it stating the `pkg/` convention is *not* what most of the ecosystem does and that "Go repos tend to be much simpler." Reach for an elaborate tree only when a specific need justifies a specific directory.
- **Start flat: put the package's code in the module root, one package per directory, package name = last path component of the module.** Add structure only when the module actually grows multiple packages or commands.
- **Do not introduce a `pkg/` directory** — it adds a needless `pkg` segment to every import path with no benefit.

## `internal/` and `cmd/`

- **Put supporting packages that other modules should not import under `internal/`.** The toolchain forbids imports of `internal/...` from outside the module subtree, so you can refactor those APIs freely — ideal for core internals you don't want external callers depending on directly.
- **Use `cmd/<binary>/main.go` once you have more than one executable, or a mix of commands and importable packages.** A single command belongs in the root as `package main`; each additional binary is a natural `cmd/<binary>/`.
- **For a server-style binary with no exported library, keep the implementation in `internal/` and a thin entrypoint in `cmd/`** — a self-contained binary has nothing to export, so don't make its packages importable.

## Package cohesion

- **One package per directory; never split a package across directories or create an import cycle** — Go enforces directory = package and forbids cycles; a cycle signals a missing third package or a misplaced type.
- **Name packages after what they provide, not grab-bag names like `utils`, `common`, `helpers`, or `base`** — the import site reads `client.Call`, so the package name is part of the API. A `utils` package has no cohesive responsibility and becomes a dumping ground.
- **Keep packages deep and cohesive — a small, stable API over substantial internals — rather than many shallow packages** (`craft-complexity.md`). Organize by domain concept (`account`, `pricing`, `inventory`), never by technical layer or by DDD-pattern type (`craft-domain-modeling.md`).
- **Draw the hardest package boundaries along bounded-context seams first, then partition within.** Where your code meets a model it doesn't own — an orchestration substrate (its transport, the env it injects), an external CLI you wrap (e.g. `git` or a linter), a separate system you integrate with, the LLM's output schema — put a package boundary with an explicit translation type at it (an environment-config struct, the wrapped-CLI client interface, the structured-output struct), rather than letting a foreign model's terms leak across. A seam that's really a context boundary needs translation, not a shared import (`craft-domain-modeling.md`).

## Dependencies

- **Pin and verify module dependencies; keep `go.mod`/`go.sum` honest.** Tidy with `go mod tidy`, and pin a dependency to a version known compatible with the rest of the toolchain rather than floating it (this matters for the model SDK and its schema-generation dependency — see `go-llm.md`).
