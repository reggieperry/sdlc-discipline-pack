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
├── assets/                            opaque pack-owned files (NOT convention-discovered)
│   ├── scripts/
│   │   ├── worktree-setup.sh          pre_start hook for all five pools
│   │   ├── sdlc-cost-rollup.sh        invoked by orders/sdlc-cost-rollup.toml
│   │   └── sdlc-glance-rubric.sh      invoked by agents/finalizer/prompt.template.md
│   ├── claude-defaults.tar.gz         drop-in .claude/ baseline for rigs without their own (rules + settings.json + conditional-docs starter)
│   ├── docs/                          principal-engineer guides (DDD, GOOS, modularity, refactoring)
│   └── comparison/                    v1.3-baseline + v2.0a-stall-record + chain-run results
├── pack.toml                          metadata, agent_defaults
└── README.md
```

This pack ships **no top-level `rules/` directory and no top-level `settings.json`**. Per Gas City pack v2 spec, those aren't pack content — `.claude/` is Claude Code's surface, not the pack's. For rigs that don't already have their own `.claude/rules/*.md` and `.claude/settings.json`, we ship a drop-in tarball at `assets/claude-defaults.tar.gz`. For rigs that already have their own (Elder, projects with established conventions), the pack's prompts reference rule names (`python.md`, `tdd.md`, etc.) which Claude Code resolves to whatever the rig has in `.claude/rules/`. See *Bootstrapping a rig's `.claude/`* below.

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

## Bootstrapping a rig's `.claude/`

Per the Gas City pack v2 spec, packs do not write to a rig's `.claude/`. Claude Code (the LLM provider) auto-loads `.claude/rules/*.md` and reads `.claude/settings.json` from whatever the rig has. This pack assumes the rig provides those files.

For rigs that already have established discipline (Elder, projects with their own `.claude/rules/` content), the pack's prompts reference rule names (`python.md`, `tdd.md`, `refactoring.md`, etc.) and Claude Code auto-loads whatever the rig has under those names. Nothing to do — the pack consumes the rig's existing rules.

For rigs that don't have any `.claude/rules/` content yet (a fresh repo, a pilot project, an OCaml project being onboarded), the pack ships a drop-in tarball at `assets/claude-defaults.tar.gz`:

```bash
cd <rig>
tar -xzf <pack-cache>/assets/claude-defaults.tar.gz
git add .claude && git commit -m "chore: bootstrap .claude/ from sdlc-discipline-pack"
```

The tarball lands `.claude/rules/{python,tdd,modularity,refactoring,code-structure,decoupling,testing,writing-style,ddd}.md`, `.claude/settings.json`, and a `.claude/conditional_docs/README.md` starter. After extraction, the rig owns the content — diverge or customize as needed.

The source files for the tarball live under `assets/rules/`, `assets/settings.json`, and `assets/conditional_docs-README.md`. The build script at `assets/build-claude-defaults.sh` regenerates the tarball from those sources whenever the pack's discipline changes.

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

### Mode C — Solo, PR + auto-merge

Branch is pushed, PR is opened, the glance rubric runs, and the finalizer auto-merges if the rubric passes.

```toml
[[rigs.patches]]
agent = "finalizer"
[rigs.patches.env]
SDLC_OPEN_PR_DEFAULT = "true"
SDLC_GLANCE_MERGE_DEFAULT = "true"
```

The env vars are read by the finalizer pool, which owns the merge gate. Until v1.x, the documenter held this responsibility — that boundary moved in v2.0.

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

Seven binary checks (`assets/scripts/sdlc-glance-rubric.sh`):

| ID | Check |
|---|---|
| R1 | `metadata.test_status == "green"` |
| R2 | `metadata.review_verdict == "pass"` |
| R3 | All CI checks on the PR are green (auto-pass when no CI) |
| R4 | Diff size ≤ 200 LOC across ≤ 10 files |
| R5 | No undeclared sensitive-file edits |
| R6 | All acceptance criteria are addressed per the review |
| R7 | PR is `mergeable: CLEAN` |

R4's thresholds are tunable: `SDLC_GLANCE_LOC_MAX` (default 200), `SDLC_GLANCE_FILES_MAX` (default 10).

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

- **v2.0** — five-pool polecat architecture, zero named sessions; documenter split into documenter (docs only) + finalizer (PR refresh + auto-merge gate); tester split out of worker for fresh-context test resolution; SDLC_*_DEFAULT env vars moved from documenter to finalizer.
- **v1.3** — five named-session phase agents (planner, implementor, tester, reviewer, documenter) with the gascity#1893 kickoff workaround; portable settings.json shipped in the pack.
- See `comparison/` for v1.3 baseline metrics, the v2.0a interim stall record, and replayable story bodies for cross-version comparison.
