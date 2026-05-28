---
paths:
  - ".claude/rules/project/architecture.toml"
---

# Architecture config (`architecture.toml`)

`.claude/rules/project/architecture.toml` is the rig's architectural-shape declaration. The pack's `sdlc-architectural-signals.py` script reads it to decide which files matter when surfacing merge-protocol signals (A, B, C). Without it, every PR routes to `human_required` — the chain can't tell which changes are architectural.

Rigs author this file once and commit it. The pack ships no default; the shape is rig-specific.

## File format

TOML. Stdlib-only parse (`tomllib`); no PyYAML dependency. Three top-level keys, each a list of strings:

```toml
sensitive_files     = ["risk_parameters.py", "agents/risk_agent.py", "indicators/*.py"]
domain_model_files  = ["core/state.py", "core/domain.py"]
protocol_modules    = ["core/agent.py"]
```

A missing key defaults to an empty list — the corresponding signal cannot fire on that axis. A missing *file* (no `architecture.toml` at the declared path) defaults to permanent `human_required` for every PR.

## Field semantics

**`sensitive_files`** — paths whose edits should always route to human review. Touched by Signal A (sensitive file delta). Entries are repo-relative; shell globs work via `fnmatch` (`*`, `?`, `[abc]`).

Cross-check the list against `.claude/rules/project/sensitive-files.md` if the rig has one. The two lists serve different consumers (signals script vs. glance rubric) but should declare the same set.

**`domain_model_files`** — modules where the rig's domain entities and value objects live (the frozen `@dataclass` classes that flow through the pipeline). Touched by Signal C (domain-model field delta). Field *removals* fire the signal; field *additions* are additive and do not fire.

Only `@dataclass(frozen=True)` classes are scanned. Non-frozen dataclasses, `attrs`-based models, Pydantic models, and plain classes are invisible to Signal C — if a rig uses those instead of frozen dataclasses, Signal C goes silent.

**`protocol_modules`** — modules declaring `@runtime_checkable Protocol` classes that other code depends on structurally. Touched by Signal B (Protocol signature delta). Adding a new method to an existing Protocol does not fire; changing or removing an existing method signature does.

Only `@runtime_checkable` Protocols are scanned. Plain `typing.Protocol` classes are visible to type-checkers but not to runtime `isinstance` — they're invisible to Signal B unless decorated.

**`numbered_catalogs`** — categories of numbered IDs that workers should resolve from `<CATEGORY>-NEXT` sentinels at plan time. Each category maps to one or more source files (or path globs) and a regex that captures the integer. The worker scans the sources, finds the highest existing integer for the category, and substitutes the sentinel with the next free integer (`max + 1`, or `1` if no matches exist).

The shape differs from the three list-valued fields above. Each category is a TOML table:

```toml
[numbered_catalogs.STAGE]
sources = ["docs/elder-invariants.md"]
content_regex = '^### STAGE-(\d+):'

[numbered_catalogs.MIGRATION]
sources = ["db/migrations/*.sql"]
filename_regex = '^db/migrations/(\d{4})_'
```

`content_regex` matches against each line of every source file; the capturing group must yield an integer. Use this when entries are headings or marked lines in a markdown or text file. `filename_regex` matches against each path resolved from the source globs; the capturing group must yield an integer. Use this when entries are numbered files in a directory (migration files, story IDs, etc.). Exactly one of `content_regex` or `filename_regex` per category.

The worker's resolution procedure is described in `agents/worker/prompt.template.md` (Numbered-catalog ID substitution). The reviewer audits for unsubstituted sentinels in the diff and flags any hit as a blocker.

## Glob conventions

Entries may be exact paths or shell-style globs:

- `risk_parameters.py` matches that file exactly
- `agents/risk_*.py` matches any `risk_<something>.py` under `agents/`
- `indicators/*.py` matches direct children of `indicators/`
- `core/**/*.py` does NOT recurse — `fnmatch` is not recursive. Use one entry per directory if you need recursive coverage, or list the modules explicitly.

Paths are repo-relative. No leading `/`. Trailing slashes are not interpreted.

## Worked example (Elder rig)

```toml
sensitive_files = [
  "risk_parameters.py",
  "agents/risk_agent.py",
  "agents/risk_gates.py",
  "agents/risk_evaluate.py",
  "agents/scanner_agent.py",
  "agents/analysis_agent.py",
  "indicators/elder.py",
  "indicators/math.py",
  "indicators/signals.py",
  "indicators/divergence.py",
  "indicators/snapshots.py",
  "core/domain.py",
  "core/trade.py",
  "indicators/types.py",
  "db/schema.sql",
]

domain_model_files = [
  "core/state.py",
  "core/domain.py",
  "core/trade.py",
  "indicators/types.py",
]

protocol_modules = [
  "core/agent.py",
]

[numbered_catalogs.STAGE]
sources = ["docs/elder-invariants.md"]
content_regex = '^### STAGE-(\d+):'

[numbered_catalogs.COST]
sources = ["docs/elder-invariants.md"]
content_regex = '^### COST-(\d+):'

[numbered_catalogs.MIGRATION]
sources = ["db/migrations/*.sql"]
filename_regex = '^db/migrations/(\d{4})_'
```

## Missing-config behavior

If `architecture.toml` is absent, the signals script returns:

```json
{
  "signals": ["MISSING_CONFIG"],
  "recommendation": "human_required",
  "rig_config": {"present": false, ...}
}
```

Every PR routes to manual review. This is intentional: a rig that hasn't declared its architectural shape can't be auto-merged safely.

## When to update

- Adding a new sensitive module — add to `sensitive_files` in the same PR
- Splitting a domain module — update `domain_model_files` to point at the new modules
- Adding a new Protocol that consumers depend on structurally — add its module to `protocol_modules`
- Removing a module — remove its entries; the signals script does not warn on dangling globs

Treat updates to `architecture.toml` as routine — they're declarations, not behavior changes. The signals script catches the next PR using the new shape.
