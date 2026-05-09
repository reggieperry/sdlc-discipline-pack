---
paths:
  - ".claude/rules/**"
  - ".claude/commands/**"
  - ".claude/skills/**"
  - ".claude/hooks/**"
  - ".claude/settings.json"
---

# Decoupling discipline

Files under `.claude/` are project-portable infrastructure: portable rules,
slash commands, hooks, skills. Every change to these paths must keep the file
as portable as it was, or improve it. Project-specific content belongs in a
project overlay (e.g., `.claude/rules/project/`), not in the project-agnostic
core.

This rule fires when the file being edited is in scope. The chain reads it before proposing changes; the reviewer reads it before accepting them.

## Recognize coupling

Six patterns. Each is observable from the diff or the file's surface; flag any of them on `.claude/`-scoped changes.

### Pattern 1 — Project name in titles or prose

The literal project name appears in a heading, a title line, or a prose passage where a generic phrasing would carry the same meaning. Cosmetic but signals intent — a writer reaching for the project name suggests the abstraction is leaking.

- Recognize: file titles like `# Chore Planning — <Project Name>`, prose like "in the <project-name> system."
- Allow: the project name in a quoted example clearly marked as illustrative.

### Pattern 2 — Project-domain vocabulary as load-bearing rule content

A rule that does not function correctly without project-specific terminology. Distinct from quoting domain words in passing — load-bearing means the rule's check or instruction names project terms directly.

- Recognize: binary checks like "the change can be explained using only <project>'s vocabulary"; instructions like "use <project>'s exact terms"; concrete-domain examples presented as the rule's content rather than as illustration.
- Allow: a single illustrative example marked as such.

### Pattern 3 — Project source paths in supposedly-portable files

Concrete file paths from project content (paths that exist in this specific project, not in any project — e.g., `src/main.py`, `lib/handlers/auth.py`) cited in rules or slash commands meant to apply across projects.

- Recognize: paths in instructions, checks, or required-reading lists; tables that hardcode project file paths as "where the principle applies"; sensitive-file enumerations embedded in a slash command body.
- Allow: paths in a quoted example clearly marked as the project's specific layout, not as the rule's general claim.

### Pattern 4 — Single-source-of-truth duplication

The same list, registry, or catalog appearing verbatim in more than one `.claude/` file. Each duplication site must be updated when the source changes; one will be missed.

- Recognize: identical bullet lists, identical path enumerations, identical vocabulary lists across multiple rules or commands.
- Fix at the source, not at the duplicate. Centralize once; let consumers reference the canonical source.

### Pattern 5 — Project content masquerading as principle

A file titled and structured like a principle rule but whose body is a project-specific playbook. Test: could this file ship unchanged to a different project? If no, it is project content.

- Recognize: rule files whose examples are entirely project-specific, whose checks reference project-specific invariants, whose helpers are project-specific shapes.
- The principle and the playbook are different artifacts. Keep the principle in the portable rule; move the playbook to a project overlay.

### Pattern 6 — Cosmetic project-name leakage in code-comments

Hooks, scripts, or skill files with docstrings or comments that mention the project by name. Low severity individually; accumulates and signals that the abstraction is leaking.

- Recognize: docstring lines like `"""<Project> uses this for traceability."""`; inline comments like `# Add <project>-specific blocks here`.
- Rephrase in project-agnostic terms. The code does the right thing; the prose should match.

## Refactor toward decoupling

Each pattern maps to a fix.

| Pattern | Fix |
| ------- | --- |
| 1 — Project name in titles or prose | Drop the name; let CLAUDE.md provide project context |
| 2 — Vocabulary as load-bearing content | Replace with "the project's ubiquitous language" or similar; project supplies its vocabulary list in an overlay |
| 3 — Project source paths in instructions | Parameterize through CLAUDE.md, a config file, or a project-overlay rule that the project owns |
| 4 — Single-source-of-truth duplication | Centralize the list at one source; downstream consumers reference it |
| 5 — Project content masquerading as principle | Split the file: portable principle stays in `.claude/rules/<name>.md`; project content moves to a project overlay |
| 6 — Cosmetic name-leakage in comments | Rephrase comments in project-agnostic terms |

## Decide ambiguous cases

Two judgment calls come up repeatedly. Resolve before merging; ask the operator if still unclear.

### Load-bearing or illustrative?

A project-specific reference is **load-bearing** when the rule's check or instruction depends on it — removing it changes what the rule does. **Illustrative** when it's a chosen-for-clarity example — removing it leaves the rule's meaning intact.

- Load-bearing → must parameterize or move to overlay.
- Illustrative → may stay if the example is marked as such ("for example, in this project, ..." rather than "the value is X").

When the distinction is genuinely unclear, the safer call is "treat as load-bearing." A spurious parameterization is cheap to undo; a missed coupling carries forward into every future project.

### Project-specific glob or generic glob?

A rule's auto-load `paths` frontmatter determines its scope. Two cases:

- **Project-specific glob** (e.g., `paths: ["indicators/**", "agents/scanner_agent.py"]`) — the rule is project content. Project-specific examples are appropriate. The overlay home is `.claude/rules/project/`; the rule itself was never portable.
- **Generic glob** (e.g., `paths: ["**/*.py"]`, `paths: ["tests/**"]`, or `.claude/`-scoped paths) — the rule is supposed to be portable. Project-specific content is leakage; refactor.

A rule whose globs are generic but whose body is project-specific (Pattern 5) is the case to watch for. The glob makes the rule fire on project-portable paths; the body assumes project-specific context. Split.

## What lives where

| Content type | Home | Auto-load |
| ------------ | ---- | --------- |
| Universal principle (TDD discipline, modularity, DDD, decoupling) | `.claude/rules/<name>.md` | Generic glob |
| Project-specific playbook (testing helpers, common-error remediations) | `.claude/rules/project/` | Project-specific glob |
| Project-domain rules (specific methodology parameters, broker constraints, framework idioms) | `.claude/rules/project/` | Project-specific glob |
| Sensitive-files list, design-doc registry, project vocabulary list | `CLAUDE.md` or a config file at one source | Read by consumers |

The portable pack ships the universal-principle column. The overlay column is per-project content authored against the principle.

## Self-audit

Run before considering a `.claude/`-scoped change done. Each item is binary; partial credit does not exist.

1. Project name does not appear in any title, heading, or load-bearing prose passage in the touched files.
2. No binary check or instruction in the touched rules names project-specific terminology as the criterion.
3. No concrete project file path appears in instructions, checks, or required-reading lists. Paths appear only in marked illustrative examples.
4. No list, registry, or catalog appears verbatim in more than one `.claude/` file.
5. Each touched rule file passes the "could this ship unchanged to a different project?" test, OR the file is in a project-overlay directory with a project-specific auto-load glob.
6. Code comments and docstrings in touched hooks, scripts, or skill files do not mention the project by name.
7. If a rule's `paths` frontmatter is generic, the rule body is principle-only — no project-specific examples in load-bearing positions.
8. Centralized sources (CLAUDE.md, config files) are the canonical location for project-specific lists; consumers reference rather than duplicate.

A change failing any item is not finished.

## Antipatterns

- Adding a project-name reference to a generic rule "for clarity."
- Copy-pasting a list across slash commands "to be safe."
- Embedding a project-specific helper signature in a rule meant to teach a pattern.
- Placing a project-specific rule under a generic auto-load glob.
- Documenting a "to-do: add project-specific content" placeholder in a portable file.
- Leaving the project name in a code comment because "we'll fix it later."
