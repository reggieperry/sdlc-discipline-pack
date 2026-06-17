# Decision: rule taxonomy moves to craft / go / python

**Date:** 2026-06-17. **Status:** Accepted (operator direction, after an adversarial multi-agent verdict with an explicit sycophancy check). First rename of the pack's public rule surface in its history — recorded here because that decision should be citable.

## Context

The pack's `.claude/rules/` used a flat, single-language taxonomy — `ddd`, `tdd`, `modularity`, `refactoring`, `code-structure`, `testing`, `python`, `concurrency`, `security`, `llm-app-patterns`, `xunit-patterns`, plus the machinery rules `decoupling`, `writing-style`, `architecture-config`, `stories`. Three problems:

1. **The pack fails its own `decoupling.md` rule.** Pattern 5 ("project content masquerading as principle") sets the test *"could this file ship unchanged to a different project?"* — and the flat rules fail it: `modularity.md` cited now-retired consumer internals as live "levels," and `ddd.md` hardcoded a specific application's risk rules ("the 2% Rule, 6% Rule, Triple Screen … Always") inside a principle-titled file. Principle files carrying one project's playbook.
2. **Single-language.** The flat taxonomy is Python-only, with no seam to add another language without either polluting every rule with language conditionals or forking.
3. **Divergence from where the runtime is heading.** A separate Go agent harness — intended to replace the Claude Code chain nodes — already organizes discipline as a language-neutral `craft-*` core plus per-language `go-*` / `python-*` overlays, and its rule loader keys on that prefix convention. The pack and the runtime it serves were drifting apart.

A reconciliation established the harness's three-layer organization is the structural fix for (1), supplies (2) directly (a full Go layer), and reduces the eventual node-graduation from "reconcile a fork" to "enable an overlay" for (3). The runtime convergence is a tailwind, not a forcing function — the harness embeds its own vendored rule copy and does not require the pack to converge to function — so the decision rests on the smell-fix and the cheap reversibility, with convergence as a bonus.

## Decision

Restructure `.claude/rules/` into three layers:

- **`craft-*`** — language-neutral principle: `craft-abstraction`, `craft-complexity`, `craft-documentation`, `craft-domain-modeling`, `craft-refactoring`, `craft-tdd`, `craft-xunit`. Each states the principle and defers idiom to the language overlay.
- **`go-*`** — `go-style`, `go-errors`, `go-types`, `go-concurrency`, `go-modules`, `go-testing`, `go-security`, `go-llm` (new — the pack had no Go discipline).
- **`python-*`** — `python-style`, `python-types`, `python-errors`, `python-modules`, `python-testing`, `python-concurrency`, `python-llm`, `python-security`.

Unchanged: the machinery rules `decoupling` and `writing-style`; the chain-config rules `architecture-config` (feeds `sdlc-architectural-signals.py`) and `stories`; the five long-form guides; and `sdlc-gate.py` (it gates on tool error-codes and never references rule filenames).

This is a **merge, not a replace.** The deeper pack content is folded into the matching new file, not dropped: `concurrency.md`'s systems depth (write skew, fencing tokens, CAS, idempotency) into `python-concurrency`; `llm-app-patterns.md` into `python-llm`; `refactoring.md` / `tdd.md` / `testing.md` depth into `craft-refactoring` / `craft-tdd`; `modularity.md` / `code-structure.md` into `craft-abstraction` / `craft-complexity`; `ddd.md` into `craft-domain-modeling` (Elder examples neutralized); and the Go layer plus the per-language security split arrive from the harness, scrubbed of harness specifics. The pack's unique security slices (CWE-209 error sanitization, migration symmetry, disabled-safeguard re-enable markers, the OWASP-LLM governance block) are grafted into `python-security`.

Two house conventions, resolved where the sources disagreed: **docstrings stay prose-only** (the harness's Google-style `Args:/Returns:/Raises:` mandate is dropped from `python-style`); **Pydantic is scoped to the validated LLM/external boundary** (no blanket ban, no blanket mandate).

## Old → new mapping

| Old (flat) | New |
| ---- | ---- |
| `ddd.md` | `craft-domain-modeling.md` (Elder examples neutralized) |
| `modularity.md` | `craft-abstraction.md` + `craft-complexity.md` |
| `code-structure.md` | `craft-complexity.md` |
| `refactoring.md` | `craft-refactoring.md` |
| `tdd.md` + `testing.md` | `craft-tdd.md` (+ language test mechanics in `*-testing`) |
| `xunit-patterns.md` | `craft-xunit.md` |
| `python.md` | `python-style.md` / `python-types.md` / `python-errors.md` / `python-modules.md` |
| `concurrency.md` | `python-concurrency.md` (+ `go-concurrency.md`) |
| `llm-app-patterns.md` | `python-llm.md` (+ `go-llm.md`) |
| `security.md` | `python-security.md` + `go-security.md` |
| (none) | the eight `go-*`, `craft-documentation.md` |
| `decoupling`, `writing-style`, `architecture-config`, `stories` | unchanged |

## Consequences

- **Loading is unaffected.** Rules auto-load by `paths:` frontmatter glob; a renamed file still loads. The differential gate is taxonomy-decoupled. What needs updating is prose cross-references no machine resolves: intra-pack rule→rule and guide→guide mentions, the **reviewer prompt** (`agents/reviewer/prompt.template.md`), and the `mol-sdlc-work` / `mol-sdlc-plan` formulas. A grep-based test asserts no dangling old-name reference survives.
- **Elder overlay aligns better.** Elder's rig `rules/project/` already uses `python-*` naming; the new pack scheme matches it. Elder's cross-references (`design-docs.md` table, `negative-cases.md`, `python-style.md`) update in a separate, lockstep rig PR (repo separation).
- **Reversible.** Single production consumer (the Elder chain); every step rolls back by tag-rollback + re-rsync. Behavior-preserving reorganization → a minor version bump.
- **Standalone correctness fix.** The migration removes the stale Elder coupling that made the pack fail its own `decoupling.md` test.

## Rollback

Roll the tag back and re-deploy the prior tag to the cache (the standard `/pack-deploy` rollback). No data migration, no schema, no irreversible step.
