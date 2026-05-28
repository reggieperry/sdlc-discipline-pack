# Story development methodology

A working reference for operators driving stories through the pack's chain. Written for the person who has installed the pack, configured a rig pointed at it, has the Gas City supervisor running, and now needs to know *how to use it*.

The pack ships the enforcement layer — auto-loaded discipline rules, the six-phase chain, the differential gate, the recommendation tiers, the architectural-signals analysis. This document is the operator-side discipline that meets the chain at the boundary: how stories get authored, how priority is set, when to invoke the deep-reasoning agent, how to sling cleanly, and how to keep the system honest as it grows.

## The three groups of work

At the highest level of abstraction, every story belongs to one of three groups:

**Group A — features.** Stories that advance the system being developed toward whatever "complete" means for it. These are the main-line items. They are dep-chained: feature X depends on feature Y, which depends on feature Z. They are phase-anchored: a build plan groups them into milestones that gate the system's progression from prototype to live operation. Group A is where the system's value comes from.

**Group B — corrections and adjustments.** Stories that make adjustments to the main system or its support systems after the underlying feature has shipped. Bug fixes, audit residue, slop-finding fixes, refactors against shipped code, scope-amendment chores. These are not features — they refine existing features. A Group B story always has a Group A predecessor that has merged.

**Group C — pack work.** Pack development itself. Group C lives in the pack's repository, not the rig's, and follows its own workflow. This document is about Groups A and B; Group C is referenced where the boundary matters.

The taxonomy matters because Group A and Group B follow different priority disciplines. Group A is ordered: the dep graph dictates what ships when, and that order is load-bearing. Group B has no inherent order; what gates a Group B story is whether its predecessor feature has merged. Once that's true, the story is slingable. Group B is the natural source of "anytime-idle" work — stories whose predecessors are released, that fit whatever focus window the operator currently has.

## Feature vs. chore

Two story shapes, distinguished by what they do to the system:

- **Feature** — a main-line story advancing the system. Carries a build-plan item number. Defines new behavior. Belongs to Group A.
- **Chore** — a correction or adjustment to an existing feature or part of the system. No build-plan item; instead a chore references the feature it adjusts. Belongs to Group B.

The distinction is not about size. A chore can be substantive (a multi-file refactor against shipped code) and still be a chore. A feature can be a small new behavior and still be a feature. What changes the classification is whether the story extends the system's reach (feature) or refines what is already there (chore).

In commit messages and PR titles, the convention is `feat(<scope>): ...` for features and `chore(<scope>): ...` for chores. The reviewer's recommendation tier and the engine's signal analysis work the same way regardless of shape — but the operator's mental model of where the story lives in the build plan does depend on it.

## Story authoring

Every story is a markdown file under `stories/<STORY-ID>-<slug>.md` with YAML frontmatter and a body. The pack ships a spec template that names the required sections:

- **Frontmatter** — `story_id`, `title`, `phase`, `build_item` (features only), `deps`, `parent`, `labels`, `sensitive_files`, `status`, `filed_as_bead`. Valid `status` values come from `assets/scripts/stories.py:VALID_STATUSES`. The canonical terminal is `closed` via `stories.py archive`; do not invent `shipped` or `done` in-place.
- **Body** — Outcome, Why this matters, Acceptance criteria, Scope (`**In:**` / `**Out:**`), Sensitive files, Notes, Cross-references.

Two pre-authoring checks save real work later:

1. **Grep PR history before drafting.** Run `git log --all --oneline --grep="<topic>"` and `gh pr list --search "<topic>" --state all` before starting a new spec. Zombie specs — stories whose underlying work shipped under a different ID — are surprisingly common. A 30-second history check catches most of them.
2. **Verify status values against the schema.** `stories.py validate` runs the schema check; do not skip it.

The pack's `agents/worker/prompt.template.md` reads the spec when the worker starts. Worker exploration cost is dominated by spec size — looser specs cost more tokens, not fewer, because the worker has to do more interpretive work to figure out scope. Tighter specs with explicit `**In:**` / `**Out:**` lists and unambiguous acceptance criteria save chain cost downstream. The lesson is counterintuitive: investing 15 minutes in a tight spec saves 30–60 minutes of worker token spend.

