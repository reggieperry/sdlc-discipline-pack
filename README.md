# sdlc-discipline pack

A Gas City pack that runs an SDLC chain — plan, build, test, review, document, finalize — against any rig with a Click/pytest/Python project shape. Five pool agents, zero named sessions, parallel-by-default. Concurrency is bounded by host CPU/RAM and per-account API rate limits, not by named-session serialization.

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

## Authoring stories

```bash
cd <rig>
bash commands/story-new/run.sh "csv2json: add a --quiet flag"
```

Opens `$EDITOR` on a prefilled markdown template with the entry-point format (Outcome / Acceptance / Scope / Sensitive / Notes). After save, prompts for `open_pr` and `base_branch` (with rig env defaults). Runs `bd create` with the description + metadata; echoes the new bead ID.

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

- **v2.6.1** — patch. Fix edge direction in `stories.py file`: bd's "blocks" edge convention is `{from_key: blocked, to_key: blocker}`, not the reverse. Symptom: filing a phase produced a dependency graph where the no-deps root story was reported as depending on every downstream, and downstream stories appeared in the ready set. Caught at first end-to-end exercise on T7920 against Elder's Phase 0 set.
- **v2.6** — story-graph bridge. New `overlay/.../sdlc-discipline/stories.py` translates between markdown story specs in `stories/` and bd beads. Five subcommands (`validate` / `file` / `ready` / `archive` / `graph`) cover the design-time → runtime lifecycle. New auto-loaded rule `overlay/.../rules/stories.md` documents the frontmatter schema and lifecycle states. Human-facing wrapper at `commands/stories/run.sh`. Stdlib-only; no `pyyaml` dep. The existing `commands/story-new/` interactive single-story scaffold remains; v2.6 adds the bulk file-based path that lets a rig author 60+ stories with deps and file them as a graph in one shot.
- **v2.5** — principal-engineer guides relocated into the pack. The four long-form guides (Freeman & Pryce TDD, Evans DDD, Liskov modularity, Fowler refactoring) ship via overlay at `.claude/sdlc-discipline/guides/`. Tactical rules updated to point at the new path. Pack becomes the single source of truth for engineering-discipline reference material across rigs.
- **v2.4** — differential gates. Worker captures a static-analysis baseline at `git merge-base HEAD origin/$TARGET` in a new `capture-baseline` formula step. Worker self-audit and tester both run `sdlc-gate.py diff` against the cached baseline; verdict is `pass` / `advisory` / `fail`. Gates fail only on findings the worker introduced (errors, suppressions, skip markers, lost asserts). Removes the v2.3 failure mode where rigs with pre-existing baseline noise saw every PR blocked at the tester.
- **v2.3** — overlay-mechanism for canonical discipline rules. Pack ships `.claude/rules/*.md` and `.claude/settings.json` via Gas City's `overlay/per-provider/claude/`; tarball machinery removed. Workspace-setup propagates overlay-materialized `.claude/` into the per-bead worktree (rig-tracked files preserved on collision).
- **v2.0** — five-pool polecat architecture, zero named sessions; documenter split into documenter (docs only) + finalizer (PR refresh + auto-merge gate); tester split out of worker for fresh-context test resolution; SDLC_*_DEFAULT env vars moved from documenter to finalizer.
- **v1.3** — five named-session phase agents (planner, implementor, tester, reviewer, documenter) with the gascity#1893 kickoff workaround; portable settings.json shipped in the pack.
- See `comparison/` for v1.3 baseline metrics, the v2.0a interim stall record, and replayable story bodies for cross-version comparison.
