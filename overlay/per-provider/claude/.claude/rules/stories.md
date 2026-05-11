---
description: Story spec authoring conventions for stories/*.md files; loaded when editing files under stories/**
conditional-read: stories/**
---

# Story spec authoring rule

This rule loads when editing files under `stories/`. Stories are the design-time artifact for the SDLC chain — markdown files with YAML frontmatter that the `stories.py` bridge tool translates into bd beads.

## File naming

`<PREFIX>-<NNN>-<slug>.md` where:

- `<PREFIX>` is the bd issue-prefix uppercased (e.g., `EL` for an Elder rig with `prefix = "el"` in `city.toml`).
- `<NNN>` is the monotonic story number; never reused, never reassigned.
- `<slug>` is short kebab-case for human readability.

Stable IDs survive renames, scope changes, and closure-and-replacement. Other documents reference the ID; the slug can change but the number cannot.

## Frontmatter schema

```yaml
---
story_id: EL-014
title: Risk parameters consumer audit and migration
phase: 0
build_item: 3
deps:
  - EL-001
parent:
labels:
  - phase-0
  - foundation
  - sensitive
sensitive_files:
  - risk_parameters.py
status: draft
filed_as_bead:
---
```

Required: `story_id`, `title`, `phase`, `status`. The rest are optional but follow conventions below.

| Field | Type | Purpose |
| ---- | ---- | ---- |
| `story_id` | string | Stable ID, never changes. Matches filename prefix. |
| `title` | string | Human-readable title. |
| `phase` | int | 0..6, from the rig's build-plan phase ordering. |
| `build_item` | int / empty | Build-plan item number this story implements. Empty for tactical / audit stories. |
| `deps` | list of story IDs | Stories that must close before this one is ready. |
| `parent` | story ID / empty | Optional epic-grouping parent (uses bd's `parent_key`). |
| `labels` | list of strings | bd labels for query filtering (`phase-0`, `sensitive`, `requires-ib`, etc.). |
| `sensitive_files` | list of paths | Subset of `.claude/rules/project/sensitive-files.md` this story touches. Empty list `[]` means none. |
| `status` | enum | `draft` / `ready` / `filed` / `in-flight` / `merged` / `closed`. |
| `filed_as_bead` | bead ID / empty | Populated by `stories.py file`; empty until filed. |

## Story body sections

After frontmatter, the body uses these sections in order:

```markdown
# EL-NNN <title>

## Outcome

<One sentence stating the user-observable result.>

## Acceptance criteria

- [ ] <Testable outcome 1>
- [ ] <Testable outcome 2>

## Scope

**In:** <files / modules>

**Out:** <explicit exclusions>

## Sensitive files

<Paths from the rig's sensitive-files list, or `None.`>

## Notes

<Context, references, design decisions made before the chain runs.>
```

## Lifecycle states

| State | Set by | Meaning |
| ---- | ---- | ---- |
| `draft` | Operator | Being written; not yet ready for chain consumption. |
| `ready` | Operator | Self-audited; deps resolved; suitable for filing. |
| `filed` | `stories.py file` | Translated to a bd bead; `filed_as_bead` populated. |
| `in-flight` | Chain | Worker session has claimed the bead. |
| `merged` | Operator | PR merged to main; bead pending close. |
| `closed` | `bd close` | bd state closed; story still in active directory. |
| (archived) | `stories.py archive` | Story file moved to `stories/_archive/` with closing note. |

Left-side transitions (`draft → ready`, `merged → archived`) are operator-driven. Right-side transitions (`filed → in-flight → merged → closed`) are chain-driven or tool-driven.

## Validation

`python3 .claude/sdlc-discipline/stories.py validate` checks:

- Schema (required fields present)
- Status enum validity
- Filename matches `story_id`
- Every `deps` entry points at a real story file
- Every `sensitive_files` entry appears in the rig's `.claude/rules/project/sensitive-files.md`
- No dependency cycles in the graph

Run before flipping status from `draft` to `ready`. Run in pre-commit and CI.

## When editing a story file

1. Stay within the schema above — adding unknown fields is fine but won't be carried to bd metadata.
2. Don't rename a story file's number portion (`EL-014` → `EL-115`). The number is a stable cross-reference.
3. Use the slug portion to clarify titles when needed; the bridge doesn't care about the slug.
4. Set `status: ready` only after `stories.py validate` is clean for the file.
5. If adding to `sensitive_files`, verify the path exists in the rig's `.claude/rules/project/sensitive-files.md`. The validate command flags mismatches.

## What this rule does NOT cover

- Story scope decisions (what work belongs in one story vs. another) — those are design judgments, not authoring rules.
- The build plan ordering — that lives in the rig's `docs/build-plan.md`.
- The bd query language for runtime filtering — see `bd query --help`.

## Source-of-truth boundary

Stories are the design-time artifact. bd is the runtime substrate. The boundary is one direction (story → bead at file time); after filing, bd owns runtime state. Don't round-trip.
