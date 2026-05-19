# sdlc-discipline pack

A Gas City pack that runs an SDLC chain — plan, build, test, review, document, finalize — against any rig with a Click/pytest/Python project shape. Five pool agents, zero named sessions, parallel-by-default. Concurrency is bounded by host CPU/RAM and per-account API rate limits, not by named-session serialization.

## Contents

- [Purpose](#purpose)
- [Architecture (v2.0)](#architecture-v20)
- [Authoring stories](#authoring-stories)
- [What's in this pack](#whats-in-this-pack)
- [Prerequisites and installation](#prerequisites-and-installation)
- [How rules reach the agent](#how-rules-reach-the-agent)
- [Principal-engineer guides (v2.5)](#principal-engineer-guides-v25)
- [Story-graph bridge (v2.6)](#story-graph-bridge-v26)
- [Differential gates (v2.4)](#differential-gates-v24)
- [Three operating modes](#three-operating-modes)
- [Per-story overrides](#per-story-overrides)
- [The glance rubric](#the-glance-rubric)
- [Architectural signals (v2.10.0)](#architectural-signals-v2100)
- [Tech-debt automation (v2.11)](#tech-debt-automation-v211)
- [Operator notification (v2.12)](#operator-notification-v212)
- [Claude retry wrapper (v2.12)](#claude-retry-wrapper-v212)
- [Supervisor startup wrapper (v2.18.0)](#supervisor-startup-wrapper-v2180)
- [Cost tracking](#cost-tracking)
- [Running the chain](#running-the-chain)
- [Watching the chain](#watching-the-chain)
- [Project assumptions](#project-assumptions)
- [Versioning](#versioning) (incl. [version history](#version-history))

## Purpose

LLM-based coding agents can write working code. They cannot, on their own, write *consistent* code at codebase scale — model outputs drift across independent generations even when prompts and seeds are held constant. A codebase built by many parallel agents under no external constraint looks like ten different developers wrote it: refactors stop composing, onboarding the next agent (or human) gets harder, and the trust required to ship gets absorbed back into per-change human review.

This pack is the *discipline layer* of a two-layer answer to that problem. It encodes engineering disciplines that have shaped software practice for 50 years — modularity, test-driven development, domain-driven design, refactoring — as auto-loaded rules, differential-gate static analysis, and a chain of pool agents whose phase-bounded roles enforce each other's work. [Gas City](https://github.com/gastownhall/gascity) is the complementary *orchestration layer* that runs many such chains in parallel. Together they enable a *software factory*: many agents producing work to a defined quality bar without per-change human review. The full framework — the discipline-orchestration architecture, the case study it derives from, and the engineering practice changes it requires — lives at [`docs/safe-agentic-engineering-thesis.md`](docs/safe-agentic-engineering-thesis.md).

This pack is being developed against a real codebase — the Elder Trading System, a quantitative trading platform implementing Alexander Elder's methodology — and that codebase doubles as the running case study throughout the principal-engineer guides, test fixtures, and version-history narratives. Read `Elder` as `the codebase you're working on` and adapt the examples to your own domain.

Neither layer eliminates the human engineer; together they relocate the engineer's work. The chain runs autonomously within a story's scope, but the *story itself* — acceptance criteria, scope boundaries, sensitive-file declarations, dependency links — must be authored by a human with enough context on the codebase, the domain, and the constraints to write a contract the chain can fulfill mechanically. A vague story produces a wandering chain that burns tokens and ships work that does not match the intent. A tight story produces a clean chain run that lands a PR matching the author's expectation. Specifying executable work is the new craft this pack assumes; no amount of discipline or orchestration recovers from an under-specified story.

## Architecture (v2.0)

The chain has five LLM pools and zero named sessions. A story bead enters at the worker pool and exits closed after the finalizer.

```text
kickoff (one-shot)
  └─→ worker pool (max=5)        plan + build + self-audit
        └─→ tester pool (max=3)  pytest + ruff + mypy + 3-round resolution
              ├─[red after 3] back to worker pool
              └─[green]
                   └─→ reviewer pool (max=3)    plan-coverage audit + rule self-audits
                         ├─[blocker] back to worker pool
                         └─[pass]
                              └─→ documenter pool (max=2)  feature doc + push
                                    └─→ finalizer pool (max=2)  PR refresh + auto-merge gate
                                          └─→ closed
```

Total max concurrent sessions per rig: 15. Bead handoffs use `metadata.gc.routed_to` only — never `--assignee` — because the supervisor's default scale-check filters `--unassigned`. The pack's `comparison/v2.0a-stall-record.json` records the regression that motivated this convention.

## Authoring stories

The chain consumes one input: a story bead. Whoever authors that bead determines what the chain produces. This section covers the two paths a human engineer takes to get a story into the chain. The Story-graph bridge section below documents the bridge tool's full subcommand surface for reference; this section is the workflow.

### The story-frontmatter contract

A story is a markdown file with YAML frontmatter, conventionally at `stories/<PREFIX>-<NNN>-<slug>.md` where `<PREFIX>` matches the rig's bd issue prefix. The frontmatter declares the contract; the markdown body provides context.

The four fields that determine chain behavior:

| Field | Purpose |
|---|---|
| **Acceptance criteria** | Testable list of "done." The tester pool checks each item; the reviewer pool checks the diff against this list. Vague criteria produce a wandering chain. |
| **Scope** | Names what's *in* and what's *out*. The reviewer flags scope drift as a finding; the finalizer parks the PR if the diff exceeds declared scope. |
| **Sensitive files** | Paths the worker must touch carefully (or not at all). The architectural-signals script consumes this; the glance rubric routes touches here to higher-review tiers. |
| **Dependencies** | Story IDs that must merge first. The bridge tool builds a `bd graph` so the supervisor only releases stories whose deps are satisfied. |

A typical body adds: outcome (one paragraph, plain English), notes (why this approach), out-of-scope (what's deliberately deferred). The full frontmatter schema and the lifecycle states (`draft` / `ready` / `filed` / `in-flight` / `merged` / `closed`) are documented in the auto-loaded rule at `overlay/per-provider/claude/.claude/rules/stories.md`.

### Path 1: interactive co-authoring with Claude Code

Recommended when the story is nontrivial or the engineer wants a second pair of eyes on the spec before the chain spends tokens on it. Claude reads the rig's existing context — other stories, the build plan, the code shape — and drafts the spec in conversation.

```bash
cd <rig>
claude
```

In the interactive session, describe the work. Useful prompt shapes:

- *"I want to add X to Y. Read `stories/` to see the format and numbering, then draft a spec under `stories/`."*
- *"Read `docs/build-plan.md` item #N. Draft the story that lands that item."*
- *"Here's a reviewer's tech-debt trailer from PR #M; turn it into a story spec."*

Claude reads adjacent stories for naming and numbering convention, reads the build plan or domain docs for fit, and drafts `stories/<PREFIX>-<NNN>-<slug>.md` with frontmatter and body. The engineer reviews the draft — *this review is the load-bearing step*, since the chain will execute whatever the spec says. When the draft is right, file and kick off:

```bash
# Either ask Claude to file it, or do it yourself:
bash commands/stories/run.sh file <PREFIX>-<NNN>-<slug>
# The bridge prints the assigned bead ID. Kick the chain off:
bash commands/kickoff/run.sh <bead-id>
```

### Path 2: manual authoring

Use when the shape of the work is already clear and Claude's help drafting isn't worth the round trip. Two sub-paths.

**Hand-write the markdown.** Pre-author the spec at `stories/<PREFIX>-<NNN>-<slug>.md` directly. Validate the frontmatter with the bridge tool, then bulk-file:

```bash
cd <rig>
bash commands/stories/run.sh validate
bash commands/stories/run.sh file
bash commands/kickoff/run.sh <bead-id-from-stdout>
```

The bridge writes `filed_as_bead: <id>` back into each story's frontmatter and flips `status: ready → status: filed`. Bulk-file is the right shape when authoring a phase of related stories at once (e.g., a dependency graph for a whole milestone).

**Editor-prompted scaffold.** When the engineer wants a prefilled template without writing the markdown by hand:

```bash
cd <rig>
bash commands/story-new/run.sh "<title>"
```

Opens `$EDITOR` on a template with the Outcome / Acceptance / Scope / Sensitive / Notes structure. After save, prompts for `open_pr` and `base_branch` defaults, runs `bd create`, echoes the new bead ID. Kick off with `commands/kickoff/run.sh`. Best for one-off stories where the rest of the dependency graph already exists.

### What makes a tight story

A heuristic for self-review before kickoff:

- **Acceptance criteria are testable.** Each item is something the tester pool can verify with a command or the reviewer can verify by reading the diff. *"Improve performance"* is not testable; *"p50 latency under 50 ms on the benchmark in `tests/perf/`"* is.
- **Scope says what's out.** The most expensive chain runs wander into adjacent code because the spec did not say "leave X alone." If the spec declares sensitive files, it should also list adjacent files explicitly *not* being touched.
- **Sensitive files are accurate.** Under-declared sensitive files defeat the architectural-signals gate; over-declared sensitive files spam the reviewer with false positives. The list should be the actual blast radius of the work.
- **Dependencies are minimal.** Long dep chains stall on whatever upstream is slowest. Split when possible; explicitly justify when not.

A spec passing this rubric typically produces a clean glance-merge chain. A spec failing it produces wandering, rework, or `human_required` PRs that absorb the chain's speed back into review time — the exact failure mode the architecture exists to prevent.

## What's in this pack

```text
sdlc-discipline/
├── pack.toml                          schema 2; zero [[named_session]] declarations
├── agents/                            convention-discovered agent definitions
│   ├── worker/
│   ├── tester/
│   ├── reviewer/
│   ├── documenter/
│   └── finalizer/
│       ├── agent.toml                 pool config (pre_start, max_active_sessions, idle_timeout)
│       └── prompt.template.md         the agent's persistent identity
├── formulas/                          *.toml formula definitions
│   ├── mol-sdlc.toml                  kickoff (routes story to worker pool)
│   └── mol-sdlc-work.toml             worker walks load → plan → workspace → implement → self-audit → submit
├── orders/                            *.toml event/scheduled tasks
│   └── sdlc-cost-rollup.toml          observer order on bead.closed → cost_history.csv
├── commands/                          operator-facing CLIs (commands/<id>/run.sh)
│   ├── kickoff/run.sh                 non-LLM chain initiation (1s vs ~60-120s LLM kickoff)
│   ├── watch/run.sh                   colorized one-line-per-phase monitor
│   ├── story-new/run.sh               interactive story scaffold
│   ├── cost-{story,session,stories}/  per-story / per-window / cross-story queries
│   └── demo/run.sh                    four-pane tmux layout
├── overlay/                           pack→agent file injection (Gas City overlay mechanism)
│   └── per-provider/claude/.claude/
│       ├── rules/*.md                 the 9 discipline rules (canonical home)
│       └── settings.json              portable hooks + permissions
├── assets/                            opaque pack-owned files (NOT convention-discovered)
│   ├── scripts/
│   │   ├── worktree-setup.sh          pre_start hook for all five pools
│   │   ├── sdlc-cost-rollup.sh        invoked by orders/sdlc-cost-rollup.toml
│   │   └── sdlc-glance-rubric.sh      invoked by agents/finalizer/prompt.template.md
│   ├── docs/                          principal-engineer guides (DDD, GOOS, modularity, refactoring)
│   └── comparison/                    v1.3-baseline + v2.0a-stall-record + chain-run results
├── pack.toml                          metadata, agent_defaults
└── README.md
```

The pack uses Gas City's `overlay/` mechanism to inject `.claude/rules/*.md` and `.claude/settings.json` into each chain agent's working directory at session-spawn time. The discipline rules live in **one place**: `overlay/per-provider/claude/.claude/rules/`. Rigs do not need to track their own copies; they consume the pack's rules via overlay. Rigs that *want* to override a specific rule can ship a same-named file in their own `.claude/rules/` — the workspace-setup propagation step preserves rig-tracked files (rig wins on filename collision). See *How rules reach the agent* below.

The non-worker pools (tester, reviewer, documenter, finalizer) drive single-conversation workflows from their prompt templates. Only the worker walks a multi-step formula because its work has structurally distinct phases inside one session.

## Prerequisites and installation

The pack runs on top of Gas City (the orchestration runtime) against a Python project (the rig). Cold-start install covers four pieces: system tools, Gas City itself, a Gas City workspace, and the pack import.

### 1. System tools

```bash
# Debian/Ubuntu
sudo apt-get install -y git jq tmux build-essential

# macOS (with Homebrew)
brew install git jq tmux
```

A Go toolchain (1.22+) is needed if installing Gas City from source. `uv` is needed at the rig level for Python dependency management.

### 2. Install Gas City

Gas City ships as a single Go binary, `gc`. Install from source:

```bash
go install github.com/gastownhall/gascity/cmd/gc@latest
```

Or clone and build:

```bash
git clone https://github.com/gastownhall/gascity.git
cd gascity
go build -o ~/.local/bin/gc ./cmd/gc
```

Verify the install:

```bash
gc version    # should print 1.1.1 or newer
gc doctor     # checks system tools, config layout, pack family
```

The doctor command surfaces missing tools (jq, tmux, git) and any config drift; treat its warnings as actionable. See `https://github.com/gastownhall/gascity` for current release notes and platform-specific install variations.

### 3. Initialize a Gas City workspace

A workspace ("city") holds one or more rigs (project directories) plus a shared runtime under `.gc/`. Create one:

```bash
gc init ~/my-city --provider claude
cd ~/my-city
```

`gc init` writes a minimal `city.toml`, a top-level `pack.toml`, the `.gc/` runtime directory, and the standard prompt-template skeletons. Rerun with `--preserve-existing` if bootstrapping over a committed workspace.

### 4. Register the rig (project)

Point the workspace at the project directory that the SDLC chain will operate against. The rig must already be a git repository with a Python toolchain (`pyproject.toml` + `uv`-managed `.venv`).

```bash
gc rig add /path/to/my-project --name my-project
```

`gc rig add` initializes the rig's bead store under `.beads/`, installs agent hooks, and appends a `[[rigs]]` block to the workspace's `city.toml`.

### 5. Install the pack

Two ways to add the pack to the rig.

**Via `gc import add` (recommended).** Pulls the pack from a git remote, caches it under `.gc/`, and writes the import declaration:

```bash
cd ~/my-city
gc import add github.com/<owner>/sdlc-discipline-pack --name sdlc-discipline
```

For a private repo, the runner's git config needs credentials that can clone it (a personal access token via `gh auth login`, an SSH key, or a credential helper).

**Via direct city.toml edit.** Useful for a local-checkout development loop:

```toml
[[rigs]]
name = "my-project"

[rigs.imports]
[rigs.imports.sdlc-discipline]
source = "/path/to/sdlc-discipline-pack"   # or "github.com/<owner>/sdlc-discipline-pack"

# Operating mode (see "Three operating modes" below). The env vars apply
# to the finalizer pool, which owns the merge gate.
[[rigs.patches]]
agent = "finalizer"
[rigs.patches.env]
SDLC_OPEN_PR_DEFAULT = "true"
SDLC_GLANCE_MERGE_DEFAULT = "false"
```

### 6. Reload and verify

```bash
gc reload
gc config explain | grep -E '^Agent:.*sdlc-discipline'
```

The reload picks up the new import. `gc config explain` should list five agents under the rig — worker, tester, reviewer, documenter, and finalizer — each pointing at the pack's prompt templates and configured with the per-pool `max_active_sessions` ceilings (5/3/3/2/2).

### 7. Start the supervisor (first time)

```bash
gc start
```

Brings the city up under the machine-wide supervisor. After this point, beads routed to a pool's template name will spawn fresh sessions on demand and de-scale on idle.

For *ongoing* supervisor restarts (after pulling pack updates, rebuilding `gc`, recovering from a crash, or as part of release deployment), prefer the pack-shipped wrapper script over a bare `gc supervisor start`. See [Supervisor startup wrapper (v2.18.0)](#supervisor-startup-wrapper-v2180). The wrapper resolves PATH consistently across interactive shells, non-interactive ssh invocations, and (future) systemd-managed deployments.

## How rules reach the agent

The pack ships discipline rules and settings via Gas City's `overlay/` mechanism. At session-spawn time, the supervisor materializes `overlay/per-provider/<provider>/...` content into the agent's working directory, then starts the chain agent there. For Claude-Code agents this means `.claude/rules/*.md` and `.claude/settings.json` land at the agent's pre-spawn cwd, where Claude Code's auto-load picks them up.

The worker formula's `workspace-setup` step then creates a per-bead worktree for the actual story work and `cd`s into it. That step propagates the overlay-materialized `.claude/` into the per-bead worktree, conditional on filename absence: only files the rig doesn't already track are populated from the overlay. A rig can override any specific rule by tracking a same-named file in its own `.claude/rules/` — the rig wins, the pack supplies the rest.

This means:

- **Fresh rigs** (a brand-new Python project, an OCaml project being onboarded, a pilot): no `.claude/rules/` content of their own. The pack supplies all 9 rules + `settings.json` via overlay. No tarball, no operator step, no manual extraction.
- **Established rigs** that ship rig-specific rules (e.g., a rig with a project-specific `python.md` style guide): the rig's tracked file wins; the pack fills in the rest.
- **Rule evolution**: the canonical rules live in *one* place — this pack's `overlay/per-provider/claude/.claude/rules/`. Update them here, ship a new pack version, and every rig picks up the change on its next chain run. Rigs don't carry their own copies that drift.

The other agents (tester, reviewer, documenter, finalizer) work directly in their pool-instance work_dirs — the same place the overlay materializes — so they see the rules without needing the propagation step. Only the worker creates a separate per-bead worktree (for crash-recovery resumability) and therefore needs the propagation logic.

The same overlay-and-propagation mechanism also delivers `.claude/sdlc-discipline/sdlc-gate.py` (the differential-gate helper) and `.claude/sdlc-discipline/guides/*.md` (the principal-engineer guides) into every chain-agent worktree. The gate helper is referenced by the worker's `self-audit` step and the tester prompt; the guides are referenced by the tactical rules (`tdd.md`, `ddd.md`, `modularity.md`, `refactoring.md`) as the long-form rationale to consult when the rule's tactical guidance isn't enough.

## Principal-engineer guides (v2.5)

Four long-form guides ship with the pack at `overlay/per-provider/claude/.claude/sdlc-discipline/guides/`:

| Guide | Subject | Tactical rule that points at it |
| ---- | ---- | ---- |
| `goos-guide.md` | Freeman & Pryce TDD discipline | `.claude/rules/tdd.md` |
| `ddd-guide.md` | Evans-grounded domain-driven design | `.claude/rules/ddd.md` |
| `modularity-guide.md` | Liskov-grounded module design | `.claude/rules/modularity.md` |
| `refactoring-guide.md` | Fowler-grounded refactoring discipline | `.claude/rules/refactoring.md` |

The tactical rules are short (~150 lines each) and prescribe what to do at the edit boundary; the long-form guides explain *why* with citations and worked examples. An agent edits a file, the tactical rule fires; if the agent needs more depth than the rule supplies, the rule names the guide and the agent reads it. Both rules and guides materialize into the agent worktree, so the cross-reference is always resolvable from inside the chain.

These guides used to live in Elder's `docs/` directory. They were moved into the pack at v2.5 to make the pack the single source of truth for engineering discipline that travels across rigs.

## Story-graph bridge (v2.6)

Stories are the design-time artifact for the SDLC chain — markdown files with YAML frontmatter that live under `stories/` at the rig root. bd is the runtime substrate. The v2.6 bridge tool translates between them: parsing frontmatter, validating the dependency graph, and bulk-filing stories into bd as beads.

The bridge ships at `overlay/per-provider/claude/.claude/sdlc-discipline/stories.py` (materialized via the standard overlay path, like `sdlc-gate.py` from v2.4) plus a thin human-facing wrapper at `commands/stories/run.sh`. Five subcommands:

| Subcommand | What it does |
| ---- | ---- |
| `validate` | Schema check, dep resolution, sensitive-files consistency, status enum, cycle detection. Returns non-zero on issues. Designed for pre-commit and CI. |
| `file` | Translates stories with `status: ready` (or specific IDs) into a `bd create --graph` JSON plan, runs the command, parses assigned bead IDs from output, writes `filed_as_bead: <id>` back into each story's frontmatter, flips `status: ready → filed`. |
| `ready` | Wrapper over `bd ready` that joins bd's output back to story-file paths. |
| `archive` | Moves a closed story's file into `stories/_archive/` with a closing note (PR URL, merged SHA, completion date appended to frontmatter). |
| `graph` | Wrapper over `bd graph --html --all`; writes the interactive dependency-graph HTML to a temp path. |

Auto-loaded rule at `overlay/per-provider/claude/.claude/rules/stories.md` documents the frontmatter schema, file naming convention (`<PREFIX>-<NNN>-<slug>.md` where prefix is the bd issue-prefix uppercased), and the lifecycle states (`draft` / `ready` / `filed` / `in-flight` / `merged` / `closed`). The rule loads when editing any file under `stories/**`.

Stdlib-only Python (no `pyyaml` dependency). YAML frontmatter is hand-parsed for the simple shape stories use: scalar keys, list-of-strings values, no anchors or flow style.

Stories complement the existing `commands/story-new/` (interactive single-story scaffold creates a bead directly). The bridge handles bulk filing from a pre-authored `stories/` directory; `story-new` handles one-off interactive creation when you don't want a markdown source file. Use whichever fits the moment.

## Differential gates (v2.4)

The pack does not enforce a zero-error static-analysis ceiling. It enforces **anti-weakening**: the worker's branch must not introduce ruff or mypy errors, must not introduce suppression directives (`# type: ignore`, `# noqa`, `# pyright: ignore`), must not add `pytest.mark.skip`/`xfail`/`skipif` markers, and must not lose assertion counts in pre-existing test files. Pre-existing baseline noise is tolerated; weakening the branch to silence new failures is not.

Mechanism:

1. **`capture-baseline` formula step** runs after `workspace-setup`, before `implement`. It computes `git merge-base HEAD origin/$TARGET`, checks out that SHA in a scratch worktree, runs ruff/mypy/suppression-scan/pytest-counts, and writes the result to `$RIG_ROOT/.gc/cache/baselines/$BASELINE_SHA/`. The cache is keyed by SHA so concurrent stories sharing a merge-base share a baseline. The bead records `gate.baseline_sha` for downstream phases to read.

2. **Worker self-audit Gate 1** runs `python3 .claude/sdlc-discipline/sdlc-gate.py diff --baseline-dir $CACHE_DIR` against the captured baseline. Verdict shape: `pass` (clean), `advisory` (soft signals — cross-file relocations whose global-(code) net is non-positive, test-file deletions), or `fail` (introduced errors, suppressions, skip markers, or lost asserts). `fail` blocks the handoff; `advisory` proceeds with metadata for the reviewer.

3. **Tester** re-runs the same diff in fresh-context. Pytest must pass on its own. The gate verdict drives routing: `pass` or `advisory` → reviewer; `fail` → bounce to worker (or escalate if the failure traces to environment / baseline corruption rather than worker error).

Identity model and known limits live in `overlay/per-provider/claude/.claude/sdlc-discipline/sdlc-gate.py`'s docstring. Within-`(file, code)` swaps are a known v2.4 gap (would require AST-anchored identity); fixed in v2.5 if the false-negative rate warrants it.

## Three operating modes

Two env vars, set per-rig in `city.toml` via a patch on the finalizer agent, drive the three common scenarios.

### Mode A — Solo, no PR

Branch is pushed; story closes on success without a PR.

```toml
[[rigs.patches]]
agent = "finalizer"
[rigs.patches.env]
SDLC_OPEN_PR_DEFAULT = "false"
```

### Mode B — Team, PR for human review

Branch is pushed, PR is opened with a story-referencing body, glance-merge is off so a human approves the merge.

```toml
[[rigs.patches]]
agent = "finalizer"
[rigs.patches.env]
SDLC_OPEN_PR_DEFAULT = "true"
SDLC_GLANCE_MERGE_DEFAULT = "false"
```

### Mode C — Solo, PR + recommendation-driven merge

Branch is pushed, PR is opened, the glance rubric runs, and the finalizer routes the PR by the reviewer's recommendation (v2.10.0):

- `glance_merge` recommendation + rubric pass → merge immediately
- `review_encouraged` + rubric pass → park; the delayed-merge order auto-merges after the configured window (default 24h) unless an objection comment is posted
- `human_required` (any signal fired, or rig has no `architecture.toml`) → park indefinitely
- rubric fail (any recommendation) → park

```toml
[[rigs.patches]]
agent = "finalizer"
[rigs.patches.env]
SDLC_OPEN_PR_DEFAULT = "true"
SDLC_GLANCE_MERGE_DEFAULT = "true"
```

The env vars are read by the finalizer pool, which owns the merge gate. Until v1.x, the documenter held this responsibility — that boundary moved in v2.0. The three-tier recommendation logic was added in v2.10.0; pre-v2.10.0 rigs (no `architecture.toml`, no `review_recommendation` metadata) default to `human_required` and park.

## Per-story overrides

The env var is the rig's default. Any story bead can override either toggle via metadata at story creation:

```bash
bd create "csv2json: ..." \
  --description "..." \
  --set-metadata open_pr=true \
  --set-metadata glance_merge=false
```

Useful when a normally-glance-merged rig has a sensitive story that warrants human review, or when a no-PR rig wants visibility on one particular change.

## The glance rubric

Five binary checks (`assets/scripts/sdlc-glance-rubric.sh`):

| ID | Check |
|---|---|
| R1 | `metadata.test_status == "green"` |
| R2 | `metadata.review_verdict == "pass"` |
| R3 | All CI checks on the PR are green (auto-pass when no CI) |
| R5 | No undeclared sensitive-file edits |
| R7 | PR is `mergeable: CLEAN` |

R4 (LOC cap) and R6 (acceptance criteria) were removed in v2.10.0: the architectural-signals script below carries the risk-detection load that LOC was a proxy for, and R2 already encodes whether acceptance criteria were addressed.

## Architectural signals (v2.10.0)

`assets/scripts/sdlc-architectural-signals.py` augments the rubric with AST-driven detection of architectural changes that should never auto-merge regardless of diff size. Six signals (sensitive-file delta, Protocol signature delta, frozen-dataclass field delta, layer crossing, public-name removal, assertion-count regression); any one fires → `recommendation = "human_required"`.

Rigs declare their architectural shape in `.claude/rules/project/architecture.toml`:

```toml
sensitive_files     = ["risk_parameters.py", "agents/risk_agent.py", "indicators/*.py"]
domain_model_files  = ["core/state.py"]
protocol_modules    = ["core/agent.py"]
```

Without the file, the signals script defaults every PR to `human_required` — a rig that hasn't declared its shape can't be auto-merged safely. The full format spec lives in `overlay/per-provider/claude/.claude/rules/architecture-config.md` (auto-loads when the rig edits its `architecture.toml`).

### Delayed-merge tier

The middle recommendation, `review_encouraged`, parks the PR with `final_state=pr_open_for_human` (same terminal state as `human_required`). A periodic order — `orders/sdlc-delayed-merge.toml`, default cooldown 30m — scans those beads and merges each PR after a delay window or in response to a PR-comment override.

Tunables:

| Env var | Default | What it does |
|---|---|---|
| `SDLC_DELAYED_MERGE_ENABLED` | `true` | Master switch. Set `false` to disable the order without removing it. |
| `SDLC_REVIEW_ENCOURAGED_DELAY_HOURS` | `24` | Delay before auto-merging a `review_encouraged` PR with no override comment. |
| `SDLC_DELAYED_MERGE_APPROVE_PATTERN` | `LGTM-AUTO\|MERGE-NOW` | First-token regex for "merge now, bypass the delay." |
| `SDLC_DELAYED_MERGE_OBJECTION_PATTERN` | `NACK\|HOLD\|VETO` | First-token regex for "hold; don't auto-merge." |

Override patterns are matched against the *first non-whitespace token* of each PR comment, so a comment that mentions `NACK` in prose does not fire the objection path. To object or fast-approve, lead with the keyword.

## Tech-debt automation (v2.11)

The reviewer emits a structured `tech_debt_trailer` JSON block at the bottom of the review file when it identifies `[tech-debt]` findings. The finalizer's tech-debt-automation hook (`overlay/per-provider/claude/.claude/sdlc-discipline/tech_debt.py`) reads that trailer after the merge gate and files one GitHub issue per item in the rig's repo, labeled `tech-debt`. Each issue body cites the target file:line, severity, suggested fix, the parent PR, and the source review file.

The hook is opt-in per rig via `architecture.toml`:

```toml
[tech_debt_automation]
enabled = true
```

Default is off — a rig that has not opted in still produces the trailer (the reviewer always emits it) but no issues are filed. This keeps the v2.11 rollout safe: rigs that pull the new pack version see no behavior change until they flip the gate.

Dedup is by exact issue title against existing open `tech-debt`-labeled issues, queried via `gh issue list`. The hook is non-blocking — failures from `gh` are logged to stderr but do not fail the finalizer step; the PR is already merged or parked at that point.

Why GitHub issues rather than chain-runnable beads: tech-debt items vary in actionability, so the capture step (machine-driven, fast) is decoupled from the prosecution step (human-triaged, optional). Patterns observed in the triaged backlog become candidates for future automation rules.

## Operator notification (v2.12)

Closes the operator-poll loop on chain completions. When the finalizer reaches its terminal state, it invokes `assets/scripts/sdlc-finalizer-notify.sh`, which composes a subject + body and pipes them through `assets/scripts/sdlc-notify.sh`. Two opt-in flavors:

```bash
# Required for any notification — recipient address.
export SDLC_NOTIFY_RECIPIENT="operator@example.com"

# Optional — also notify on auto-merged closes (final_state=merged).
# Without this, only human_required PRs (final_state=pr_open_for_human)
# trigger an email.
export SDLC_NOTIFY_ALL_CLOSES=true
```

Both env vars are read by the finalizer pool agent. `SDLC_NOTIFY_RECIPIENT` alone delivers the `human_required` PR alerts (the default behavior — replaces the operator's manual polling loop on PRs parked for review). Adding `SDLC_NOTIFY_ALL_CLOSES=true` extends coverage to auto-merged closes (useful for long-running chains where the operator has stepped away).

`sdlc-notify.sh` sends via local `msmtp` (which carries its own sender config in `~/.msmtprc`); if `msmtp` is unavailable on the host, the helper logs the would-have-sent subject to stderr and exits 0 — a missing notification substrate must never fail a chain. When `SDLC_NOTIFY_RECIPIENT` is unset, the helper short-circuits before invoking `msmtp`.

Subject formats:
- `[<rig>] PR <#> open for review: <story-title>` — flavor 1 (PR parked, `final_state=pr_open_for_human`)
- `[<rig>] PR <#> auto-merged: <story-title>` — flavor 2 (auto-merged, `final_state=merged`)

Body: PR URL, reviewer recommendation tier (`glance_merge` / `review_encouraged` / `human_required`), architectural signals fired, story ID, story title.

Other notification paths (stall detection, order-fire stall detection) ship in follow-up sub-stories of pack #44.

## Claude retry wrapper (v2.12)

The wrapper sits between `gc`'s pool spawn and the `claude` binary. It has two operating modes:

- **Passthrough mode** (`STORY_ID` unset) — `exec claude "$@"` directly, no retry, no metadata writes. Fires on the mayor session, freelance claude sessions, and any pool agent (since `gc` does not currently inject `STORY_ID` into the spawn env).
- **Active mode** (`STORY_ID` set) — runs `claude` as a subprocess; on exit, delegates a retry-or-exit decision to `claude_retry.py`. On stall, re-spawns via `claude --resume <session-id>` with a continuation prompt and a per-cause sleep schedule. Writes `<template>.attempt_n`, `<template>.last_exit_cause`, and `<template>.state` to the bead per attempt for operator audit via `bd show <bead>`.

**Operational state in v2.12.0**: every spawn fires in passthrough mode. The retry loop is code-present but dormant — gc's spawn env does not include `STORY_ID`, and pool agents set it themselves only after claiming a bead inside their claude sessions (too late for the wrapper). The supervisor's zombie-detect-and-recreate path (`internal/runtime/tmux/adapter.go:920-937`) handles chain stalls today by respawning sessions with the same name, which restores the bead's `assignee` binding. The decision to leave retry dormant lives in issue #63 — reopen it when operational data (529-storm frequency, idempotence of per-turn-cap stalls, supervisor-respawn cost per stall) justifies the redesign.

**What this still gives you**: the passthrough guard prevents the chain-breaking failure mode the 2026-05-16 T7920 outage demonstrated, where a global `[providers.claude] command` override broke every claude spawn (mayor included) on the wrapper's `STORY_ID:?` line. The wrapper is safe to opt in to globally; absent `STORY_ID`, it gets out of the way.

### How a rig opts in

Add to the workspace `city.toml`:

```toml
[providers.claude]
base = "builtin:claude"
command = "/path/to/pack-cache/assets/scripts/sdlc-claude-with-retry.sh"
path_check = "claude"
```

`base = "builtin:claude"` inherits gc's built-in claude provider defaults (`ready_delay_ms`, etc.) while the `command` override redirects every claude spawn through the wrapper. `path_check = "claude"` tells gc to verify `claude` itself is installed on `PATH` (the wrapper is a shell script; gc's existence check needs to look for the real binary). Without `path_check`, gc would check for the wrapper's path on every spawn.

The `command` path is the wrapper's location in the pack cache — for path-based imports the cache prefix is set at install time and is stable. Find it via `ls -d <workspace>/.gc/cache/includes/sdlc-discipline-pack-*/assets/scripts/sdlc-claude-with-retry.sh`.

Before flipping the opt-in on a production rig, run `bash assets/scripts/sdlc-smoke-test-claude-wrapper.sh` against the deployed cache. The script stands up a real tmux session with a fake claude binary and asserts the wrapper reaches the readiness prompt in both passthrough and active scenarios. The 2026-05-16 incident's regression-prevention test.

### What's auto-resolved

The wrapper auto-resolves two pack-side env vars so the rig's `city.toml` doesn't have to thread them. Both fire only in active mode (i.e., after `STORY_ID` is in env, which is not the case today):

- `SDLC_TEMPLATE` — derived from `GC_SESSION_NAME` (e.g., `sdlc-discipline.worker-1` → `worker`). gc sets `GC_SESSION_NAME` on every pool agent.
- `CLAUDE_RETRY_PY` — resolved relative to the wrapper's own location.

`STORY_ID` is read from gc's spawn-time env. When unset, the wrapper passthroughs.

### What's configurable

| Env var | Default | Purpose |
| ------- | ------- | ------- |
| `SDLC_MAX_ATTEMPTS` | `5` | Max retry attempts before exit 75 (EX_TEMPFAIL) |
| `SDLC_CLAUDE_SESSION_LOG` | `/dev/null` | Path to claude's session JSONL (drives cause classification). Production sets via the runtime; tests override. |
| `SDLC_RETRY_SLEEP_OVERRIDE` | (unset) | Override per-retry sleep (seconds). Used by tests. |
| `SDLC_NOTIFY_MSMTP` | `msmtp` | Override the msmtp binary path (used by tests to exercise the absent-msmtp fallback). |

### Per-cause retry schedule

| Cause | Schedule (seconds) | Rationale |
| ----- | ------------------ | --------- |
| `turn_cap` (Mode B) | 5, 5, 5, 5, 5 | Per-turn limit; immediate retry is fine |
| `api_529` (Mode A) | 30, 60, 120, 300, 600 | API overload; exponential backoff |
| `api_429` | 60, 60, 60, 60, 60 | Rate limit; conservative |
| `crash` | 60, 60, 60, 60, 60 | Process died abnormally; system room |
| `unknown` | 60, 60, 60, 60, 60 | Conservative default |

## Supervisor startup wrapper (v2.18.0)

`assets/scripts/sdlc-supervisor-start.sh` is the pack-shipped wrapper around `gc supervisor start`. It sources `~/.profile` to inherit the operator's interactive-shell PATH before exec'ing the supervisor, then prepends `~/.local/bin` as belt-and-suspenders so user-installed binaries (`uv`, `bd`, `gh`, and anything else under `~/.local/bin`) are visible to the supervisor and every chain phase it spawns.

### Why it exists

By default, a non-interactive process spawn — a script, a systemd unit, anything invoked outside an interactive shell — inherits a minimal PATH that omits `~/.local/bin`. The supervisor inherits this minimal PATH, every pool agent inherits it from the supervisor, and every chain phase the pool agents spawn inherits it from them. When a phase script reaches for `uv`, `bd`, `gh`, or any user-installed tool, the call fails with `FileNotFoundError`. The chain's signals classifier crashes silently. The reviewer's recommendation tier degrades without anyone noticing.

Today this is latent on hosts where the operator starts the supervisor from an interactive shell — interactive shells inherit `~/.profile`'s PATH, so the supervisor and its children carry `~/.local/bin`. The latent bomb fires the moment the supervisor moves to a systemd-managed deployment (per the release-deployment posture in the host's future): the systemd unit inherits the system's bare PATH, and every chain phase that reaches for a user-installed binary crashes.

The wrapper closes this class problem at the script layer. One PATH-resolution point covers today's operator-invoked startup AND tomorrow's systemd unit (whose `ExecStart=` points at the same script), so any host that switches lifecycle models retains identical PATH semantics.

### Usage

```bash
# Verify env resolution without bouncing the supervisor:
bash /path/to/pack/assets/scripts/sdlc-supervisor-start.sh --check

# Start (or restart) the supervisor through the wrapper:
bash /path/to/pack/assets/scripts/sdlc-supervisor-start.sh
```

In a path-imported pack, the actual location is `<city>/.gc/cache/includes/sdlc-discipline-pack-<hash>/assets/scripts/sdlc-supervisor-start.sh`. Resolve the cache directory via `gc config explain` or `ls <city>/.gc/cache/includes/`.

`--check` mode prints the resolved PATH plus the resolved paths of `gc`, `uv`, `bd`, and `gh`, then exits 0 without invoking the supervisor. Use it as a pre-flight check before a real bounce.

Additional flags are forwarded to `gc supervisor start`.

### Configuration

`SDLC_SUPERVISOR_GC` overrides the gc binary path (default: `gc`, looked up via PATH). Used by tests to substitute a stub binary; production rigs do not need to set it.

### When to use the wrapper vs `gc start`

| Operation | Use |
| --- | --- |
| First-time city setup (install + register) | `gc start` (per [Step 7](#7-start-the-supervisor-first-time)) |
| Restart after a pack upgrade, `gc` rebuild, or supervisor crash | `bash sdlc-supervisor-start.sh` |
| Restart after pulling pack updates that ship new agent configs | `bash sdlc-supervisor-start.sh` |
| Restart from a non-login context (cron, systemd unit, ssh script) | `bash sdlc-supervisor-start.sh` (load-bearing) |
| Verify the supervisor's PATH would be correct without bouncing | `bash sdlc-supervisor-start.sh --check` |

The first-time `gc start` is acceptable from an interactive shell because the interactive shell already carries `~/.profile`'s PATH. The wrapper becomes load-bearing for any restart context that isn't guaranteed to have it.

### Stopping the supervisor

The wrapper covers only `start`. Use `gc supervisor stop` directly to stop. The stop path doesn't need PATH-fix discipline — `gc` is on PATH because the operator just typed it from a login shell.

## Cost tracking

Each pool agent records `<phase>.session_id` and `<phase>.started_at` at start, `<phase>.completed_at` at end, on the story bead's metadata. The `sdlc-cost-rollup` order watches for `bead.closed` events and appends a row to `<city>/cost_history.csv`:

```csv
timestamp,story_id,phase,session_id,duration_seconds,cost_usd,rig
```

Today, `cost_usd` is left as `0` because Gas City does not yet expose per-session token usage in a queryable form. Duration captures the time signal; cost can be filled in later when usage data becomes available, or estimated from duration × per-model rate.

Three query scripts:

```bash
commands/cost-story/run.sh <story_id>          # per-phase breakdown for one story
commands/cost-session/run.sh --since 1h        # time-window summary
commands/cost-stories/run.sh --rig csv2json    # cross-story summary, optionally filtered
```

## Running the chain

After authoring a story, kick off the SDLC pipeline. Two paths.

**Recommended: the kickoff script.** Runs four `bd` commands locally and exits in under a second.

```bash
cd <rig>
bash commands/kickoff/run.sh <bead_id>
```

The script sets `gc.routed_to=<rig>/sdlc-discipline.worker` on the story bead, stamps `sdlc_run_started`, leaves a kickoff note, and exits. The supervisor's pool reconciler spawns a fresh worker on its next tick and the chain proceeds.

**Alternative: the formula-driven kickoff.** Spawns a fresh Claude Code session that runs the same four `bd` commands inside `mol-sdlc.toml`'s kickoff step. Higher latency (~60–120s) and material RAM pressure under concurrent loads (each kickoff is ~350 MB), but creates a wisp molecule bead in the bead store for graph-tracking purposes.

```bash
gc sling <rig>/<provider> mol-sdlc --formula --var story_id=<bead_id>
```

Once the bead is routed, both paths converge: the worker's pool reconciler spawns a fresh worker, which walks the six-step `mol-sdlc-work` formula (load-context, plan, workspace-setup, implement, self-audit, submit-and-exit) and routes the bead to the tester pool. Each subsequent pool runs a single-conversation handoff and routes onward via `gc.routed_to` — never `--assignee`.

## Watching the chain

```bash
bash commands/watch/run.sh <bead_id>
```

Colorized one-line-per-phase monitor. Prints a line on each meaningful state transition; exits when the story closes or after 30 minutes.

For a richer multi-pane view (events stream + bead metadata + watch + artifacts):

```bash
bash commands/demo/run.sh <bead_id>
```

Four-pane tmux layout. Edit the `CITY` and `RIG` paths at the top of the script for your setup, or set `SDLC_DEMO_CITY` / `SDLC_DEMO_RIG` env vars.

## Project assumptions

The agent prompts assume a Python project with:

- a `tests/` directory and `uv run pytest tests/ -v` as the test command
- Click for CLI surface (used in some prompt examples)
- standard `git` workflow (branch + commit + push)

Adapting to a different stack is mostly a prompt-template edit (test command, language conventions). The formulas, scripts, and routing convention are stack-agnostic.

## Versioning

Schema 2. Pack version follows semver:

- **major** — breaking change to env-var names, formula names, agent names, or prompt protocol
- **minor** — new agents, formulas, or scripts; new env vars with safe defaults
- **patch** — bug fixes, prompt clarifications, doc updates

### Version history

Entries list the headline change for each tagged release, newest first.

- **v2.18.2** — docs patch (pack #82). `agents/finalizer/prompt.template.md`'s "Tech-debt automation" section is rephrased so the finalizer LLM no longer constructs its own `architecture.toml` gate check around the script invocation. The 2026-05-18 audit across 15 recent finalizer sessions on T7920 found 1 GATE-BUG (EL-089's session, where the LLM prepended an `if [ -f "architecture.toml" ]` grep that returns 0 because Elder keeps its config at `.claude/rules/project/architecture.toml`; the script invocation was then skipped silently). Frequency ~7%; the silent-failure surface is invisible to the operator because the bash block returns 0. Fix is prose-only: the section now opens with **Run the bash block below verbatim** plus an enumeration of what the script self-gates on (architecture.toml opt-in, trailer presence, per-item dedup), and an inline comment in the bash block explicitly forbids adding extra grep/check logic. No code changes; the script's `is_enabled()` already handles both architecture.toml locations correctly. Full pack suite 248 tests still pass.
- **v2.18.1** — docs patch. README gains a top-of-document Contents (TOC) covering every `##` section (the README crossed 700 lines with v2.18.0 — a TOC was overdue), and a new "Supervisor startup wrapper (v2.18.0)" section documents the `sdlc-supervisor-start.sh` invocation pattern that v2.18.0 introduced. Step 7 of installation now points at the wrapper for *ongoing* supervisor restarts (vs the one-time `gc start` first-time city bringup). A "when to use the wrapper vs `gc start`" table makes the boundary explicit. No code changes.
- **v2.18.0** — one commit accumulated on `main` since v2.17.1. Tagged 2026-05-18. Scope unit: close pack #81's class problem at the script layer. `assets/scripts/sdlc-supervisor-start.sh` is a pack-shipped wrapper that the operator runs instead of `gc supervisor start`. The wrapper sources `~/.profile` so the supervisor inherits the operator's interactive-shell PATH (where `uv`, `bd`, `gh` and other user-installed binaries live under `~/.local/bin`), prepends `~/.local/bin` as belt-and-suspenders for hosts whose .profile doesn't add it, then `exec`s gc. One PATH-resolution point works identically for today's operator-invoked startup and tomorrow's systemd-managed deployment (per EL-100 / future release-deployment work) — the systemd unit's `ExecStart=` will point at this same script. The bomb pack #81 surfaced was that any non-interactive supervisor invocation would inherit a bare PATH and crash `signal_d_layer_crossing()` with FileNotFoundError on `uv`; the wrapper closes the bomb for all current and future binaries the pack reaches for, not just `uv`. `--check` mode prints resolved PATH plus tool paths without invoking gc, so operators verify env resolution before bouncing the supervisor. Eleven unittest cases drive the script via subprocess against synthetic HOMEs and stub gc binaries covering --check, .profile sourcing, ~/.local/bin belt-and-suspenders, gc-not-found error path, and exec handoff. Full pack suite 248 tests.
- **v2.17.1** — patch. `tech_debt_autofix.slug_from_summary` was stripping identifier punctuation (`_`, `.`, parens, commas) instead of treating it as word breaks, so `_stdin_prompt parses int(raw_choice)` became slug `stdinprompt-parses-intrawchoice` and truncation could land mid-word at `instead-of-model` (cutting off `model_dump`). Surfaced 2026-05-18 evening during sub-C dry-run smoke against open Elder tech-debt issues #287 and #246. Fix replaces every non-alnum non-dash char with a single space (uniform word-break treatment), then truncates at the last `-` within the limit when one exists in the back half of the slug. Three new unittest cases (underscore breaks, dot breaks, parens breaks); one existing case rewritten for the new uniform rule. Full autofix suite 33 tests pass.
- **v2.17.0** — two commits accumulated on `main` since v2.16.0. Tagged 2026-05-18. Scope unit: harden chain-recovery — operator gets richer alerts when a stall fires (Mode A/B verdict attached to the email) and a mechanical tool for the manual checkpoint commit (no more accidental `.claude/settings.json` reshapes riding through to PR review). Together these close the disambiguation half of Brooklyn-autonomy gap #3 (recovery-escalation) and the load-bearing failure mode in pack #79.
  - Commit `22a2e7c` — wire `sdlc-mode-classify.sh` into `sdlc-stall-detector.py`'s notification path. When a stall is detected, the script now locates the Claude Code session JSONL for the bead (under `~/.claude/projects/<project-key>/`, project-key normalized per v2.13.1's slash/dot/underscore rule), runs the classifier against it, and appends `Mode A` / `Mode B` / `uncertain` plus the matching recovery hint to the alert email body. Three states handled explicitly: classified (verdict + reason + recovery + session path), session located but classifier failed (manual command recipe attached), session not located (auto-classification unavailable note). Two new CLI args: `--classify-bin` (default sibling script or `$SDLC_MODE_CLASSIFY_BIN`) and `--rig-root` (default `$GC_RIG_ROOT` or cwd). Thirteen new unittest cases covering project-key normalization (2), session location including most-recent-by-mtime pick (4), classifier subprocess wrapper (3, with bash stubs), body augmentation across all three states (4), and per-verdict recovery hints (3). Disambiguation gap closed.
  - Commit `c53402e` (pack #79) — `assets/scripts/sdlc-stall-recover.sh` (165 lines bash). Operator-invokable replacement for the manual `git add -A && git commit -m "chore(stall-recovery): wip..."` pattern. Stages everything except a default exclusion list of permission-config files (`.claude/settings.json`, `.claude/settings.local.json`, `.claude/rules/project/architecture.toml`, `.claude/rules/project/sensitive-files.md`), then commits authored as `SDLC Recovery <sdlc-recovery@example.com>` so chain-takeover provenance shows in `git log`. Extend exclusions via `SDLC_STALL_RECOVERY_EXCLUDES` (colon-separated). CLI args: `--phase` (required), `--bead-id`, `--note`, `--dry-run`. Eleven unittest cases driving the script against real git repos in tmpdirs (no mocks) including the canonical pack #79 case — `.claude/settings.json` drift + legitimate `core.py` change yields a commit whose changed-file set contains `core.py` but not `.claude/settings.json`, while the body transparently notes which excluded files had pending changes. The investigation finding worth keeping in pack history: there is no "stall-recovery script" in the pack or in gas city's Go source; the EL-033 chain's three smoking-gun commits had `Reginald Perry` as committer with the `SDLC Recovery` author identity set per-commit via `git -c user.name=...`. Pack #79's premise ("find the script and add exclusion logic") was structurally wrong — the fix is giving the operator a mechanical tool, not patching automation that doesn't exist. Closes #79.
- **v2.16.0** — one commit accumulated on `main` since v2.15.0. Tagged 2026-05-18. Scope unit: close pack #32 sub-C — the consumer side of the routing labels v2.15.0 shipped. With sub-A wiring producing labeled issues and sub-C reading them, the autofix-safe slice of tech-debt now flows from finalizer-filed issue to operator-slingable story spec on one cron tick. Sub-B (LLM fallback for `defer-to-llm` items) remains unbuilt and is the next narrowing of the operator-attention surface.
  - Commit `448d01a` — `overlay/.../sdlc-discipline/tech_debt_autofix.py` plus `assets/scripts/tests/test_tech_debt_autofix.py`. Reads open `tech-debt:autofix-safe` issues via `gh`, parses each one's structured body (the shape `tech_debt.build_issue_body` writes), allocates the next-free `EL-NNN` from the rig's `stories/` directory, renders a story spec at `status: ready`, writes it to `stories/EL-NNN-<slug>.md`, and comments back on the issue with an idempotency marker (`<!-- tech-debt-autofix-spawned story=EL-NNN -->`) so re-runs are no-op. Does NOT auto-sling — the operator reviews the generated spec, then slings manually via `gc bd file` + `gc bd kickoff`. Three CLI flags: `--rig-root <path>` (required), `--dry-run` (print specs without writing), `--issue N` (restrict to one issue), `--limit N` (cap batch size, default 10). Stdlib-only; gh subprocess-injected. 29 unittest cases (body parsing across well-formed and three malformed paths, slug + title-prefix helpers, story-id allocation including within-batch collision avoidance, marker detection, spec rendering, and the spawn command end-to-end across 8 cases — dry-run, write-mode, already-spawned skip, partial-body skip, multi-issue id-advance, single-issue mode, no-issues exit, missing-stories-dir error). Real-world smoke validated parse + render against issue #288's body in `reggieperry/elder_trading_system`. Full pack suite 206/206.
- **v2.15.0** — two commits accumulated on `main` since v2.14.0. Tagged 2026-05-18. Scope unit: pick up the cheaper alternatives from the 2026-05-18 Brooklyn Foreman v0 post-mortem. The Foreman's `mode_discrim` and `residue` decision moments — the two clear-win pieces of the seven the v0 design enumerated — get covered without a new pool or LLM call site.
  - Commit `2ec2d6f` — `sdlc-mode-classify.sh` (94 lines bash). Grep-based stall-mode classifier the operator runs during recovery; reads a Claude Code session JSONL, emits `mode_a` (529 storm) / `mode_b` (per-turn-cap exhausted) / `uncertain`. The two modes have distinct mechanical signatures (529 status / overloaded_error frames vs max_turns / tail-tool-use), so a grep classifier is more reliable than reading the JSONL by eye at 11 PM. Conservative thresholds: uncertain is the safe default. Subsumes the Foreman's `mode_discrim` decision moment.
  - Commit `2684294` — wire `tech_debt_classifier.py` (code-orphan since v2.12.0) into `tech_debt.py`'s finalizer-time file path. Every auto-filed tech-debt issue now carries a routing label — `tech-debt:autofix-safe` / `tech-debt:needs-human` / `tech-debt:defer-to-llm` — alongside the base `tech-debt` label, computed by the deterministic-rules classifier with `low/med/high` severity normalization at the boundary. A downstream auto-fix orchestrator (sub-stories B + C of pack #32, still unbuilt) reads the verdict label to pick eligible items. `ensure_label` refactored from per-label `--search` (four calls) to one unfiltered `gh label list` + Python set-membership (one call), matching the v2.12.1 pattern in `issue_exists` to avoid GitHub's punctuation/operator edge cases in label-name search. Eight new ClassifyItemTests + two new CreateIssueVerdictLabelTests pin the boundary; full pack suite passes (177/177). Subsumes the Foreman's `residue` decision moment.
- **v2.14.0** — three commits accumulated on `main` since v2.13.1. Tagged 2026-05-18. Scope unit: promote universal SDLC discipline doctrine from operator memory into the pack's auto-loaded rules layer; close two open issues whose fixes had been hot-patched outside the source tree.
  - Commit `c1df21e` — land 14 generic discipline rules from the Elder operator's 29-memory pack (distilled from PR-review observation plus seven books: Viafore *Robust Python*, Kleppmann *DDIA* Chs 7-9, Ramalho *Fluent Python* Pt V, Ousterhout *APoSD* 2e, Huyen *AI Engineering*, Percival & Gregory *Architecture Patterns*, Fontaine *Art of PostgreSQL*). Universal half lands in the pack; language- and store-specific bits stay as rig project rules per the separation discipline. Six existing rules extended (`code-structure.md` +29, `testing.md` +53, `ddd.md` +23, `security.md` +24, `tdd.md` +9, `writing-style.md` +10), one guide extended (`modularity-guide.md` +98 for the Ousterhout APoSD synthesis), and two new rules added (`concurrency.md` 149 lines — DDIA-shaped catalog of write skew, CAS, idempotency keys, fencing tokens, safety-vs-liveness, defense-selection cheat sheet; `llm-app-patterns.md` 111 lines — Huyen-shaped paired-metric / instruction-hierarchy / output-bound / CoT-latency-budget / sample-sizing / contextual-retrieval discipline). Net +500 lines across 9 files; no rule exceeds 200 lines post-extension. No Python-specific framework names introduced (pre-existing references in `testing.md` / `code-structure.md` queued for cleanup in a separate pass — issue #80).
  - Commit `8a7d3f4` (issue #77) — `sdlc-stall-detector.sh` and `sdlc-order-stall-detector.sh` silently exited 0 when `GC_CITY_ROOT` was unset, claiming success while performing zero work. Three real Mode B stalls during Elder's Phase 1 batch on 2026-05-16/17 went undetected for ~17 hours because the controller treated the `exit 0` as a successful no-op. Three-part fix: walk up from PWD looking for `city.toml`; fall back to `gc cities` for the registered-city lookup; exit 1 (not 0) when no city resolves. The fix was hot-patched on T7920 during EL-091 chain work and rsynced to source + cache per the path-import refresh discipline; this commit promotes it from "patched on the production host" to "committed in the source tree and available to fresh pack installs." Closes #77.
  - Commit `04706a4` (issue #53) — adopt `assets/scripts/sdlc-cost-by-step.py` from orphan-in-working-tree to committed source. The script decomposes a worker session's cost (token usage) by the six mol-sdlc-work formula steps (load-context, plan, workspace-setup, implement, self-audit, submit-and-exit) by binning token usage by `current_step` metadata-transition windows. Foundational for VAL-005's Sonnet-vs-Opus worker-cost research; foundational for any future per-step cost-tracking story. Stdlib-only, 294 lines. Closes #53.
- **v2.13.1** — patch. `project_key()` in the operator-memory snapshot module now normalizes `/`, `.`, AND `_` to `-` when computing Claude Code's auto-memory directory key, not just `/`. v2.13.0 shipped with the single-char-only replacement; Elder's rig directory name `elder_trading_system` produced a key with the underscore preserved, looking at a directory that doesn't exist (Claude Code's actual dir uses `elder-trading-system`). Snapshot wrote an empty file on Elder; the graceful-degradation path handled it operationally but the snapshot's value was dormant rather than delivered. Smoke chain on 2026-05-17 surfaced the gap within minutes of v2.13.0 deploy. Two regression tests added pinning the underscore and dot cases; the test helper `_make_memory_dir` also gets the same normalization to prevent the related `tempfile`-suffix-with-underscore flake source.
- **v2.13.0** — three PRs accumulated on `main` since v2.12.1. Tagged 2026-05-16. Scope unit: minimum cut to mostly-unattended chain operation. The thesis frames the engineer's relocated work as story authorship and walk-away; v2.13.0 closes the operational gaps that prevented walk-away from holding.
  - PR #67 (issue #36 sub-2) — reviewer prompt gains Block H (Security audit). `security.md` already auto-loads on Python edits (sub-1, v2.11.0); Block H makes the per-finding tier classification (blocker / tech-debt / nit) mechanical rather than implicit. For mostly-unattended, security findings have to route to the right tier without human review — blocker parks the PR, tech-debt auto-files via v2.12.1's hook, nit stays quiet. Without Block H, the tier was judgment-based and could drift. Closes #36 (sub-1 + sub-2 both shipped).
  - PR #68 (issue #45) — operator memory snapshot at kickoff. New `overlay/per-provider/claude/.claude/sdlc-discipline/snapshot_operator_memory.py` walks the operator's Claude Code auto-memory directory (`$HOME/.claude/projects/<project-key>/memory/`), filters to entries whose `metadata.type` is in `{project, reference}`, writes a concatenated snapshot to a per-bead context file. The kickoff hook wires it in; worker, reviewer, and documenter prompt templates read the snapshot before processing the story. The finalizer is deliberately not updated — mechanical merge work, no project judgment. Closes the context gap where chains worked from CLAUDE.md + rules + the story spec alone, missing operator-side project decisions, references to external systems, and recent state.
  - PR #69 (issue #44 sub-4 + sub-5) — bead-phase stall detection and order-fire stall detection. Two new cron orders fire on a 15-minute cooldown. `sdlc-stall-detector` walks `bd list --status in_progress` for chain beads, compares each bead's elapsed time in `current_step` against a per-phase SLO (load-context 5m, plan 30m, workspace-setup 5m, implement 120m, self-audit 10m, submit-and-exit 10m, tester 15m, reviewer 20m, documenter 20m, finalizer 15m), and emails the operator via `sdlc-notify.sh` on violations. `sdlc-order-stall-detector` reads `gc order list` + `gc order history` to catch cooldown-trigger orders whose last fire is older than `interval × 2` — the rebase-watcher non-fire from May 2026 is the motivating case. Both throttle at four hours per `(bead, phase)` or per `order_name`. Sub-1/2/3 already covered the success-side signals (human_required PRs, chain completions); sub-4/5 close the *silent-failure* gap. Closes the operational requirement for walk-away: the operator gets pinged when a chain or order silently stops making progress.
- **v2.12.1** — patch. Tech-debt auto-file actually fires now. Two bugs, both shipped (silently) in v2.11.0 alongside the feature itself.
  1. Finalizer prompt path: the bash hook referenced `$RIG_PACK/.claude/sdlc-discipline/tech_debt.py`, but cache-based pack imports lay the file out at `$RIG_PACK/overlay/per-provider/claude/.claude/sdlc-discipline/tech_debt.py` — the `overlay/per-provider/claude/` prefix was missing. `|| true` swallowed the resulting "file not found" silently. Surfaced 2026-05-16 when Elder's `architecture.toml [tech_debt_automation] enabled = true` gate had been flipped but zero tech-debt issues had ever been auto-filed despite multiple chain runs producing valid trailers. Fixed in `agents/finalizer/prompt.template.md`; the silent `|| true` is now an explicit stderr log when the script is missing.
  2. Dedup query: `issue_exists` passed the full title to `gh issue list --search`, but GitHub search-query syntax treats `[`, `]`, `.`, `:`, and em dashes as operators or word boundaries — real tech-debt titles silently returned empty results, defeating dedup. Fixed in `tech_debt.py` by dropping `--search` and exact-match-filtering the full open `tech-debt` set in Python (200-issue cap, well above the realistic ceiling).
  - End-to-end validated: `python3 .../tech_debt.py file --review-file reviews/el-jvoi45u.md ... → "0 filed, 1 dup, 0 invalid"` against an open #237. Two new regression tests in `assets/scripts/tests/test_tech_debt.py::IssueExistsTests` pin both fixes; full pack suite 126/126.
- **v2.12.0** — three PRs accumulated on `main` since v2.11.0. Tagged 2026-05-16. Scope unit: pack #47 wrapper opt-in safe for production rigs after the T7920 incident the same day, plus a code-orphan rules module for the planned classifier orchestrator. Remaining open issues (#32 sub-B/C, #44 sub-4/5, #36 sub-2, #38, #39, #45, #46, #63) carry forward.
  - PR #60 — README opt-in instructions for the pack #47 claude-retry wrapper. Operator-facing setup guidance.
  - PR #61 (issue #32 sub-A) — `overlay/per-provider/claude/.claude/sdlc-discipline/tech_debt_classifier.py` ships the deterministic rules module (`autofix-safe` / `needs-human` / `defer-to-llm` verdicts on tech-debt trailer items). Code-orphan in v2.12.0 — sub-B (LLM fallback) and sub-C (orchestrator) not yet built, so the classifier isn't wired into chain machinery.
  - PR #62 (issue #47 sub-2 + sub-3) — wrapper passthrough guard fixes the 2026-05-16 T7920 outage where the global `[providers.claude] command` override broke every claude spawn (mayor included) on the wrapper's `STORY_ID:?` line. New `assets/scripts/sdlc-smoke-test-claude-wrapper.sh` provides a tmux-based spawn-shape integration test that catches this bug class before any production opt-in. Wrapper retry remains dormant by design (per issue #63's decision) — pool agents pass through to claude directly because `STORY_ID` is not in the spawn env; supervisor zombie-detect-and-recreate handles stalls. The opt-in is operationally safe; the retry feature is deferred until empirical data justifies the redesign.
- **v2.11.0** — six PRs accumulated on `main` since v2.10.0. Tagged 2026-05-16; remaining open issues (#36 sub-2, #38, #39, #45, #46) moved to v2.12+. The 2026-05-16 release of accumulated v2.11 scope.
  - PR #37 (issue #34) — rebase-watcher `CONFLICTING` handling and sweeper rig-enumeration fix; the v2.7.0 watcher's missed-fire under the observed concurrent-conflict pattern closes.
  - PR #40 (issue #35) — `NEXT` sentinel for numbered-catalog ID assignment in worker output. Worker substitutes the next free integer at plan time; removes a class of false-conflict where two parallel chains chose the same catalog ID.
  - PR #41 (issue #32 sub-1) — reviewer emits structured `tech_debt_trailer` JSON at the bottom of `reviews/<bead-id>.md` when findings include `[tech-debt]` items.
  - PR #42 (issue #36 sub-1) — universal `security.md` rule auto-loads on `**/*.py` edits. CWE/OWASP-cited, covers trust boundaries, secrets, databases, Python anti-patterns, cryptography, LLM applications.
  - PR #43 — finalizer PR-body cleanup: drop the `bd-show` line, render Plan/Review/Documentation pointers as clickable links instead of bd CLI invocations.
  - PR #48 (issue #32 sub-2 + sub-3) — finalizer consumes the trailer: `overlay/per-provider/claude/.claude/sdlc-discipline/tech_debt.py` reads the trailer at finalizer time and files one GitHub issue per non-duplicate item. Opt-in per rig via `[tech_debt_automation] enabled = true` in `architecture.toml`. Idempotent `tech-debt` label provisioning; dedup by exact title against open issues.
- **v2.10.0** — architectural-signals merge protocol. New `assets/scripts/sdlc-architectural-signals.py` augments the rubric with AST-driven detection of architectural changes that should never auto-merge (six signals: sensitive-file delta, Protocol signature delta, frozen-dataclass field delta, layer crossing, public-name removal, assertion-count regression). Three-tier reviewer recommendation (`glance_merge` / `review_encouraged` / `human_required`) replaces the binary tier; the middle tier hands off to `orders/sdlc-delayed-merge.toml` (cooldown 30m) which auto-merges after a delay window or on PR-comment overrides (`LGTM-AUTO`, `MERGE-NOW`). Per-phase model selection (Sonnet on tester/documenter/finalizer; Opus on worker/reviewer). Settings-deny audit gate (`sdlc-settings-smoke.sh`) shipped to catch `settings.json` drift before merge.
- **v2.9.6** — patch. Correct `option_defaults` value in pack settings — schema key is `sonnet`, not the model ID. v2.9.5 misnamed the key; v2.9.6 fixes.
- **v2.9.5** — per-phase model selection. Tester, documenter, and finalizer move to Sonnet via `option_defaults`; worker and reviewer stay on Opus. Cost reduction at the expense of slightly less powerful judgment in the lower-stakes phases.
- **v2.9.4** — patch. Cost-rollup observer was silent on `bead.closed` events; four related fixes restore the per-story cost emission to `cost_history.csv`.
- **v2.9.3** — patch. Three bridge / kickoff bugs that surfaced during the VAL-001 burst run. Hardened the story-bridge file path and the kickoff bead-routing.
- **v2.9.2** — patch. Normalize mypy `[import]` and `[import-not-found]` codes in the differential-gate identity model; pre-existing baseline noise on either alias is now stable across regenerations.
- **v2.9.1** — patch. Kickoff routes by registered rig name, not directory basename. Symptom: rigs whose `name` in `gc rig list` differed from their directory basename (Elder's case: `name = "elder"` for directory `elder_trading_system`) saw routing fail because `sdlc-kickoff.sh` resolved the wrong rig.
- **v2.9.0** — bandit security gate. `bandit` joins `ruff` and `mypy` as the third deterministic linter in the differential anti-weakening gate. `_SUPPRESSION_PATTERNS` extended for `# nosec` (with the space-vs-comma quirk documented). Backward-compatible against pre-v2.9 baseline dirs (treats absent `bandit.json` as zero findings).
- **v2.7.7** — patch. Clear `--assignee` on every pool handoff. Symptom: a bead with `--assignee` set was invisible to the pool reconciler's `--unassigned` filter, stalling the chain at every pool-to-pool transition.
- **v2.7.6** — TDD global-pass refactor discipline in the worker formula. Worker pauses for a fresh-context refactor pass once the implementation lands green, before moving to self-audit.
- **v2.7.3** — patch. Clear assignee on closed → open transitions; companion to v2.7.7's broader handoff-time clearing.
- **v2.7.0** — merge-failure recovery loop. Rebase-bounce command (worker re-enters with rebase-iteration mode), finalizer bounce-on-conflict path (PR-comment-driven re-route to worker on `--force-with-lease` rejection), autonomous watcher order firing on `bead.closed` with `final_state=merged`. Closes the multi-PR window where parallel chains would conflict on `main` and stall.
- **v2.6.1** — patch. Fix edge direction in `stories.py file`: bd's "blocks" edge convention is `{from_key: blocked, to_key: blocker}`, not the reverse. Symptom: filing a phase produced a dependency graph where the no-deps root story was reported as depending on every downstream, and downstream stories appeared in the ready set. Caught at first end-to-end exercise on T7920 against Elder's Phase 0 set.
- **v2.6** — story-graph bridge. New `overlay/.../sdlc-discipline/stories.py` translates between markdown story specs in `stories/` and bd beads. Five subcommands (`validate` / `file` / `ready` / `archive` / `graph`) cover the design-time → runtime lifecycle. New auto-loaded rule `overlay/.../rules/stories.md` documents the frontmatter schema and lifecycle states. Human-facing wrapper at `commands/stories/run.sh`. Stdlib-only; no `pyyaml` dep. The existing `commands/story-new/` interactive single-story scaffold remains; v2.6 adds the bulk file-based path that lets a rig author 60+ stories with deps and file them as a graph in one shot.
- **v2.5** — principal-engineer guides relocated into the pack. The four long-form guides (Freeman & Pryce TDD, Evans DDD, Liskov modularity, Fowler refactoring) ship via overlay at `.claude/sdlc-discipline/guides/`. Tactical rules updated to point at the new path. Pack becomes the single source of truth for engineering-discipline reference material across rigs.
- **v2.4** — differential gates. Worker captures a static-analysis baseline at `git merge-base HEAD origin/$TARGET` in a new `capture-baseline` formula step. Worker self-audit and tester both run `sdlc-gate.py diff` against the cached baseline; verdict is `pass` / `advisory` / `fail`. Gates fail only on findings the worker introduced (errors, suppressions, skip markers, lost asserts). Removes the v2.3 failure mode where rigs with pre-existing baseline noise saw every PR blocked at the tester.
- **v2.3** — overlay-mechanism for canonical discipline rules. Pack ships `.claude/rules/*.md` and `.claude/settings.json` via Gas City's `overlay/per-provider/claude/`; tarball machinery removed. Workspace-setup propagates overlay-materialized `.claude/` into the per-bead worktree (rig-tracked files preserved on collision).
- **v2.0** — five-pool polecat architecture, zero named sessions; documenter split into documenter (docs only) + finalizer (PR refresh + auto-merge gate); tester split out of worker for fresh-context test resolution; SDLC_*_DEFAULT env vars moved from documenter to finalizer.
- **v1.3** — five named-session phase agents (planner, implementor, tester, reviewer, documenter) with the gascity#1893 kickoff workaround; portable settings.json shipped in the pack.
- See `comparison/` for v1.3 baseline metrics, the v2.0a interim stall record, and replayable story bodies for cross-version comparison.

Tags that aren't called out individually above: `v2.0.0`, `v2.0.1`, `v2.1.0`, `v2.1.1`, `v2.1.2`, `v2.2.0`, `v2.3.0`, `v2.3.0-rc1`, `v2.4.0`, `v2.5.0` are early-iteration tags whose content folded into the v2.x major-version entries above as that scope was finalized.