The reviewer also reads the spec when it cross-checks against the diff. If the spec has a `metadata.source_audit_doc` pointer (set in frontmatter as `source_audit_doc: <path>`), the reviewer enumerates the audit's named identifiers and cross-checks them against the spec's `**In:**` and `**Out:**` lists. This is an opt-in safeguard against scope-narrowing during chain execution.

## Story-decomposition judgment

Some work fits one chain run; some work does not. Stories that are too large bounce on reviewer flags or run past the differential gate's appetite. Stories that are too small fail to amortize the per-chain fixed cost. Three heuristics capture most of the calibration.

**Diff size against reviewer appetite.** A story whose implementation diff is likely to exceed roughly 400-600 lines of change across multiple files is a candidate for decomposition. At that diff size the reviewer is more likely to flag scope sprawl and the slop-reviewer's pattern catalogue runs against more code than it was tuned for. The threshold is not exact; it is the size at which the chain's per-phase audits stop being a uniform pass and start finding things to question. Splitting along natural cleavage lines — value object first, then carrier, then consumer — keeps each diff inside the appetite.

**Sensitive-file count.** A story that touches more than one or two sensitive files is a candidate for decomposition. Each sensitive-file touch carries reviewer cost, and concurrent stories that both touch the same sensitive file create rebase-storm risk that the differential gate's baseline-capture step interacts badly with. Splitting reduces both the per-story reviewer load and the cross-story coordination burden.

**Independent test surfaces.** When a story's acceptance criteria split cleanly into two groups whose tests do not share fixtures, scaffolding, or domain reasoning, the story is two stories waiting to be separated. A property test for a new value object and an integration test for its consumer use different test machinery; the worker's mental model is also different. Splitting along the test boundary lets each chain run optimize for one thinking mode.

A five-stage refactor arc that landed in mid-2026 — a value object, a dormant carrier field, a threading change, a consumer update, and a required-field promotion — illustrates the discipline at the upper bound. Each stage was constructible as a separate chain run with a distinct scope fence; each diff stayed inside reviewer appetite; each had its own test surface. The arc shipped through five chains over a single working session rather than through one large chain that would have routed `human_required` on diff size alone. The dependency line between stages was tight, but the chains themselves ran independently.

## The scope-section discipline

The `**In:**` and `**Out:**` sections are how the spec talks to the chain about scope. The worker reads both before claiming any acceptance criterion; the reviewer cites both when auditing whether the diff stayed within bounds. Writing them well is one of the highest-leverage operator activities in story authoring.

**What belongs in `**In:**`.** Concrete file paths, the behavior changes intended for each path, the test additions that prove the behavior. The list should be explicit and finite — the worker is expected to touch every file named and nothing else. If a change requires creating a new file, the file's path goes in the list, not "a new module for X." Vagueness in `**In:**` produces wandering workers; precision produces convergent ones.

**What belongs in `**Out:**`.** Three categories of work the spec author wants the worker to leave alone. First, cross-cutting refactors that have not been agreed to — opportunistic cleanup the worker might be tempted to fold in. Second, fixture or test cleanup that is tempting but unscheduled — the worker should not rewrite test infrastructure to make their job easier. Third, related-but-separate work that belongs in a different story — adjacent stories under the same parent often share vocabulary, and the scope fence prevents one story from claiming work the next is supposed to do.

The trap to avoid is writing `**Out:**` sections so restrictive that they prevent natural cleanup. A worker who notices a typo in a docstring they are otherwise modifying should fix the typo; a worker who notices an unused import in a file they are restructuring should remove it. The `**Out:**` list should fence off work that is clearly someone else's domain, not work that lives inside the file the worker is already editing.

A good test of an `**Out:**` section: read each bullet and ask "would the chain bounce on this if the worker accidentally did it?" Items where the answer is "yes — this would route human_required or fail review" belong in the list. Items where the answer is "no — this would be fine but unscheduled" can usually be left out; the reviewer's scope check catches them anyway. The list earns its place when it prevents specific failure modes, not when it enumerates everything the story does not do. The pattern that ships best: short `**In:**` (two to five items), short `**Out:**` (three to six items), each item one line.

## The audit pattern

Both the human and the computer drift. In normal operation, the operator drifts away from precise recall of what shipped, what's planned, and what state the queue is in. The computer drifts away from current truth because memory snapshots age. The audit pattern is what aligns everyone back to ground truth.

An audit is operator-triggered: it happens when confusion surfaces. The operator notices that an answer doesn't match expectation, or that a recommendation rests on an identifier whose freshness is unclear, or that a planning memory and the actual spec files disagree. At that moment, the operator pauses and invokes an audit — typically through the deep-reasoning agent — instead of continuing on the suspect basis.

The audit's output is a verdict that re-anchors the conversation: corrected DAG, verified file paths and line numbers, a concrete edit list, or a clear "current state is X, your prior assumption Y is stale." After the audit, the operator and computer share the same ground truth again. Then work resumes.

A new operator should treat the audit pattern as a first-class discipline, not as an emergency response. Schedule audits when:

- A planning document is more than a few days old and you're about to act on its claims.
- A memory cites a specific identifier (file path, function name, story ID, commit SHA) and you're about to recommend an action that depends on it.
- A recommendation crosses several files or repositories, and you can't easily hold all the dependencies in working memory.
- An answer from the assistant feels confident in a way that doesn't match your own gut.

These align with the deep-reasoning agent's self-trigger criteria.

## The deep-reasoning agent

The pack's chain is one piece of leverage; the deep-reasoning agent is another. It is a fresh-context Opus subagent invoked through the `deep-reason` skill (per-user; the skill is operator-installed, not pack-shipped, and lives under the operator's `~/.claude/skills/deep-reason/`).

The agent exists because the chain reviews per-PR, and some questions require a cross-PR view. Audits, architectural verdicts, multi-spec dep checks, and "is this plan still load-bearing?" questions all benefit from a subagent that starts with no conversation history and walks the problem from scratch.

The six triggers the operator's own `~/.claude/CLAUDE.md` codifies:

1. About to draft an ADR, design doc, or multi-story design pack. Pressure-test the model before the second draft.
2. About to make a verdict-shaped, hard-to-reverse commit. Tag push, PR merge, destructive op affecting shared state.
3. Defending a position under operator pressure across more than one message.
4. Question requires synthesizing across five or more files or repositories.
5. About to recommend an action based on a memory citing a specific identifier — story ID, function name, commit SHA, build-plan item number — without having freshly verified the identifier exists and is current.
6. Audit-shape or review-shape question whose answer is a verdict. "Is X done?" "Are these issues real?" "Does this plan hold?" "Is this PR safe to merge?"

Skip the agent for single-file edits, lookups with a known target, tasks doable in one to three tool calls, and pure implementation work. Delegating implementation is fine; delegating *understanding* is the trap.

When the agent returns, the operator surfaces the verdict in two or three sentences and names what changed in the plan as a result. If nothing changed, that is also reportable — the agent confirmed rather than corrected.

## The trust arc — review posture as the system matures

Operator review posture evolves in three stages as the chain proves itself:

**Stage 1 — review-everything.** Early adoption. The pack has shipped but the operator has not yet developed empirical confidence in chain output. Every PR gets read carefully. Tier verdicts are advisory; the operator's read is the gate.

**Stage 2 — glance-merge-by-default.** After the pack has absorbed the discipline literature its rules and guides reference — Liskov / Evans / Freeman & Pryce / Fowler / Ousterhout / Meszaros — chain output quality crosses a threshold. The reviewer's `glance_merge` tier becomes trustworthy for clean-signal diffs. The operator glances rather than reads. `human_required` PRs still get full attention.

**Stage 3 — deep-reason-when-it-matters.** The chain's per-PR review is reliable; the residual risk is cross-PR drift — vocabulary that diverges across already-merged work, scope-narrowing claims that did not surface in any individual review, multi-version narrative honesty. The deep-reasoning agent fills that gap, invoked at the operator's discretion or on the audit triggers above.

New operators start at Stage 1. The migration to Stage 2 is empirical: when you find yourself merging `glance_merge` PRs without reading them and the merges keep working, you've moved. Stage 3 develops alongside Stage 2 as your awareness of drift accumulates.

## Priority setting

Group A is dep-anchored. The build plan groups features into phases; within a phase, the dep graph dictates order. A planning document — call it the unified plan — captures the order explicitly and is the operator's go-to for "what's next?"

Group B is, in pre-production posture, flat. There is no internal priority order because the impact landscape is unknown. Pick whichever Group B story fits the current focus window. Once the system is running paper or live, Group B gains domain-shaped order: capital impact, audit-trail integrity, observability gaps, latency budgets, whichever criteria the operator's domain surfaces. The priority emerges from production exposure, not from a priori ranking.

Within both groups, two structural constraints apply:

- **Sensitive-file rebase storm avoidance.** Two PRs touching the same sensitive file should not be in flight concurrently. The differential gate's baseline-capture step and the finalizer's rebase loop both interact badly with concurrent modifications to the same file. Serialize.
- **Cost band against quota headroom.** Each chain run consumes quota or API tokens against the operator's account. Estimate before slinging using the pack's per-band cost ranges (see `docs/research/` for empirical data if available, or the operator's own observed averages). Check current quota usage before committing substantive work.

The "anytime-idle slot" is not a separate tier — it's the natural mode for any Group A or Group B story whose predecessors are released. The slot fills with whatever fits the operator's current focus window. Small slot, small story; long slot, substantive story.

## Cost-band estimation by story shape

Every chain run consumes API tokens or subscription allowance. Estimating cost before slinging is the difference between burning quota on a story whose budget could have funded three smaller ones and dispatching with confidence that the spend is proportionate. The bands below derive from empirical measurements on a project running the pack's v2.10.x defaults (worker and reviewer on Opus, tester and documenter and finalizer on Sonnet).

**The five bands.** *Trivial* (a docstring fix, a typo, a one-line edit) lands in the $45-60 range across 35-45 minutes. *Moderate-low* (a single property test, a single-function docstring expansion, a one-file additive change) lands at $55-70 across 40-50 minutes. *Moderate-high* (an Extract Function across two or three files, a CLI option with tests, a multi-file behavior change without sensitive-file touches) lands at $75-120 across 50-70 minutes. *Substantive* (a new module of roughly 100-200 lines, a multi-file refactor with restructuring, a sensitive-file touch with non-trivial reviewer load) lands at $135-300 across 75-120 minutes. *Phase-architectural* (replacing a pipeline stage, adding a domain concept, threading a new type through several modules) lands at $220-550 or more across 90-180 minutes.

**The single-chain wall-clock band.** A clean run with no bounces lands in the 45- to 105-minute range for the first three bands. Wall clock that exceeds 2x the band's upper bound is a signal worth investigating — usually a tester or reviewer bounce, occasionally a supervisor stall. 3x or more usually means the story was misclassified at sling time and should have been decomposed.

**Bounces are additive.** A tester-to-worker bounce adds $5-10. A reviewer-to-worker bounce on a blocker finding adds $30-60. A rebase-iteration bounce (finalizer routes the worker back to resolve a merge conflict with main) adds $10-25. One reviewer bounce can double a moderate-low story's cost; budgeting for at least one bounce on substantive stories is realistic.

**Cost shapers.** Worker and reviewer phases account for roughly 90% of chain cost; tester, documenter, and finalizer each sit in the $1-3 range. Reviewer cost correlates with plan complexity — the number of acceptance criteria and the depth of plan structure — not with diff size. Sensitive files raise reviewer load. Property tests and domain-reasoning work raise worker cost. Pure-additive diffs and single-file edits lower it.

**Multi-story budgeting.** When several stories are in flight, the per-story estimates compose linearly for cost and partially in parallel for wall clock. A burst of nine mechanical stories on the case-study pack landed in 95 minutes of wall clock against roughly 9 hours of cumulative chain time. Budget cost against the cumulative sum; budget operator attention against the wall-clock burst.

## Pre-sling verification

Before slinging a story, verify five things:

1. **Declared deps match content-implied deps.** Read the AC; identify what types, functions, or modules it references that another spec produces. If the declared deps disagree, fix the declaration before slinging.
2. **Sensitive files declared accurately.** Cross-check the spec's `sensitive_files:` against the rig's `.claude/rules/project/sensitive-files.md`. Files declared sensitive that aren't on the list are spec-author confusion; files actually edited but not declared are a reviewer-bounce risk.
3. **Predecessors merged.** `gh pr list --search "<predecessor-story-id>" --state merged` confirms.
4. **Rig on main.** Chain agents branch from the rig's current state. Slinging from a feature branch causes downstream confusion.
5. **Working tree clean of unrelated changes.** Uncommitted work in the rig directory does not poison chain worktrees (which clone fresh), but it does poison the operator's own git workflow during sling steps.

A sixth check, conditional: if any of the above feels uncertain, run the audit. The deep-reasoning agent on a dep-graph question takes 5–10 minutes and saves chain runs.

## Sling discipline

Six steps, ordered:

1. **Flip status from `draft` to `ready` in the spec frontmatter.** One-line edit.
2. **Commit on `main` with a rich commit message.** Convention: symptom (what's wrong without this story) + cause (why the gap exists) + fix (what this story does) + validation (predecessors merged, audit verdict if any, dep graph position). Heredoc body for multi-paragraph messages. Co-authorship line if the operator wants to credit a collaborator.
3. **Push to origin.**
4. **On the chain host, pull main.** The chain host runs the Gas City supervisor and the chain agent worktrees.
5. **File the bead.** Run `python3 <pack-cache>/.../sdlc-discipline/stories.py file <STORY-ID>` to create the bead in the issue graph, populate `filed_as_bead:` in the spec frontmatter. Commit the stamp.
6. **Kickoff.** Run the pack's kickoff script with the bead ID. The script sets `metadata.gc.routed_to` to the worker pool, opens the bead, and the supervisor's pool reconciler spawns a worker on its next tick.

Authorization for slings should be narrow. The operator approves a specific sling, not a broad allowlist. A chain run commits quota or money; reversibility is partial; budget context should accompany every sling proposal.

## Chain monitoring posture

Do not poll. The supervisor emits notifications through natural completion mechanisms (background-task notifications, tmux session events, bead status transitions). Polling on top of these creates noise without adding signal. The pack-shipped `sdlc-watch.sh` script streams chain progress for a specific bead if you want continuous visibility for a substantive run.

Trust the supervisor's stall-detection orders (the alive-idle detector, the drain-ack-recover order, the stall-detector, the order-stall-detector, the exhausted-bead-retry order). They cover the common failure modes. If a chain is stuck and none of the detectors has fired, investigate before retrying — the absence of a detector signal is itself information.

When surprises happen, stop and verify before acting. The narrower interpretation of any completion report is usually the correct one. "X is done" mid-pipeline often means *the operator's part* is done, not the full pipeline.

## Post-merge sweep

When a story's PR merges, three things should happen — some automatically, some operator-driven:

- **Archive the spec.** Run `stories.py archive <STORY-ID> --pr "#<n>" --sha <full-sha>` to move the spec to `stories/_archive/` with a closing block (`status: closed`, `closed_at`, `merged_pr`, `merged_sha`). The canonical terminal state.
- **Tech-debt findings ship as GitHub issues.** The reviewer's tech-debt trailer is structured JSON; the finalizer files each item as a GitHub issue post-merge. The operator does not need to do this manually unless the finalizer's gate is disabled.
- **Update the rig's CLAUDE.md when audits surface mismatched claims.** Audits often catch project documentation that has drifted from current reality (file purposes, sensitive-file lists, architectural roles). When this happens, propagate the correction in the same audit-follow-up PR.

## Pointers

- Pack-shipped tactical rules: `.claude/rules/` (in any chain-agent worktree, or in the pack repo at `overlay/per-provider/claude/.claude/rules/`).
- Pack-shipped long-form guides: `.claude/sdlc-discipline/guides/` — Liskov, Evans, GOOS, Fowler, APoSD, Ousterhout, Meszaros source-grounded references.
- Story-filing bridge: `assets/scripts/stories.py` (subcommands: `validate`, `file`, `archive`, `rebase`).
- Kickoff: `commands/kickoff/run.sh`.
- Reviewer's recommendation tiers and the architectural-signals analysis: `assets/scripts/sdlc-architectural-signals.py` and `agents/reviewer/prompt.template.md`.
- Out-of-chain orders: `orders/` — rebase-watcher, stale-PR sweeper, delayed-merge sweeper, the stall detectors, the cost rollup, the zombie reconciler.

Adoption-ready operators have all six anchors in their mental model: the three groups, the feature-vs-chore distinction, the audit pattern, the deep-reasoning agent triggers, the trust arc, and the sling discipline. The chain enforces the rest.
