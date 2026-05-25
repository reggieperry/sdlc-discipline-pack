---
title: "Safe Agentic Engineering at Scale: An Architecture for Trustworthy Multi-Agent Software Development"
author: "Reginald Perry"
date: "May 2026"
documentclass: report
classoption: 11pt
geometry:
  - margin=1in
toc: true
toc-depth: 3
numbersections: false
linkcolor: black
urlcolor: black
fontfamily: lmodern
header-includes: |
  \usepackage{microtype}
  \usepackage{titling}
  \pretitle{\begin{center}\Large\bfseries}
  \posttitle{\end{center}\vskip 0.5em}
  \preauthor{\begin{center}\normalsize}
  \postauthor{\end{center}}
  \predate{\begin{center}\normalsize}
  \postdate{\end{center}\vskip 1em}
---

## Abstract

Large language models (LLMs) have become capable enough at code generation to compete with experienced engineers on well-specified tasks. The bottleneck in agent-driven software development has shifted from agent capability to two related problems: whether the agent's output can be trusted at codebase scale, and whether trust can be maintained as many agents work in parallel. This thesis argues that these are not separate problems and that solving both requires two distinct mechanisms in combination. A discipline layer constrains what agents generate, producing consistency that the agent itself cannot maintain across many independent generations. An orchestration layer coordinates many agents under that uniform discipline, producing scale that no single agent can achieve. Neither layer alone is sufficient for trustworthy agent-driven software development at the scope of building real products. Together they enable what the thesis terms a software factory — a system in which many agents work in parallel under uniform quality enforcement, producing work that meets a defined bar without requiring per-change human review.

The thesis develops this argument through three movements. The first establishes why agents drift without external constraint, drawing on contemporary literature documenting LLM output variability and on theoretical work on software engineering discipline from the past five decades. The second describes what each layer must provide and uses two concrete instances — the SDLC discipline pack and Gas City — to illustrate the requirements. The third examines a real-world case study of these mechanisms applied to building a quantitative trading system, drawing implications for engineering roles, team composition, and company-level product development.

The contribution of this thesis is a framework for analyzing agent-driven software development systems, an empirical anchoring through one substantial case study, and an argument for the engineering practice changes required when adopting this approach.

---

## 1. Introduction

The state of large language model code generation changed markedly between 2023 and 2026. Models that could write isolated functions on benchmark problems in 2023 were, by 2026, writing whole features in real codebases under human direction. Tools like Claude Code, GitHub Copilot, Codex, and Gemini CLI made LLM-driven coding available to professional engineers as a daily practice. The question of whether agents could write working code became less interesting than questions about what to do with that capability.

Two questions emerged as practical bottlenecks. First, whether the agent's output could be trusted enough to ship without per-line human review. Second, whether trust could be maintained when many agents worked in parallel on the same codebase. Both questions matter because building a product — as opposed to completing isolated tasks — requires both trustworthy output and the throughput that only parallelism provides.

The dominant industry response to these questions has been to treat the agent as a fast typist. An engineer directs the agent, the agent produces code, the engineer reviews every change. This approach absorbs the agent's speed into the human review bottleneck. When teams add a second agent, the situation worsens: two streams of output need review, and the codebase begins to drift because two agents in two sessions produce subtly different idioms even when given similar tasks. Structuring agent-driven development this way preserves the human review bottleneck rather than relieving it, and the parallelism problem remains untouched.

The position this thesis develops is that the trust problem and the scale problem are not separable, and that solving them requires two mechanisms in combination. A discipline layer constrains what agents generate, producing the consistency that the agent itself cannot maintain across many independent generations. An orchestration layer coordinates many agents under uniform discipline, producing the scale that no single agent can achieve. Neither layer alone is sufficient. Together they enable what the thesis terms a software factory: a system in which many agents work in parallel under uniform quality enforcement, producing work that meets a defined bar without per-change human review.

Both layers have concrete instantiations available at the time of this writing: the SDLC discipline pack realizes the discipline layer, and Gas City realizes the orchestration layer. Section 6 examines the Elder Trading System — a quantitative trading platform in active development — as a case study exercising both layers; the empirical material reported in this thesis derives from its operation.

The thesis develops in nine sections. Section 2 motivates the work by examining the state of agent-driven development and why current practice fails to scale. Section 3 reviews related work on software engineering discipline, LLM output consistency, and multi-agent software engineering systems. Section 4 introduces the frameworks being analyzed — Claude Code, Gas City, Beads, and the SDLC discipline pack — at sufficient depth for readers without prior familiarity. Section 5 develops the discipline-orchestration architecture as a general framework, explaining what each layer must provide and how the layers compose. Section 6 examines the Elder Trading System case study, presenting empirical results from its adoption of the architecture. Section 7 discusses implications for engineering roles, team composition, and product development at companies. Section 8 acknowledges limits of the analysis and identifies open questions. Section 9 concludes.

The contribution of this thesis is threefold. First, it presents a framework — the discipline-orchestration architecture — for understanding what makes agent-driven software development trustworthy at scale. Second, it provides an empirically anchored case study showing the framework in operation against a real codebase. Third, it identifies the engineering practice changes that adopting this approach requires, with explicit attention to what fails when those changes are skipped.

---

## 2. Motivation and background

### 2.1 The capability shift

The capability of LLM code generation has improved rapidly. By 2026, contemporary models including Claude (Anthropic), GPT-4 and successors (OpenAI), and Gemini (Google) regularly solve programming problems that would have been considered hard for any automated system three years prior. Empirical evaluations using benchmarks such as SWE-bench (Jimenez et al., 2024) and LiveCodeBench have documented this improvement across multiple model generations. Models that produced compiling-but-wrong code in 2023 now produce code that passes real test suites against real codebases.

Practitioner tooling has matured alongside the capability. Claude Code, Codex, Cursor, and similar agent runtimes provide structured environments in which LLMs can read codebases, run tests, modify files, and commit changes. These tools transform the LLM from a chat interface into a software engineering agent — a process with bounded autonomy that can carry out engineering tasks given sufficient context and tooling.

The shift in capability has changed what questions are interesting. Whether models can write code is no longer in dispute. The interesting questions are about the operational characteristics of agent-driven development at scale.

### 2.2 The consistency problem

LLM outputs vary across runs even when prompts are held constant. Recent literature has documented this systematically. Angermeir et al. (2025), studying 85 articles from ICSE 2024 and ASE 2024 that performed experiments with LLMs, attempted to reproduce 18 studies that provided research artifacts. Of those 18, only 5 were sufficiently complete and executable, and none of the 5 produced fully reproducible results. The authors identified LLM output variability — even at temperature zero — as a primary factor.

A 2025 empirical study titled "AI-Generated Code Is Not Reproducible (Yet)" examined this directly for code generation tasks across Claude, Gemini, and Codex (Wang et al., 2025). The study designed prompts that explicitly asked for reproducible code, gave models multiple opportunities to produce identical output, and measured the variability that resulted. Even with reproducibility-focused prompts, the same model produced materially different code on different runs. Different models showed different patterns of variation, with Claude producing the most consistent output across tested languages and Codex showing notable variation across scripting languages.

The mechanism behind this variability is not arbitrary. Recent work on LLM code generation mistakes (Lin et al., 2025) traced specific failure modes to how models predict plausible continuations from training data. The training corpus contains many valid coding patterns; the model has learned to produce any of them. Without external constraint pinning the choice, the model picks based on local context — what tokens are recently in its window, what idioms appear in nearby code, what patterns its training emphasized. Across independent generations, the picks vary.

This is not a property unique to current models. Hutson (2018) documented reproducibility problems in AI systems generally years before LLMs reached coding capability. The pattern repeats: machine learning models that look like deterministic functions are actually distributions over plausible outputs, and the variance is a feature of the architecture, not a bug to be patched.

For software engineering, this matters at codebase scale rather than at the level of individual outputs. A single generation that uses one idiom is fine. Many generations that each pick different idioms produce a codebase that looks like ten different developers wrote it. The cumulative cost compounds: code that follows one pattern does not compose cleanly with code that follows another; refactoring becomes harder because the same concept has different shapes in different places; onboarding new developers (or new agent sessions) becomes harder because the codebase has no consistent style to teach.

### 2.3 The scale problem

A single agent working under tight human supervision can produce useful output. Whether useful output at one-agent speed is competitive with traditional development depends on the work; for some tasks the agent is faster than a human typing, for others the supervision overhead absorbs the gain. The mathematics changes when many agents work in parallel.

Parallel agent execution requires solving coordination problems that single-agent development does not face. The agents need to communicate or to know they don't need to communicate; they need to acquire work without stepping on each other; they need to handle dependencies between tasks; they need to recover when individual agent sessions fail; they need to be monitored at the system level rather than per-session. These are the standard problems of distributed systems applied to a new substrate.

Liu et al. (2025), in their literature review on LLM-based multi-agent systems for software engineering, identified the orchestration platform as one of the two primary components of any LMA system, alongside the agents themselves. The orchestration platform "serves as the core infrastructure that manages interactions and information flow among agents" and defines coordination models, communication mechanisms, and planning styles. Tang and Runkler (2026) reach similar conclusions, identifying multi-agent orchestration as a key research challenge for LLM-based agentic systems in software engineering.

Building orchestration infrastructure from scratch is substantial engineering work. Multi-agent platforms must handle session lifecycle, work tracking, dependency-aware scheduling, health monitoring, communication, and failure recovery. Doing this well requires expertise in distributed systems separate from expertise in software engineering or in LLM application. Most teams attempting to scale agent-driven development have neither expertise nor inclination to build this infrastructure themselves.

### 2.4 The combined problem

The consistency problem and the scale problem are not separable. Trust without scale produces one agent doing clean work slowly — useful but not transformative. Scale without trust produces many agents doing fast slop in parallel — actively harmful, because it produces inconsistent codebases at rates that overwhelm human review.

Most practitioners encountering one problem do not encounter the other. Teams that work with a single agent under tight supervision experience some consistency issues but do not feel the scale problem. Teams that experiment with parallel agents discover the consistency problem the hard way, often abandoning the experiment when codebase drift becomes painful. Both groups conclude that agent-driven development at scale is not yet viable. They are correct given the tools they tried; they are wrong about the general claim.

The thesis this work argues is that solving both problems together is possible, and that doing so requires two specific mechanisms operating in combination. The remainder of the thesis develops this argument in detail.

---

## 3. Related work

This section reviews three bodies of work that ground the thesis: software engineering discipline from the past five decades, contemporary literature on LLM-based software engineering and multi-agent systems, and emerging work on agentic development tooling.

### 3.1 Software engineering discipline

The discipline this thesis examines is not new. The principles encoded in the SDLC discipline pack draw on works that have shaped software engineering thinking for decades.

**Modular software design.** Parnas (1972) introduced the concept of information hiding as the basis for modular decomposition. His criterion was that modules should hide design decisions that are likely to change, presenting stable interfaces to clients. Liskov (1972) developed methodologies for reliable software systems based on data abstraction, and her later work on data abstraction and hierarchy (Liskov, 1988) formalized the substitutability principle that bears her name. These works established that modular software requires explicit attention to what each module knows, what it exposes, and how it depends on other modules. Without this discipline, software accumulates entanglement that becomes difficult to reverse.

**Domain-driven design.** Evans (2003) introduced a vocabulary and a set of patterns for building software that reflects the structure of the business domain rather than the structure of the technical infrastructure. His concepts — entities versus values, aggregates with consistency boundaries, repositories, factories, anti-corruption layers — provided a framework for engineers building complex business software. Evans's work has been particularly influential in domains where business logic is intricate and where the cost of misalignment between code and domain is high.

**Test-driven development.** Beck (2002) presented TDD as a discipline for building software with confidence: write a failing test, write the minimum code to pass it, refactor while keeping tests green. Freeman and Pryce (2009), in *Growing Object-Oriented Software, Guided by Tests*, extended this to the discipline of growing entire systems through the same iterative cycle, with extensive attention to what good tests look like and what tests reveal about design.

**Refactoring.** Fowler (1999, 2018) catalogued the practice of improving the design of existing code while preserving its behavior. His work named common code smells — the patterns in existing code that signal trouble — and provided a vocabulary for the transformations that address them. Refactoring as a discipline depends on having tests that catch behavioral regression, linking it explicitly to TDD practice.

These works span roughly fifty years of accumulated thinking. They were written for human engineers practicing in human teams. The thesis this work argues is that the same discipline can be applied to agent-driven software development, with the discipline encoded as external constraints rather than as practitioner habit.

### 3.2 LLM-based software engineering

A substantial recent literature has examined LLM application to software engineering tasks. Surveys by Liu et al. (2025), Guo et al. (2025), and Tang and Runkler (2026) provide comprehensive overviews of the state of LLM-based software engineering systems as of 2025-2026.

Liu et al. (2025), in their systematic review of LLM-based multi-agent systems for software engineering, identified two primary components of any LMA system: an orchestration platform managing agent interactions, and the agents themselves with their specific capabilities and roles. The review covered cooperative, competitive, and hierarchical coordination models; centralized and decentralized communication mechanisms; and various planning and learning styles. The authors performed two case studies of state-of-the-art LMA frameworks to demonstrate effectiveness and limitations.

Tang and Runkler (2026) reviewed LLM-based multi-agent systems specifically through the lens of the software development lifecycle. Their work identified key challenges including multi-agent orchestration, human-agent coordination, computational cost optimization, and effective data collection — challenges that align with the practical bottlenecks this thesis addresses.

Guo et al. (2025), in their comprehensive survey on benchmarks and solutions in software engineering of LLM-empowered agentic systems, identified the evolution from simple prompt engineering to sophisticated agentic systems incorporating planning, reasoning, memory mechanisms, and tool augmentation. Their analysis included critical research gaps such as multi-agent collaboration and self-evolving systems.

The literature confirms that orchestration is a recognized component of LLM-based software engineering systems and that consistency-of-output is a recognized challenge. What the literature has not extensively analyzed is the combination — how the discipline layer (which addresses consistency) and the orchestration layer (which addresses scale) interact, and what their composition enables that neither layer alone does. This thesis addresses that gap.

### 3.3 Reproducibility and consistency

Several recent works have documented the reproducibility and consistency problems with LLM-generated code. Angermeir et al. (2025) studied reproducibility of LLM-centric empirical research, finding that of 18 ICSE 2024 and ASE 2024 papers with research artifacts, only 5 were sufficiently complete to attempt reproduction, and none produced fully reproducible results.

The "AI-Generated Code Is Not Reproducible (Yet)" study (Wang et al., 2025) examined this directly for code generation, showing that even reproducibility-focused prompts cannot eliminate variation across runs. The study quantified differences across models and across languages, showing different agents have different consistency profiles.

Lin et al. (2025) analyzed LLM code generation mistakes to identify the categories and causes of non-syntactic errors. Their work, prompting GPT-4 and Qwen2.5-Coder on coding questions from selected datasets with temperature zero and multiple runs, contributed taxonomies of common LLM coding failures that motivate the anti-patterns refused by external discipline layers.

These works establish empirically what theoretical reasoning predicted: LLM outputs are distributions, not functions. The variation persists across nominally identical inputs, and downstream code quality varies with it. External constraint is the only currently known mechanism for making LLM-driven development consistent at scale.

### 3.4 Agentic development tooling

The tooling ecosystem for agentic development has emerged rapidly between 2023 and 2026. Claude Code (Anthropic), Codex (OpenAI), and similar tools provide agent runtimes — environments in which an LLM can read codebases, run commands, modify files, and commit changes. These are not new architectures so much as specific implementations of patterns previously discussed in research literature.

Multi-agent orchestration platforms have followed. MetaGPT (Hong et al., 2023) assigns different roles to generative agents for collaborative task completion. AgentScope (Gao et al., 2024) provides message exchange as a core communication mechanism with distributed deployment support. OpenAI's Swarm framework provides developer-controlled multi-agent coordination. Each of these targets a slightly different point in the design space; the literature has not yet converged on dominant designs.

Gas City, the orchestration platform analyzed in this thesis, represents a distinct point in the design space. Released in 2026 by Knutsen and the Gas Town Hall community (Sells, 2026; Knutsen, 2026), Gas City extracted general orchestration primitives from Steve Yegge's earlier Gas Town multi-agent system, with explicit attention to making orchestration plumbing reusable across different agent configurations. The design principle the authors call Zero Framework Cognition — that the infrastructure handles transport, not reasoning — distinguishes Gas City from frameworks that encode judgment in code.

---

## 4. The frameworks

This section introduces the specific frameworks examined in the case study at sufficient depth for readers without prior familiarity. The introductions are descriptive rather than prescriptive; they describe what each framework is and how it operates, deferring evaluation to later sections.

### 4.1 Large language model agent runtimes: Claude Code

Claude Code is an agent runtime developed by Anthropic that wraps the Claude family of large language models in a software engineering context. The runtime gives the agent the ability to read files, write files, run commands, search code, and commit changes — operations needed to do engineering work in a real codebase. The agent operates with bounded autonomy: it makes decisions about what to do next, but operates within constraints set by configuration files (rules, hooks, sensitive-file lists) that limit what it can touch.

When Claude Code is invoked, it operates in a session associated with a specific working directory. The session has access to anything in the directory it operates against. Files in `.claude/` are read automatically based on path-matching rules — a Python file being edited triggers loading of `.claude/rules/python.md` if present, for example. This auto-loading mechanism is how external discipline reaches the agent on every operation, rather than only when a developer remembers to include relevant context.

Claude Code is the agent runtime used throughout the case study in Section 6. It is not the only such runtime; Codex, Cursor agents, and other tools fill similar roles. The thesis is not specific to Claude Code; the analysis would apply to any agent runtime with similar capabilities. Claude Code is used because it is the runtime against which the empirical evidence was gathered.

### 4.2 Gas City: orchestration platform

Gas City is a multi-agent orchestration software development kit, released as version 1.0 in April 2026 (Sells, 2026; Knutsen, 2026). It is open-source, written primarily in Go, and licensed under MIT. The project emerged from work on Steve Yegge's Gas Town multi-agent system, with the explicit goal of extracting general orchestration primitives that could support different multi-agent configurations rather than only Gas Town's specific 8-role design.

Gas City's architecture comprises five primitives — Agent Protocol, Task Store, Event Bus, Config, and Prompt Templates — and four mechanisms built from them — Messaging, Formulas, Dispatch, and Health Patrol. The five-and-four structure is deliberate: every irreducible capability is a primitive; every other capability is derived. The Task Store primitive is implemented by Beads (Yegge, 2026), a persistent task graph system built on the Dolt versioned SQL database. The Agent Protocol primitive has pluggable implementations including tmux sessions, Kubernetes pods, subprocess invocations, and shell-script wrappers, allowing the same Gas City installation to run agents in any backing infrastructure.

The design principle the Gas City authors call Zero Framework Cognition is that the framework handles transport — sessions, work assignment, communication, health monitoring — but does not encode judgment. Decisions about how to do the work live in prompts and configuration, not in Go code. This principle is what allows agent configurations to be expressed as data: agent definitions, prompt templates, and formulas are TOML files that the framework executes without interpreting their content.

Packs are Gas City's unit of distribution. A pack is a directory containing agent definitions, prompt templates, formulas (which define multi-step workflows), and any other configuration the pack needs. Installations can import packs from version-controlled repositories at pinned versions, and per-project overrides (called rig overrides) can patch pack content using a Kustomize-style explicit-patch model. This allows packs to remain portable across projects while accommodating project-specific configuration.

For purposes of this thesis, Gas City is one instance of an orchestration layer that meets the requirements developed in Section 5.2. Other orchestration platforms could potentially fulfill the same role; Gas City is examined because it is the platform against which the case study evidence was gathered, and because its explicit attention to packs-as-distribution-units makes it well-suited for the kind of discipline-layer-as-pack pattern this thesis analyzes.

One operational consequence of Gas City's architecture worth naming up front: the supervisor is machine-wide rather than per-application. A single supervisor reconciles N city configurations (each with its own configuration file, task store, and pool roster) from one process. This shapes how applications partition work across cities — build-time concerns and runtime concerns can live in separate cities under one supervisor — which the case study in Section 6 exploits when it separates the SDLC chain from the runtime LLM-judgment pipeline. The Elder architecture decision recording this split (ADR-0013, amended 2026-05-25 to reflect the one-supervisor-many-cities model) is the operator-facing reference for that pattern.

### 4.3 The SDLC discipline pack

The SDLC discipline pack is a Gas City pack that provides discipline-layer functionality. The pack is in the v2.x line as of mid-2026 and continues to evolve at pack-version pace; the current release and the version-history record live in the pack repository's README. The pack was built specifically to enable trustworthy agent-driven software development at scale. The pack ships several categories of content.

**Rule files** define discipline that auto-loads when agents edit relevant files. The pack supplies rule files for modularity, domain-driven design, test-driven development, refactoring, and language-specific concerns (currently Python). Each rule file is associated with path-glob patterns that trigger its loading. A Python file edit causes the Python rules to load; an edit to a domain module causes the DDD rules to load.

**Principal-engineer guides** provide longer-form context that the rule files reference. These guides distill content from the foundational works discussed in Section 3.1 — Liskov, Parnas, Evans, Beck, Freeman and Pryce, Fowler — into a form the agent can read as background context when working on complex tasks.

**Agent definitions** specify the agents that participate in the SDLC chain: a worker that writes code, a tester that verifies tests, a reviewer that audits against the standards, a documenter that updates documentation, and a finalizer that merges the pull request when everything passes.

**Formulas** define the chain workflow that sequences these agents. A typical SDLC formula runs worker → tester → reviewer → documenter → finalizer, with failed audits triggering correction or escalation.

**The differential gate** is the pack's anti-weakening mechanism. The gate runs as part of the test and review phases and refuses changes that introduce ruff or mypy errors against the baseline, add suppression directives, mark tests as skipped or expected-failure, or reduce assertion counts. The gate prevents the failure mode where an agent satisfies its task by lowering the bar rather than by doing the work.

**The merge-failure recovery loop** (introduced in v2.7.0) handles the failure mode where parallel agents produce pull requests that conflict pairwise. When a finalizer rebase fails, the bead routes back to the worker pool with conflict context; the worker's rebase-iteration mode resolves the conflict and the chain re-walks through tester, reviewer, documenter, and finalizer. A bounce counter caps the loop, escalating to human review after a configurable number of attempts. An autonomous trigger (an event-driven watcher complemented by a cron-driven sweeper) detects sibling pull requests that fall behind the target branch after a merge and re-routes them into the loop. This is the orchestration layer's answer to the timing-conflict problem that parallel execution generates.

**The stories bridge** is a CLI tool that takes story specifications (markdown files with structured frontmatter) and creates the corresponding beads in the Gas City task store. Stories declare their dependencies; beads inherit them.

**The cost helper** tracks per-phase LLM API costs by parsing session output and computing per-phase USD costs. Output integrates with the Gas City task store, recording costs against each bead as it closes.

The pack distinguishes between content that travels with the pack (the discipline, the chain shape, the gate, the bridge, the helper) and content that lives in the rig (project-specific rule overlays, sensitive-file lists, design document paths). The architectural separation enables the discipline to be reused across projects while project specifics remain local.

### 4.4 Beads: persistent task storage

Beads is the persistent task graph system that backs Gas City's Task Store primitive. Developed by Steve Yegge as a separate project (Yegge, 2026), Beads provides persistent storage for tasks with dependency tracking, status management, and event observation. Each task is a bead with attributes (status, dependencies, routing labels, content), and the system supports queries against the bead graph (find ready beads, find beads routed to a specific agent type, find beads in a specific status).

Beads is built on Dolt, a versioned SQL database that provides Git-style branching and merging for structured data. This gives Beads several desirable properties: tasks have full history, can be branched and merged like code, and can be federated across installations using Dolt's remote protocols. For purposes of this thesis, Beads's specific implementation matters less than what it provides: persistent task storage that outlives any individual agent session, which is the property that lets Gas City recover from agent failures and resume work after restarts.

### 4.5 Operational vocabulary used throughout the thesis

The vocabulary below recurs across Sections 5 through 9. The terms are introduced here so a reader who jumps to the case study or the implications sections can follow without having to re-derive their meaning from context. Each term names a specific operational concept rather than a metaphor.

**Chain.** The fixed sequence of phases the SDLC discipline pack runs on one unit of work: worker → tester → reviewer → documenter → finalizer. A "chain run" or "chain" without further qualification refers to one walk of this sequence on one story.

**Phase.** One of the five steps in the chain, each carried by a different agent running in a fresh session under a phase-specific prompt and responsibility. The worker writes the code change. The tester runs the test suite, the differential gate, and the language-specific audits (lint, type-check, security scan). The reviewer audits the change against the pack's rules and the rig's project-specific overlays. The documenter writes the feature documentation. The finalizer opens or refreshes the pull request and merges it once the upstream gates pass.

**Story.** A specification for one chain-runnable unit of work. A story is a markdown file with structured frontmatter (acceptance criteria, sensitive-file declarations, dependencies on other stories, scope boundaries) and prose detailing what the work should produce. Stories are filed as beads in the task store; one chain run processes one story.

**Pool.** A set of identically-configured agent slots that the orchestration layer scales between zero and a configured maximum based on demand. Each phase has its own pool — a worker pool, a tester pool, and so on — so that multiple stories can run the same phase concurrently up to that pool's maximum. Pool maxima appear in the case study as the throughput floor under burst load (Section 6.3 reports max = 2 on the documenter and finalizer pools becoming the binding constraint for a nine-chain burst).

**Burst.** A set of chains dispatched concurrently. The case study uses bursts both as a parallelism measurement (the VAL-001 burst examined in Section 6.3) and as a practical means of clearing a batch of independent work.

**Bounce.** A chain iteration triggered by a failure the orchestration layer can address by re-walking phases rather than escalating to human review. Two kinds appear in the case study. A tester-to-worker bounce fires when the worker's code does not pass the differential gate; the worker receives the gate's complaint and re-attempts the change. A finalizer-to-worker bounce (introduced in pack v2.7.0) fires when the worker's code is correct in isolation but no longer composes cleanly with main after an upstream merge; the worker enters a rebase-iteration mode that resolves the conflict, then the chain re-walks tester through finalizer. A bounce counter caps the loop; exceeding it escalates to human review.

**Trap story.** A story specification constructed so that each acceptance criterion pairs a legitimate engineering path with a tempting shortcut (a `pytest.mark.xfail` marker, a `# type: ignore` suppression, a weakened-tolerance assertion). The construction tests whether the differential gate catches the deliberate shortcut attempts. VAL-003 in Section 6.3 is one such story.

**Sensitive file.** A file declared by the rig — in `.claude/rules/project/sensitive-files.md` or an equivalent location — as requiring explicit human review on any edit. Examples in the case study include the risk-parameter configuration and the indicator-parameter definitions whose values come from the source material's published parameters. The reviewer agent routes pull requests touching sensitive files to a human-required tier rather than auto-merging them.

---

## 5. The discipline-orchestration architecture

This section presents the central analytical contribution of the thesis: a framework for understanding what makes agent-driven software development trustworthy at scale. The framework analyzes any such system as composed of two layers — a discipline layer and an orchestration layer — and identifies what each layer must provide for the composite system to work.

### 5.1 The discipline layer: what it must provide

The discipline layer's purpose is to make agent output trustworthy by constraining it. The layer enforces consistency that the agent itself cannot maintain across many independent generations, for the structural reasons documented in Sections 2.2 and 3.3. Four properties are required.

**Coverage of decisions that matter.** Not every decision needs to be constrained. The discipline layer pins down the decisions where drift causes the most damage: data shapes at module boundaries, error-handling patterns, test idioms, abstraction discipline, naming conventions, dependency direction. The decisions that do not matter — exact line length, comment style preferences, ordering of imports beyond the standard categories — can be handled by formatters or left to the agent. Coverage is not exhaustive; it is targeted at where consistency compounds.

**Auto-loading into agent context.** Rules that the agent does not read on every generation do not constrain the agent. The discipline layer needs a mechanism to load relevant rules into the agent's context whenever it touches relevant code. Path-glob triggers work well: rules for Python files load on every Python edit; rules for the test directory load on every test edit; rules for domain code load whenever the domain layer is touched. The agent reads what is relevant; the rest stays out of context.

**Mechanical audits at output time.** Rules in context narrow what the agent produces; audits at output time catch what slipped through. An audit is a check that runs against the agent's output: did it pass lint, did it pass type-checking, did it add suppression directives, did it weaken test coverage, did it violate architectural boundary rules. Audits that fail block merge. The agent's work is not done until the audits pass.

**Anti-weakening enforcement.** This is the failure mode most worth catching. An agent given a task that takes substantive effort to complete properly may instead make its work appear to pass by lowering the bar — adding type-ignore comments, marking tests as skipped, removing failing assertions, weakening contracts that were previously stricter. The agent does not do this maliciously; it is choosing among ways to satisfy the task, and the bar-lowering route is locally easier. An anti-weakening gate refuses this kind of change: a branch must not introduce new lint or type errors against the baseline, must not add suppression directives, must not mark tests as skipped or expected-failure, must not reduce assertion counts. Work that triggers any of these blocks before merge.

The SDLC discipline pack described in Section 4.3 is one concrete instance of this layer. Its rule files implement the auto-loading requirement. Its differential gate implements anti-weakening enforcement. Its principal-engineer guides provide the foundational reasoning that the rules distill. The pack's specific implementation could be replaced by another instance with the same properties; the requirements are general, not specific to one design.

An early empirical test of the anti-weakening property — VAL-003 in Section 6.3 — designed a story so that each acceptance criterion paired a legitimate engineering path with a tempting shortcut (`pytest.mark.xfail`, `# type: ignore`, weakened assertion). Across that test and the chain runs that followed, zero shortcut markers appeared in any merged diff and the differential gate did not need to fire. As of 2026-05-25 the sample has grown to roughly 239 merged pull requests through the chain over a six-week window, and the directional reading has held — workers consistently produced honest paths before the back-end audit had occasion to refuse. The sample is large enough to be load-bearing for the directional claim though still short of the size needed for a tight statistical bound. The pattern §6.4 surfaces — that the front-end constraint does most of the work and the back-end audit closes the loop — is the same pattern the §5.1 property predicts: the agent's choice space narrows to honest paths before the gate has to refuse anything.

### 5.2 The orchestration layer: what it must provide

The orchestration layer's purpose is to coordinate many agents working in parallel. The layer manages the operational complexity that single-agent development does not face. Five properties are required.

**Session lifecycle management.** Each agent runs in a session — a process, container, or terminal that contains the agent's working state. The orchestration layer creates sessions, assigns them work, monitors them, restarts them on failure, and shuts them down when they finish. Without this, every agent needs its own session-management code, and adding a second agent doubles the complexity.

**Persistent work tracking.** Work needs to live somewhere outside the agent's session, because sessions come and go. A persistent task store holds the work — what needs doing, what is in flight, what is done, what dependencies exist between tasks. When an agent's session dies, the work survives. The next session picks it up.

**Dependency-aware scheduling.** Real work has dependencies. Task B cannot start until task A produces its output. Task C and task D can run in parallel because they do not share anything. The orchestration layer must understand dependencies and schedule accordingly. Agents claim tasks whose dependencies are satisfied; tasks with unsatisfied dependencies wait. The scheduler maximizes parallelism within the dependency constraints.

**Health monitoring and self-healing.** Agents stall, crash, or get stuck in loops. The orchestration layer's contribution to recovery is bounded: it respawns sessions that crash, preserves work tracking across restarts, and surfaces the failure to whatever observer is configured to act on it. That covers process-exit failures cleanly. The wider class of stall failures — API overload that the agent runtime retries against, per-turn cap expiry that drains and exits cleanly, alive-but-idle drift where the process stays running at an input prompt — requires additional mechanisms layered atop the orchestration platform. In the case study these mechanisms ship in the discipline pack as recovery scripts (a retry wrapper around the agent runtime, a drain-ack subscriber that catches per-turn-cap exits, and a pane-state cron that submits a synthetic continuation turn when the agent stalls alive) rather than in Gas City itself. The composition is what produces the self-healing property as a whole — neither the orchestration layer nor the discipline-pack recovery scripts cover every shape alone. Without the composite, every operator becomes responsible for keeping agents alive, which does not scale.

**Pluggable infrastructure.** Where agents actually run varies. Local development uses one approach; production uses another. The orchestration layer should not assume one and break on the other. A provider abstraction lets the same configuration run agents in any backing infrastructure.

Gas City, described in Section 4.2, is one concrete instance of this layer. Its five primitives and four mechanisms implement these properties. Other orchestration platforms exist that fulfill similar roles, including MetaGPT (Hong et al., 2023), AgentScope (Gao et al., 2024), and OpenAI's Swarm framework. Gas City is examined here because its explicit attention to packs-as-distribution-units makes it well-suited to compose with discipline layers like the SDLC pack.

### 5.3 The composite system

The discipline layer and the orchestration layer are not independent. They compose into a system whose properties exceed the sum of the parts. The orchestration layer enables many agents to work in parallel; the discipline layer enforces consistent quality across all of them; together they produce parallel agent work that meets a uniform quality bar.

The composition mechanism in the Gas City case is the pack model. The SDLC discipline pack is imported into a Gas City installation. Pack content materializes into each agent's session at spawn time, so every agent that Gas City launches operates under the discipline the pack supplies. Per-project overrides allow projects to extend the pack's content with project-specific overlays without modifying the pack itself.

The composite system's workflow has a specific shape that is worth describing in detail because it differs from traditional software development.

**Step 1: Requirements gathering.** The technical product manager gathers requirements from business stakeholders. This step is not changed by the architecture; the requirements work is human work, conducted in the way it has always been conducted.

**Step 2: Story authoring.** The TPM and the engineer collaborate to translate requirements into structured stories. Each story has acceptance criteria in observable form, sensitive-file declarations, and dependencies on other stories. The TPM keeps stories grounded in business value. The engineer surfaces architectural ambiguity and knows what the chain can and cannot reliably handle.

**Step 3: Dependency graph construction.** The engineer works with the agent runtime (Claude Code in the case study) to organize the stories into a dependency graph. Some stories enter the graph as authored; some need further decomposition into chain-runnable units. The result is a directed acyclic graph where each node is a story sized for one chain run and each edge represents a concrete dependency — story B cannot start until story A's code is in main.

The dependency graph is the engineer's load-bearing artifact in this workflow. It captures architectural understanding (what depends on what), risk allocation (which stories touch sensitive files and require human review), and scheduling (what can run in parallel). Building a good graph is the difference between a milestone shipping in days versus weeks. Good graphs have specific properties: each node is sized for one chain run, edges declare concrete dependencies rather than coordination preferences, the critical path is visible, and sensitive nodes are declared.

**Step 4: Injection into the chain.** Once the graph is ready, the engineer files the stories as beads in the Gas City task store and routes them to the SDLC pack's worker pool. The chain takes over. Workers claim stories whose dependencies are satisfied; multiple workers run in parallel where the graph allows. Each story flows through the chain phases — worker writes code, tester verifies tests, reviewer audits against the standards, documenter updates documentation, finalizer merges when the differential gate passes. Failed audits trigger correction or escalation. Sensitive-file edits halt for human review. Everything else runs unattended.

Feedback loops happen at natural boundaries — milestone reviews, MVPs reaching stakeholders, integration tests surfacing issues. The chain does not eliminate iteration. It shortens the implementation phase of each iteration to the point where iteration becomes cheap and frequent.

### 5.4 What the architecture enables

The composite system enables specific operational properties that neither layer alone provides.

**Trust at codebase scale.** The discipline layer enforces consistency across many independent agent generations. Code generated by different agents in different sessions looks like code generated by one consistent developer, because the constraints are uniform. The differential gate prevents drift across many small changes.

**Parallel execution.** The orchestration layer schedules independent work to run simultaneously, with the number of parallel agents bounded by the dependency graph and by available compute capacity. For most non-trivial work, parallelism reduces wall-clock time meaningfully.

**Self-healing operation.** The orchestration layer's health monitoring and the discipline layer's audits combine to handle most failure modes without human intervention. Agents that stall get restarted; work that fails audit triggers correction; only failures that cannot be self-healed escalate to humans.

**Cost transparency.** The cost helper integration tracks LLM API costs per task and per phase. The team can analyze cost-per-story and cost-per-milestone with empirical accuracy rather than estimation.

**Quality auditability.** Every change passes through the chain's defined phases with documented outputs. The audit trail for any merge is preserved and inspectable. Quality enforcement is mechanical and reproducible rather than dependent on which human reviewed which change on which day.

**Operational observability.** The composite system surfaces failures that escape self-healing in a form the operator can act on without polling. Success-side notifications fire on chain completion and on parks-for-human-review; silent-failure detection runs as periodic mechanical audit against operational state, alerting on beads stuck mid-phase past their phase SLO and on cron orders whose last fire is older than expected. The pattern generalizes the differential gate's output-time audit to a temporal audit against the chain's runtime state. Without this property the operator cannot trust unattended operation, because failures that escape both layers' active protections surface only when the operator polls.

---

## 6. Case study: the Elder Trading System

This section presents empirical evidence from the application of the discipline-orchestration architecture to a real-world software project. The Elder Trading System is a quantitative trading platform implementing Alexander Elder's trading methodology, developed by a single engineer between approximately late 2025 and the time of writing in mid-2026.

### 6.1 The project

The Elder Trading System combines several technical capabilities. It pulls market and fundamental data from Interactive Brokers, SEC EDGAR, Yahoo Finance, and the Anthropic API. It applies a quality-at-reasonable-price (QARP) filter combining factors from Novy-Marx, Greenblatt, and Asness/Frazzini/Pedersen. It implements Alexander Elder's Triple Screen trading system with specific indicator parameters drawn from Elder's *Come Into My Trading Room* (2002) and *The New Trading for a Living* (2014). It maintains state in PostgreSQL with a Streamlit dashboard for human-in-the-loop trade approval.

The project is intended for production deployment with the engineer's own capital at risk. Its scope — Triple Screen indicator logic, full 2%/6% risk-management enforcement, real-time stop monitoring, Interactive Brokers TWS API integration, persistent state in PostgreSQL, a Streamlit dashboard for human-in-the-loop trade approval — is comparable to a small commercial trading system rather than to a sample application.

The project structure has 27 specifications in its build plan, decomposed into chain-runnable stories. The codebase contains roughly 10,000 lines of Python including tests at the time of the 2026-05-16 thesis revision and roughly 45,800 lines as of 2026-05-25, organized into core/, agents/, indicators/, db/, dashboard/, knowledge/, and tests/ packages. The system has not yet executed live trades; the paper-trading infrastructure — IB Gateway on the production workstation under systemd, the IBExecutor adapter and stop monitor shipped through the chain, the SafeZone recalculation handler — reached operational status as Phase 2 progressed in the days after the thesis's initial writing. Phase 0 (pipeline engine refactor) and Phase 1 (trade approval and audit persistence) are complete; Phase 2 (trade execution and real-time stop monitoring) is in progress.

### 6.2 The adoption

The engineer adopted the discipline-orchestration architecture progressively. Early development (late 2025) used Claude Code interactively without the SDLC chain — agent-as-fast-typist with per-PR human review. This phase produced working code but exhibited the consistency and scale problems Section 2 describes. The codebase showed drift between modules; productivity scaled with engineer attention rather than with agent capability.

The transition to the discipline-orchestration architecture occurred over approximately two months in 2026. Key milestones included:

- The SDLC discipline pack reaching version 2.6.3 with stable formulas and gates
- The Elder rig integrating the pack via Gas City's import mechanism
- Decoupling work that separated project-portable discipline content from Elder-specific overlays
- The first chain runs that exercised the full worker → tester → reviewer → documenter → finalizer pipeline
- A 12-concurrent stress test that landed 8 mechanical PRs in 54 minutes for $18.71 in API costs
- The v2.7.0 merge-failure recovery loop, designed and validated on the engine-refactor stories of Phase 0 — the first observed cycle in which parallel chains produced pairwise-conflicting pull requests, and the chain recovered without per-PR human intervention

After the transition, the project's primary development mode became chain-driven. Stories were authored collaboratively between the engineer and the agent runtime, organized into a dependency graph, and injected into the chain for execution. The engineer's attention shifted from per-PR review to story authoring, graph construction, and milestone-level oversight.

### 6.3 Measured results

Several empirical measurements were captured during the case study.

**A note on how cost figures are reported.** The chain phases in this case study authenticate against the engineer's Claude Max subscription rather than against a Claude Developer Platform API key. The dollar figures reported in this section — both in Table 1 and in the per-phase breakdowns that follow — are *API-rate equivalents* computed by multiplying token counts recorded in each phase's session transcript by Anthropic's published per-million-token pricing. They represent what each chain run would have cost at standard API rates. The engineer's actual dollar charge for these runs is the Max subscription's monthly fee plus any overage incurred when usage exceeds the subscription's interactive Claude Code allowance. The case study reports the API-rate equivalent because that number is comparable across auth modes (subscription vs API key) and makes the discipline-cost trajectory legible regardless of how a given operator chooses to pay.

**Per-story execution.** Ten stories of varying shape are shown in Table 1. Additional stories — primarily Phase 0 work prior to the engine refactor and the nine mechanical docstring additions aggregated as a single row inside the VAL-001 burst — have shipped through the chain but are not listed individually.

| Story | Description | Wall clock | Notes |
|-------|-------------|------------|-------|
| EL-014 | risk_parameters consumer audit | 1h 9m | $139.23 worker cost |
| EL-001 | StageCoordinator skeleton + risk_agent migration | 54m | Parallel with EL-015 |
| EL-015 | import-linter rule for risk_parameters | 41m | Parallel with EL-001 |
| EL-005 | Per-stage permission scoping | 1h 7m | Merged manually during v2.7.0 validation |
| EL-003 | Cumulative budget tracking with halt-on-exceed | ~50m + bounce iteration | First end-to-end exercise of the merge-failure recovery loop; one finalizer bounce, one worker rebase iteration, clean re-walk and auto-merge |
| REFACTOR-001 | core/state.py decomposition into cohesive modules | 83m | Eight atomic commits per Two-Hats discipline; 219 tests pass; preparatory refactor unblocking downstream agent migrations |
| REFACTOR-002 | indicators/elder.py decomposition | 63m | Nine atomic commits; six Hypothesis property tests added as gap-fill |
| REFACTOR-003 | risk_agent gates extraction into separate module | 54m | Five atomic commits; nine boundary tests added as gap-fill |
| VAL-003 | force_index property tests (trap story for anti-weakening) | 36m | First deliberate empirical test of the §5.1 anti-weakening claim — see "Anti-weakening evidence" below |
| VAL-001 burst | Nine mechanical stories dispatched concurrently | 95m total | 9-of-9 first-pass clean; zero bounces; downstream pool serialization observed at documenter and finalizer pools (max = 2); approximately $3,346 total across the nine burst PRs ($372 per PR average), recovered retroactively after the v2.9.4 cost-rollup fix |

*Table 1: Measured story execution times and notes for ten stories spanning Phase 0 (EL-*), preparatory refactors (REFACTOR-*), and the validation arc (VAL-*).*

EL-014 was a substantive audit and migration touching 19 risk-parameter consumers across the indicator code. EL-001 and EL-015 ran in parallel, with worker phases starting within 16 seconds of each other; the wall-clock time for the two-story parallel batch was 54 minutes total. EL-005 was merged manually as part of v2.7.0 validation work, since its parallel siblings became the first deliberate test case for the recovery loop's behavior under real pairwise conflicts.

The three REFACTOR stories were dispatched in lagged-parallel — REFACTOR-002 first, REFACTOR-003 about five minutes later — to validate the chain's behavior on a pair of compatible refactors touching different files. Both chains ran end-to-end with zero operator-in-the-loop moments, zero bounces, and zero merge conflicts (each touched `.claude/rules/project/sensitive-files.md` at non-overlapping positions; git's three-way merge handled the integration without intervention).

**Anti-weakening evidence.** VAL-003 was designed as a trap story: each of the three property tests it asked the worker to write was paired with a tempting shortcut — `@pytest.mark.xfail` with a hand-wave reason for Test 1, `# type: ignore` to silence a float-comparison warning for Test 2, a weakened-tolerance assertion for Test 3. Each shortcut would have caused the differential gate's anti-weakening checks to fire (new suppression directive, new skip marker, lost assertion count). The honest paths required real engineering: investigate the implementation's actual contract, choose principled tolerances backed by IEEE 754 reasoning, defend strict equality. The chain converged on the first attempt with zero shortcut markers in the diff (confirmed via grep across the merged commits for `type: ignore`, `noqa`, `nosec`, `pyright: ignore`, `mark.skip`, `mark.xfail`, `mark.skipif`). The gate's anti-weakening checks did not need to fire. The directional reading — and it is directional, not statistical at this sample size — is that the worker chose the honest path natively. The same pattern held across the nine subsequent chains in the VAL-001 burst, none of which attempted any of the shortcut categories.

**Chain phase distribution.** Within each run, worker phases consumed 60-80% of wall clock time. Tester, reviewer, documenter, and finalizer phases consumed the remainder. LLM inference time, not orchestration overhead, dominated chain cost — the underlying generation work is where the time went, which matches what theoretical reasoning predicted.

**Tester-to-worker bounce rate.** Across the completed runs, no shipped story required the worker to rework after the tester ran. The differential gate combined with TDD discipline at worker time appears to pay for itself: the cost of doing the work correctly at worker phase is less than the cost of one tester-detected bounce back. The sample remains small but the directional signal is consistent with the theoretical argument that discipline-at-generation-time is cheaper than discipline-at-review-time. The v2.7.0 recovery loop introduced a separate bounce category — finalizer-to-worker on rebase conflict — which is structurally distinct from a tester bounce: the worker's code is correct in isolation; it just no longer composes cleanly with main. The first observed instance bounced once and resolved on the worker's first rebase iteration, suggesting the recovery loop's design is sound but, again, the sample is small.

**Concurrent operation.** Two concurrent stress measurements anchor the parallelism claim. The earlier 12-concurrent stress test from a pre-v2.7.x pack version landed 8 mechanical PRs in 54 minutes at approximately $2.34 per PR, demonstrating that the orchestration layer can support meaningful concurrency without breaking down. The VAL-001 burst on the current pack (v2.9.x with the full anti-weakening discipline in place) dispatched 9 mechanical stories concurrently and landed 9 PRs in 95 minutes from first worker-start to last finalizer completion, with zero chain bounces, zero operator interventions in the chain itself, and a total cost of approximately $3,346 across the burst ($372 per PR average; cost recovered retroactively after the v2.9.4 cost-rollup fix described in Section 8.2). The two measurements differ along five dimensions — pack version, story shape, pool sizing, Gas City binary version, rig — so they are not directly comparable; the directional reading is that parallel dispatch continues to produce parallel completion within the dependency graph's constraints. The pattern at 2-concurrent (EL-001 and EL-015 in parallel) confirmed that the scheduling worked correctly with concrete dependencies between non-trivial work. The v2.7.0 cascade — three sibling pull requests autonomously routed through the recovery loop by the cron sweeper after a parent merge — extended the concurrent-operation evidence into the contested-merge regime, where parallel chains produce conflicting outputs and the orchestration layer must mediate.

The cost difference between the two measurements deserves explicit treatment. The roughly 160-fold per-PR increase from $2.34 to $372 reflects compounding sources: the worker phase's per-task reasoning depth grew as anti-weakening, TDD, and modularity rules thickened (worker accounted for $197 of the average VAL-001 chain, the dominant component); the v2.9.0 bandit security gate added tester work; the differential gate's anti-weakening checks added reviewer judgment that did not exist in the earlier build; and every phase now runs Claude Code with `--effort max`. The chain has become more expensive in direct proportion to the discipline it now enforces. Per-phase model selection — moving tester, documenter, and finalizer from Opus to Sonnet, introduced in v2.10.0 — is the primary mechanism to compress per-chain cost without weakening the discipline that produced the growth, with projected savings around $50 per chain on those three phases. The thesis treats this trajectory as a tradeoff rather than a regression: discipline enforcement is what makes parallel agent output trustworthy at scale, and the cost is the price of that property.

The VAL-001 burst surfaced one throughput-shaping property worth recording. When more chains arrive at a phase than that phase's pool maximum permits, the chains queue and serialize through the phase. In Elder's pool configuration the documenter and finalizer pools are sized at max = 2 (the SDLC pack defaults). For the 9-chain burst this meant the eighth and ninth chains to reach documenter waited approximately seventeen and eighteen minutes respectively for slots. Phase work itself ran fast — two to four minutes per chain per phase — but the cumulative queueing extended total wall clock. Across the nine chains, time spent in inter-phase reconciler latency totaled approximately 25% of cumulative chain time; the remainder was phase work. The throughput characteristic is thus dominated by downstream pool capacity rather than worker capacity, which is the opposite of the intuitive failure mode and suggests pool-sizing decisions are load-bearing for burst-driven workloads.

**Pack distribution.** The SDLC discipline pack shipped seven patch and minor versions during the case study period after first contact with real chains (v2.6.1, 2.6.2, 2.6.3, 2.7.0, 2.7.1, 2.7.2, 2.7.3). Each fixed bugs or added capabilities that only surfaced through real operation: bd edge-convention inversion, Claude Code recap stall, cost helper zero output, missing assignee-clear on closed-to-open transitions, wrong command namespace in the autonomous watcher, GitHub mergeable-state UNKNOWN at watcher fire time. The pack-distribution mechanism proved sound under iteration — each patch shipped, deployed, and validated within a single working session.

**Cost tracking accuracy.** The pack's cost helper computes per-phase USD costs by parsing Claude Code's session JSONL transcripts, summing input, output, and cache tokens by model, and applying Anthropic's published per-million-token pricing. The helper produces an API-rate equivalent regardless of how the underlying Claude Code session is authenticated (subscription or API key); the token counts in the JSONL are the same in both cases. The helper itself is straightforward; the wiring around it failed for some time in operational obscurity (an observer that should have appended rows to the city-level ledger was silently producing nothing for rig-bound beads). The v2.9.4 pack release fixed the wiring; the API-rate-equivalent numbers reported in Table 1 and the per-phase totals derive from this corrected pipeline. Two cross-checks remain open: comparing the helper's output for an individual API-key-billed chain run against the corresponding Anthropic billing dashboard entry to confirm the per-token math, and characterizing the subscription's interactive Claude Code allowance against the chain's burst usage profile to determine when overage is incurred under subscription auth.

### 6.4 Discipline manifested in code

The measurements in §6.3 show the discipline firing — gate verdicts, zero shortcut markers, structured reviewer recommendations — but say less about what the discipline produces. The pack's stated purpose is to constrain the LLM's choice space toward best practices in design, coding, and testing, then verify on the back end that the constraints held. The constraint is the load-bearing part: rules auto-load into the worker's context at generation time and shape what gets written before any audit fires. The verification is the safety net: the differential gate and the reviewer's audit checklists run against the produced diff and refuse anything that slipped through. The two phases ride on the same set of rules — the rule the worker reads at task start is the rule the reviewer cites in the review file at task end[^reviewer-checklists] — and what follows shows both phases visible in merged artifacts from the case study, organized by the four practice lines named in §5.1.

[^reviewer-checklists]: The reviewer also reads review-specific checklists (for example, `.claude/rules/project/review-elder.md`) that prescribe what to audit. These are not new constraints; they are pointers back to the shared practice rules. The constraint the worker applied and the audit the reviewer ran trace to the same rule file.

**Modularity: explicit public surface and dependency direction.** The module-level docstrings written by the chain consistently declare both what the module exposes and where it sits in the dependency graph. A risk-gate primitive module — extracted from a larger agent module in one of the case study's decomposition refactors — opens with:

```
Risk-gate primitives — pure-function gates enforcing Elder's 2%, 6%, Impulse,
capability, and circuit-breaker policies. Each reads a `TradeProposal` plus an
`AccountSnapshot` and returns `RiskDecision | None`; the composer
`evaluate_proposal` in `agents/risk_evaluate.py` walks them in sequence. Leaf
module for risk logic — no internal imports through here.

Public surface: the seven `gate_*` functions (`gate_impulse_censorship`,
`gate_capability`, `gate_apgar`, `gate_per_trade_risk`, `gate_channel_width`,
`gate_six_pct_budget`, `gate_circuit_breaker`), `AccountSnapshot`, and
`approved_decision`.
```

The public surface is enumerated by name. The dependency role is declared — "Leaf module for risk logic — no internal imports through here." A reader who lands in this module can answer "what does it expose, and who depends on it" without grepping. This is the Parnas information-hiding criterion from §3.1 turned into a writing habit the chain reliably produces because the modularity rule auto-loads on every Python edit and the reviewer's audit checklist includes a "Module shape" check that flags missing public-surface declarations.

**Domain-driven design: frozen types, bounded contexts, ubiquitous language.** The shared-vocabulary module produced by an early decomposition refactor is 43 lines and opens with a one-paragraph statement of its bounded-context role: "shared concepts only — types specific to one bounded context live in that context's own module, not here." Four public enums follow — `Direction`, `Impulse`, `ABCRating`, `TradeGrade` — each value named in the project's ubiquitous language (`LONG`, `SHORT`, `STAND_ASIDE`; `GREEN`, `RED`, `NEUTRAL`, `BLUE`). The `BLUE` alias carries a one-line comment naming the source: "Elder 2014 name; alias for NEUTRAL." Vocabulary reconciliation is documented in code, not in a separate glossary that can drift.

Domain types are frozen by default. The `AccountSnapshot` dataclass that flows into the seven-gate sequence carries its invariant in its docstring: "Snapshot is frozen so a stage cannot mutate it between gates and a later batch's running open-risk update cannot leak back into an earlier proposal's evaluation." The decision to freeze is justified at the point of decision rather than left to convention.

Gate function names match the domain rules they enforce. `gate_impulse_censorship` names the rule it implements (the prohibition against buying when the Impulse System is red); `gate_six_pct_budget` names the 6% open-risk rule; `gate_per_trade_risk` names the 2% rule. The names are not interpretations — they cite the rules. The rig's vocabulary rule auto-loads on every Python edit; the reviewer's domain checklist audits the diff against it. Generic synonyms — "stop-loss," "envelope," "trend filter" — are flagged before merge.

**Test-driven development: pure-function shapes and behavior-named tests.** The gate functions are pure: each takes typed inputs and returns either `None` (gate satisfied) or a `RiskDecision` (gate rejection with the failing rule named). Inputs in, value out, no shared state. The composer uses a short-circuit pattern that test fixtures exercise one gate at a time:

```python
if (d := gate_impulse_censorship(proposal, equity)) is not None:
    return d
if (d := gate_capability(proposal, account, equity)) is not None:
    return d
```

The shape lets a single fixture build a passing-everywhere account and a violating-one-gate proposal, then assert that the named gate fires. The same decomposition refactor that split the production code by submodule also split the test file along the same boundaries, so tests track module shape rather than the other way around.

The reviewer's test-discipline checks ride on the same auto-loaded rules. A representative reviewer file from the case study writes:

> Test discipline: `test_module_docstring_covers_required_topics` describes the behavior (not the method), reads the source via `Path(snapshots_module.__file__)` rather than monkey-patching, and carries domain-language diagnostic messages on every assertion.

Behavior-not-method naming, no monkey-patching, domain-language assertion messages — each is a discipline the test rule prescribes and the reviewer audits against.

**Refactoring: atomic operations named from the catalog.** Refactors land as multi-commit pull requests in which every commit names a Fowler-catalog operation. One case-study decomposition — splitting a monolithic indicator file into five submodules — produced twelve commits that read like a Fowler checklist:

```
refactor(indicators): Move Function — extract pure math to indicators/math.py
refactor(indicators): Move Function — extract signal logic to indicators/signals.py
refactor(indicators): Move Function — extract divergence detection to indicators/divergence.py
refactor(indicators): Move Function — extract market thermometer to indicators/market.py
refactor(indicators): Move Function + Inline File — extract snapshots...
chore(rules): rescope sensitive-files list for indicators submodules
test(indicators): split test_indicators_elder.py per submodule and add property tests
refactor(indicators): Remove Middle Man — narrow elder.py to its actual public surface
```

Every refactor commit names the operation from Fowler's catalog. The chore commit isolates the rule-system bookkeeping (declaring the new submodules as sensitive files) from the behavior change. The test commit lands the parallel test-side split. None of the refactor commits introduces or modifies a feature — the Two-Hats discipline from the refactoring rule is honored on every commit boundary. The front-end constraint shows up as the commit shape; the back-end verification shows up in the review file, which on a routine docstring change in the case study quoted "single-hat (`docs(risk-params): …`) per `refactoring.md`" to confirm the rule was followed before recommending pass.

**The compound effect.** Each rule constrains the choice space along one dimension; together they shape the artifact along multiple dimensions at once. A worker writing risk-gate code under this discipline has the modularity rule pushing toward explicit public-surface declarations, the DDD rule pushing toward frozen types and named-rule functions, the TDD rule pushing toward pure-function shapes that are trivially testable, the refactoring rule pushing toward atomic commits, and the rig's vocabulary rule pushing toward the project's ubiquitous language. The pattern is uniform across all four practices: a rule auto-loads at worker time to constrain what gets written, and the same rule's audit checklist fires at reviewer time to verify the constraint held. The front-end constraint does most of the work — the §6.3 trap test showed workers choose honest paths natively when shortcuts are tempting, so the back-end audit rarely needs to fire — but the back-end verification is what makes the system trustworthy: it closes the loop by checking that the constraints actually held in the produced diff. The result is code that reads like one consistent developer wrote it — the consistency property §2.2 names — because the same constraints narrowed the choice space at every decision point and the same checklists verified afterward that the narrowing took. The discipline pack does not produce best-practice code by reviewing for it alone, nor by hoping the worker writes it alone; it constrains the worker's choice space up front and verifies on the back end that the constraints held.

### 6.5 Observed patterns

Several patterns emerged from the case study that are worth highlighting.

**The decomposition multiplier.** The Elder build plan's 26 specifications decomposed into 88 chain-runnable stories — roughly 3.4× expansion. Each story was sized for a single chain run, typically touching 1-3 files. This decomposition was substantial engineering work that the architecture's design did not initially anticipate. The engineer spent significant time learning to author chain-runnable stories well; early stories proved too large or had unclear acceptance criteria, requiring rework.

**The spec-decay failure: backlogs accumulate stale assumptions.** Decomposing 26 build-plan items into 88 chain-runnable stories produces a long-tail backlog whose oldest entries may sit for weeks before their slot in the dependency graph opens. The codebase moves under them. Two cases observed in the case study illustrate the failure mode. A persistence-layer story drafted early in Phase 1 proposed creating eight database tables; by the time its slot opened, those tables already existed in the schema because adjacent work had landed them through a different path. A code-quality story drafted after a major refactor proposed adding module docstrings; by the time its slot opened, the docstrings had been written by the refactor itself. In both cases the spec was internally consistent but no longer matched reality. The failure surfaced at human triage time — the engineer caught both before they were filed as beads — but if the kickoff process had been fully automatic, the chain would have produced merge-conflicting work against state that no longer required it. The implication is that backlog discipline needs a freshness step at slot-opening time: re-read the spec's premise against current codebase state before filing the bead. The cost of the check is small; the cost of letting a stale spec into the chain is a wasted chain run plus a duplicate-or-conflict cleanup. The differential gate validates code against baseline but cannot validate spec against reality. Two pack releases narrow the gap. A periodic story-spec drift reconciler — a cron-driven check that compares filed beads against current spec state and flags those whose premises have drifted — closes the already-filed subset of this surface: it does not prevent filing a stale spec, but it surfaces drift before downstream work depends on it. A `--kickoff` flag on the stories-bridge that issues kickoff alongside the bead-create step closes a different filing-side failure — the bead filed but never routed to a pool, invisible to the reconciler. What remains an operator-discipline gap is the authoring decision to file work that no longer needs to be done; the mechanical audit can report what has drifted but cannot prevent the choice to file from a stale draft.

**The graph-quality-to-throughput correlation.** Wall-clock time to complete a milestone depended heavily on the structure of the dependency graph. Milestones with parallel-friendly graphs (many independent stories) completed materially faster than milestones with sequential graphs (long dependency chains). This made graph construction a load-bearing engineering skill that the team had to develop.

**The pack-versus-rig boundary.** The architectural separation between pack content (project-agnostic discipline) and rig content (project-specific overlays) emerged as cleaner than initially designed. Specific Elder-domain content moved into rig-side overlays at `.claude/rules/project/`; the pack ships principle-only versions of modularity, DDD, TDD, and language rules. The composition pattern — rig wins on filename collision — made the boundary explicit and enforceable.

**The cost-distribution bimodality, and its compression as discipline thickens.** Early measurements showed a bimodal cost distribution: mechanical PRs in low single dollars and substantive stories in mid hundreds. The v2.9.4 cost-rollup fix, which enabled retroactive attribution of fifteen chains from a single working day, allowed a sharper look at the distribution under the current discipline. The new data shows the bimodality persists but the gap has compressed and the floor has risen. The smallest chains observed — a frontmatter backfill (CLEAN-001) and the VAL-003 trap test — ran at $62 and $91 respectively. Substantive refactors (REFACTOR-001 through REFACTOR-003) ran at $262 to $349. The nine mechanical docstring chains inside the VAL-001 burst landed in a tight $243 to $440 band, depending mostly on bounce-free convergence rather than story content. The floor lifted because the worker phase now does substantial reasoning even on small tasks (per-task anti-weakening, TDD discipline, Two-Hats commit shaping), and the per-chain phase overhead (tester, reviewer, documenter, finalizer) is largely fixed regardless of work content. The distribution is still useful for cost projection, but the relevant question has shifted from "is this a mechanical or a substantive story" to "how thick is the discipline that runs on every chain regardless of story content."

**The reviewer-phase cost driver: spec complexity, not work content.** The cost-distribution analysis above treated phase overhead as approximately fixed regardless of work content. A subsequent measurement refined this. Two stories of comparable shape and diff size — both adding a single property test to a small module — ran with materially different reviewer cost. The first, authored with eight acceptance criteria at the implementation-step level of detail, produced a $29 reviewer phase. The second, authored with six acceptance criteria stated as observable post-conditions, produced an $11 reviewer phase. The reviewer's per-AC audit work scales with how many ACs the spec enumerates, not with how much code the worker produced. The implication for spec authoring is structural: terser acceptance criteria, stated as observable post-conditions rather than as implementation steps, reduce reviewer cost without weakening the quality bar the reviewer enforces. The pattern is consistent with a worker-phase observation that exploration tokens — 95–98% of cached input on a typical worker run — come from spec-and-codebase reading rather than from generation. The spec, not the diff, sets the upper bound on chain cost.

**The pool-capacity floor under burst load.** Burst dispatch produces a different throughput characteristic than steady-state operation. When the number of concurrently dispatched chains exceeds the maximum pool size of any downstream phase, the chains queue and serialize through that phase. The phase's per-chain work continues at its normal pace; the additional wall-clock cost is queueing latency. In the VAL-001 burst, the documenter and finalizer pools at the SDLC pack's default max = 2 became the binding constraints — the eighth and ninth chains to reach those phases waited approximately seventeen and eighteen minutes for slots. Worker capacity at max = 8 was not the bottleneck despite being the first pool the chains hit. The implication for capacity planning is that pool sizing decisions are load-bearing for any workload arriving in bursts rather than as a steady stream. Pool maxima should be chosen with the largest expected burst in mind, not the average concurrent demand; over-provisioning is cheap because pool minimums are zero, but under-provisioning silently extends wall-clock without surfacing as a chain failure.

### 6.6 Limits of the case study

The case study has specific limits that should be acknowledged.

**Single project, single language, single domain.** The empirical evidence comes from one project written in Python in the quantitative trading domain. How the architecture generalizes to other languages, other domains, or other team compositions is not directly tested by this evidence. The theoretical arguments suggest generalization should work; empirical confirmation requires additional case studies.

**Single engineer.** The case study had one engineer authoring stories, constructing graphs, and reviewing chain output. Team dynamics — multiple engineers collaborating on graph design, multiple TPMs working with one engineering team, organizational coordination patterns — are not exercised by this case.

**Small sample of completed stories, growing.** Around 35 stories had shipped end-to-end through the chain by the 2026-05-16 thesis revision. As of 2026-05-25 the count is roughly 239 merged pull requests through the chain over a six-week window — a six-fold growth that strengthens the directional claims without yet reaching the size needed for a tight statistical bound. Trends in the data (zero tester-to-worker bounce rate across all merged stories, zero shortcut markers in the VAL-003 trap-test diff and in the nine VAL-001 burst diffs, bimodal cost distribution, pool-scaling-under-burst as a throughput shape) are suggestive but not statistically significant. Larger samples across additional rigs would strengthen or refute the patterns observed.

**Active development phase.** The Elder system is mid-development. The case study does not yet address how the architecture operates in steady-state maintenance phases, how it handles emergency response, or how it performs as the codebase ages.

These limits do not invalidate the architectural framework; they bound the empirical evidence that supports it.

---

## 7. Implications

This section examines what adopting the discipline-orchestration architecture implies for engineering roles, team composition, and product development at companies.

### 7.1 The changed role of the engineer

The engineer's role under this architecture differs in specific ways from the role under traditional development.

Before adoption: the engineer wrote code, reviewed other engineers' code, debugged production issues, attended design meetings, and split attention across many small implementation decisions. The engineer's leverage was the ability to type code that worked.

After adoption: the engineer designs systems, authors stories collaboratively with the TPM, builds the story dependency graph with the agent runtime, reviews chain output at milestones, and handles the architectural decisions and consequential changes that the chain is not trusted to make alone. The implementation work — writing code that implements a well-specified story — happens through the chain.

The boundary between chain-driven and engineer-driven work is not drawn at complexity. It is drawn at two structural properties. Work that can be completely specified — acceptance criteria in observable form, dependencies enumerated, sensitive files declared — belongs in the chain regardless of how complex the implementation is. Work that requires judgment about what to build and how the codebase should be shaped belongs to the engineer regardless of how small the immediate change is. The case study held the engineer's active role to two categories under this rule: authoring new stories and making architecture decisions. Six alert classes surface chain output the engineer must examine — architectural signals fired by the reviewer, invariant violations caught by the differential gate's audit checklists, recovery-loop escalations that exceeded the bounce counter, budget caps tripped on a single chain or on a milestone, spec-blockers the worker recognized and paused on, and trend anomalies surfaced by the cost-rollup and stall-detector observers. The engineer responds to these rather than monitoring the chain continuously, and outside the alert classes the chain runs without engineer attention.

The skills that matter most shift. Domain modeling, architecture, dependency-graph construction, and reading code at scale to verify chain output become the primary engineering activities. Typing speed, idiom recall, and tactical implementation patterns matter less because the chain handles them.

The engineer becomes more like a principal engineer than a senior engineer. One engineer plus the architecture can ship the work of a much larger traditional team, but only on work that fits the pattern: well-specified stories, well-structured dependency graphs, codebases that support the chain's discipline, architecture that has been thought through.

The engineer is not optional. Without an engineer who can decompose stories well, design good dependency graphs, and recognize when something has gone subtly wrong in chain output, the chain produces work no one can verify. The engineer is more important than before, and fewer of them are needed.

### 7.2 The changed role of the technical product manager

The TPM's role shifts in parallel ways.

Before adoption: the TPM gathered requirements, wrote stories, tracked tickets, ran standups, escalated blockers, managed the engineering team's attention. A large fraction of the role was coordination — keeping people aligned, keeping work flowing, removing impediments.

After adoption: the TPM gathers requirements, collaborates with the engineer on story authoring, and reviews delivered work at milestones. Standups become irrelevant — there is no daily attention-allocation problem when work proceeds through the chain. Ticket tracking is mostly automated; the task store reports what is running, what shipped, what halted. Blocker escalation still happens but is rarer and more substantive — the blockers are usually requirements ambiguities or design questions, not implementation issues.

The TPM spends more time at the front of the process — getting requirements right, structuring stories well, understanding what is being built. Less time in the middle on tracking and expediting. The same or more time at the back reviewing what was delivered against what was wanted. The role becomes more strategic and less operational.

### 7.3 Company-level implications

Companies that adopt the architecture experience changes in several areas.

**Engineering output scales with engineering judgment rather than headcount.** Adding an engineer under traditional development adds capacity for one more engineer's worth of typing. Adding an engineer under this architecture adds capacity for one more engineer's worth of design, story authoring, and dependency-graph construction, which can drive many parallel chains. The constraint on output becomes how fast the team can author well-specified stories and good graphs, not how fast it can type code.

**Quality enforcement moves earlier in the process.** The discipline applies at generation time rather than at review time. Anti-patterns get refused before they enter the codebase. The differential gate prevents work from shipping that weakens the bar. This is mechanical and consistent in a way human code review cannot match across many PRs.

**Team composition shifts toward seniority.** The team is smaller, more senior, more product-engaged. Junior engineers without enough experience to author well-specified stories or recognize subtle problems in chain output are less effective in this shape. Senior engineers and TPMs become more leveraged.

**Cost economics change shape.** Implementation labor cost decreases. Compute cost increases — running the chain costs real money in API calls. For most engineering work the trade is favorable, but the costs are different in shape and the company's cost model needs to reflect this.

**Risk posture changes.** Catastrophic individual mistakes become less likely because the discipline catches them. Subtle systemic drift becomes the new failure mode worth watching. The company needs to invest in periodic codebase audits, substantive milestone reviews, and feedback loops that turn caught problems into new rules. This is a different operational discipline than traditional development requires.

**Implementation cycle time compresses.** The work between "we agreed on the story" and "the change is in main" is much shorter. The graph's critical path becomes the floor; the chain's parallelism reduces wall-clock time toward that floor.

### 7.4 What the architecture is not

The argument deserves explicit limits.

This architecture is not autonomous software development. The engineer remains essential. The TPM remains essential. Business stakeholders remain essential. What changes is what each role spends time on. The architecture redistributes work; it does not eliminate roles.

This architecture is not a productivity multiplier with no tradeoffs. The discipline has to be maintained. The rules have to evolve as the codebase grows and as the language ecosystem shifts. The story authoring discipline has to be honored — vague stories produce wrong work fast, not right work fast. Teams that skip the front-end work get worse outcomes than traditional development, not better.

This architecture is not suitable for every kind of engineering work. Genuine architectural exploration, novel algorithmic work, deep debugging of opaque systems — these still require continuous engineer judgment in the loop. The architecture accelerates well-specified implementation work, which is most engineering work in production codebases but not all of it.

This architecture is not free of operational risk. Chains can produce subtly wrong output. The differential gate catches anti-weakening but not all forms of wrong. The engineering review at milestones catches more. Production monitoring catches what slips through. Each layer is necessary; no layer alone is sufficient.

---

## 8. Limits and future work

This thesis has specific limits beyond those of the case study itself.

### 8.1 Analytical limits

**Single architectural framework.** The thesis analyzes one specific architectural pattern — the discipline-orchestration composition with the dependency graph as the engineering artifact. Other architectural patterns exist or could be designed. The thesis does not establish that this pattern is optimal or that no other pattern could work; it establishes that this pattern works and identifies what makes it work.

**Limited theoretical formalization.** The thesis develops the discipline-orchestration architecture as a design framework but does not formalize it mathematically. A more rigorous theoretical treatment — formal models of agent behavior, queuing theory for the orchestration layer, information theory for the discipline layer — would strengthen the analysis but is beyond the scope of bachelor's-level work.

**Empirical anchoring through one case.** Section 6.6 acknowledged the limits of single-case empirical evidence. The thesis's conclusions are necessarily conditional on the patterns observed in the Elder case study; alternative cases might surface different patterns.

### 8.2 Open questions

Several questions remain open and would benefit from future investigation.

**Generalization across languages and domains.** The case study is in Python in a quantitative trading domain. How the architecture performs in JavaScript, Rust, or Go projects, or in domains like web services, mobile applications, or embedded systems, is not directly tested. Theoretical reasoning suggests the principles generalize; empirical confirmation requires additional case studies in different settings.

**Scaling beyond a single engineer.** Multi-engineer teams working under this architecture have not yet been observed. Questions include how multiple engineers collaborate on graph construction, how organizational coordination patterns interact with chain operation, and what team compositions optimize throughput.

**Steady-state operation.** The case study is in active development phase. How the architecture performs in maintenance phases (where work is more reactive than planned), in emergency response (where speed matters more than throughput), and in long-running codebases (where accumulated technical debt complicates the discipline) is not yet characterized.

**Cost economics at scale.** The cost-distribution bimodality observed in the case study suggests that cost projection requires understanding work-shape mix. How this distribution evolves as projects mature, as agent capabilities improve, and as model pricing changes is an open empirical question.

**Failure modes not yet observed.** The case study has not encountered certain failure modes that the architecture might be vulnerable to. Examples include large-scale agent failures during business-critical operations, adversarial conditions, and security implications of chain-driven development with elevated permissions.

**Operational observability gaps.** The case study surfaced a class of silent failure that the chain's own discipline does not catch: failure modes in the operator's tooling that surround the chain. The VAL-001 burst exposed three: a bridge script that silently mis-handled story-key formats outside the original convention and re-filed already-filed stories as duplicates; a polling watcher whose status-filter convention caused it to under-count completions; a cost-rollup observer that produced no rows on the production installation, leaving the burst's dollar cost initially unrecoverable. Each was recoverable manually, and all three have since been fixed in pack releases v2.9.3 and v2.9.4 — the bridge writeback and watch-script issues in v2.9.3, the cost-rollup observer's rig-routing and JSONL-mtime-slop bugs in v2.9.4. The cost-rollup fix shipped alongside a replay mechanism that backfilled the missed bead-closed events; the VAL-001 dollar number reported in Section 6.3 was recovered through that mechanism rather than measured live. The pattern is structurally distinct from chain-internal failures (which the differential gate, recovery loop, and bounce protocol already handle); it suggests that operational tooling deserves the same kind of mechanical audit at startup that the chain applies to code at merge time. Whether the architecture extends to systematic detection of these tooling-side failures — for example, a startup self-check that exercises each observer against a fixture event and asserts the expected side-effect — or whether they remain an operator-responsibility seam, is the open question the three observed failures sharpen.

The question is partially answered as of pack v2.13.0. Two cron orders shipped in that release — `sdlc-stall-detector` and `sdlc-order-stall-detector` — extend the differential gate's mechanical-audit pattern into the temporal domain. The first scans `in_progress` chain beads on a 15-minute cooldown and alerts via email when a bead's `current_step` has elapsed past its per-phase SLO. The second reads `gc order list` and `gc order history` and alerts when a cooldown-trigger order's last fire is older than `interval × 2` — the rebase-watcher-non-fire class of failure named earlier in this paragraph. Both throttle re-alerts per `(bead, phase)` or per order at four hours so the operator hears the stall once and stays silent until the situation changes. The mechanism is the same one the differential gate uses: a small body of code that knows what a healthy state looks like, runs against the world periodically, and refuses or alerts when reality deviates. Applied to operational state rather than to a code diff, it produces operational observability without requiring the operator to poll.

The residual open subclass is **silent-success-no-output**: a chain that completes by every measure the discipline gate sees but fails to produce an artifact the operator expects elsewhere. The case study surfaced one such failure on 2026-05-16. The pack #32 tech-debt auto-file feature was broken from its v2.11.0 ship until v2.12.1 fixed it — a path mismatch in the finalizer's bash invocation combined with a search-syntax bug in the dedup query — and produced zero GitHub issues during that window despite multiple chain runs emitting valid `tech_debt_trailer` JSON blocks. No chain failed. The differential gate passed. The reviewer's findings were recorded in `reviews/<bead>.md` as they should have been. The operator discovered the gap only by actively querying `gh issue list --label tech-debt`, expecting to find a queue and finding it empty. Detecting this subclass mechanically requires the operator-tooling-side equivalent of a property test that knows what artifacts should exist after a chain run and verifies their presence.

The picture sharpened across the pack releases that followed. The periodic story-spec drift reconciler ships as a cron order alongside `sdlc-stall-detector` and `sdlc-order-stall-detector`; it scans story specs against current bead state and flags drift before downstream work depends on it. A slop-reviewer phase landed as a shadow taste-pass that runs after the standard reviewer and writes a structured trailer of taste-side findings to the review file; the operator gets a second-opinion artifact whose absence is itself a signal. Per-bead audit-trail metadata extends the recovery-loop machinery so each bounce, escalation, and tier decision is recorded against the bead rather than disappearing into operator memory. The same operational lessons produced a stall-mode taxonomy the discipline pack uses to choose between detection mechanisms, treated below.

What remains genuinely open is narrower than the silent-success-no-output framing first suggested. The three pre-v2.13.0 silent failures named earlier in this paragraph — a bridge mis-handling story keys, a watcher under-counting completions, a cost-rollup observer producing no rows — all have mechanical detection in the current pack. The unmet case is now the one where the chain produces correct code and correct review files but fails to write an expected output downstream of the merge: a follow-up issue not opened, an external notification not sent, an audit-doc subsection not appended. The current audit surface cannot verify these mechanically because it does not know what downstream artifacts a given story is supposed to produce; the contract is implicit in the story spec rather than declared as a mechanical post-condition. Closing this subclass requires the story spec itself to enumerate its downstream artifacts so the chain or a follow-on observer can check for them. That is an open design question for future pack iterations.

**Chain failure-mode taxonomy.** Continued operation produced a structured taxonomy that sharpens §5.2's generic characterization of agent stalls. Worker-session failures divide into three shapes. Mode A is API-side overload — the agent runtime exits cleanly with a recognizable status, and a retry-wrapper around the agent runtime invocation handles it with exponential backoff. Mode B is per-turn duration cap — the agent runtime drains its conversation cleanly and exits, and a drain-acknowledged subscriber catches the upstream event and routes the bead back to its pool. Mode C is the same cap with the process kept alive at an input prompt waiting for operator continuation — a pane-state cron polls for the shape and submits a synthetic continuation turn. Chain-output residue divides similarly. Category A is scope ambiguity the worker recognizes during the run; the worker pauses and surfaces the question rather than guessing, and the bead routes through the operator's authorize queue. Category B is a violation the worker submits believing the work is complete, which the reviewer catches in audit; the bounce loop routes the bead back to the worker with the reviewer's complaint as context. The distinction between Category A and Category B matters because the recovery shapes differ — Category A is operator-mediated while Category B re-routes through the chain itself.

### 8.3 Future work directions

Several directions for future work follow from the analysis.

**Additional case studies.** Replicating the analytical framework against different projects, languages, domains, and team compositions would strengthen the empirical foundation.

**Theoretical formalization.** Mathematical models of the discipline layer (formalizing what audits constrain), the orchestration layer (formalizing what scheduling enables), and the composition (formalizing what the combined system guarantees) would deepen the analysis.

**Tooling for the engineering practice changes.** The story authoring discipline, the graph construction practice, and the milestone review pattern are skills that engineers must develop. Tooling that supports these skills — story validators, graph analyzers, milestone report generators — could accelerate adoption.

**Comparison studies.** Direct comparison between traditional development, agent-as-typist development, and architecture-driven development on equivalent project scopes would provide useful empirical grounding for adoption decisions.

**Scheduled fresh-context evaluation as institutional audit.** A complementary audit form emerged from practice in the case study: scheduled fresh-context evaluation of the pack's own accumulated changes by a reasoning agent with no prior conversation state. The mechanism is a cron-driven order that generates an evaluation prompt, invokes a fresh agent runtime, and writes the agent's findings to a per-date review file under `reviews/`. The pattern approximates the human engineering practice of stepping back from ongoing work to ask whether the accumulated decisions still cohere. Pre-tag deep-reasoning validation runs against each minor version before the tag is pushed and has caught design drift the unit tests and reviewer audits did not — falsely claimed test coverage, configuration variables computed but never consumed, framing claims that overstated what a mechanism guaranteed. Each evaluation produced actionable corrections that closed before the next release tag. Whether the pattern generalizes — scheduled self-evaluation as a first-class mechanism in disciplined agentic systems, complementing the front-end constraint and back-end audit of the §5.1 discipline layer with a periodic third audit form pitched at design drift rather than code drift — is an open question worth formal study.

---

## 9. Conclusion

This thesis has argued that safe agentic engineering at scale requires two mechanisms in combination: a discipline layer that produces trust by constraining what agents generate, and an orchestration layer that produces scale by coordinating many agents under uniform discipline. The argument proceeded through three movements — establishing why agents drift without external constraint, describing what each layer must provide, and examining a case study of these mechanisms in operation.

The contribution of this work is threefold. First, it presents the discipline-orchestration architecture as a framework for analyzing agent-driven software development systems. The framework identifies what each layer must provide and how the layers compose into a system whose properties exceed the sum of the parts. Second, it provides empirical anchoring through one substantial case study showing the framework in operation against a real codebase, with measured execution times, costs, and quality outcomes. Third, it identifies the engineering practice changes that adopting this approach requires, with explicit attention to what fails when the changes are skipped.

The architecture's two layers have working instantiations as of mid-2026: the SDLC discipline pack in its v2.x line, and the Gas City supervisor in its v1.x line. Both continue to evolve at pack-version pace; the current release and the version-history record live in each project's repository. The Elder Trading System case study examined in Section 6 has been operated against both, and the measured results reported in Sections 6.3 through 6.5 derive from that operation. Section 8 names the limits of what one case study can establish; further empirical work will determine how far the patterns generalize.

A second claim the thesis makes — implicit in the architecture, explicit only at this point — is that the engineer's work itself is changing. For decades, the engineer's primary leverage was the ability to type code that worked. Decisions were comparatively slow; code was comparatively fast. With capable agents operating under disciplined chains at scale, code becomes fast and decisions become the relative bottleneck. The engineer's leverage shifts toward decision quality: domain modeling, dependency-graph construction, and the ability to recognize when chain output is wrong at an architectural level rather than at a line level. The job is design and judgment, supported by tools that handle execution. The chain is a capable tool with specific failure modes, not an autonomous engineer; the engineer's role remains engineering, and the technical product manager's role remains product management. What changes is how each role connects to implementation work.

The contribution this thesis makes is the framework rather than the specific tools. The SDLC discipline pack and Gas City are one realization of the discipline-orchestration pattern; other realizations exist or will emerge. The unit of analysis the thesis offers — generation-time discipline composed with parallel orchestration under that discipline — is the framework future case studies can apply or refute. Whether the patterns observed in the Elder case study generalize across team compositions, language ecosystems, and organizational scales is the empirical work this thesis does not undertake. The framework supports that work; the case study anchors it; the practitioner community and subsequent research will establish its limits.

---

## References

Angermeir, F., et al. (2025). Reflections on the Reproducibility of Commercial LLM Performance in Empirical Software Engineering Studies. arXiv preprint arXiv:2510.25506. Accepted to ICSE 2026.

Beck, K. (2002). *Test Driven Development: By Example*. Addison-Wesley.

Elder, A. (2002). *Come Into My Trading Room: A Complete Guide to Trading*. John Wiley & Sons.

Elder, A. (2014). *The New Trading for a Living: Psychology, Discipline, Trading Tools and Systems, Risk Control, Trade Management*. John Wiley & Sons.

Evans, E. (2003). *Domain-Driven Design: Tackling Complexity in the Heart of Software*. Addison-Wesley.

Fowler, M. (1999). *Refactoring: Improving the Design of Existing Code*. Addison-Wesley.

Fowler, M. (2018). *Refactoring: Improving the Design of Existing Code* (2nd ed.). Addison-Wesley.

Freeman, S., & Pryce, N. (2009). *Growing Object-Oriented Software, Guided by Tests*. Addison-Wesley.

Gao, D., et al. (2024). AgentScope: A Flexible yet Robust Multi-Agent Platform. arXiv preprint.

Guo, L., et al. (2025). A Comprehensive Survey on Benchmarks and Solutions in Software Engineering of LLM-Empowered Agentic Systems. arXiv preprint arXiv:2510.09721.

Hong, S., et al. (2023). MetaGPT: Meta Programming for Multi-Agent Collaborative Framework. arXiv preprint.

Hutson, M. (2018). Artificial intelligence faces reproducibility crisis. *Science*, 359(6377), 725-726.

Jimenez, C. E., et al. (2024). SWE-bench: Can Language Models Resolve Real-World GitHub Issues? *International Conference on Learning Representations (ICLR)*.

Knutsen, J. (2026). Gas City. Open-source software repository, https://github.com/gastownhall/gascity.

Lin, J., et al. (2025). A Deep Dive Into Large Language Model Code Generation Mistakes: What and Why? arXiv preprint arXiv:2411.01414.

Liskov, B. (1972). A Design Methodology for Reliable Software Systems. *Proceedings of the Fall Joint Computer Conference*.

Liskov, B. (1988). Data Abstraction and Hierarchy. *ACM SIGPLAN Notices*, 23(5), 17-34. (Originally presented as OOPSLA 1987 keynote.)

Liu, J., et al. (2025). LLM-Based Multi-Agent Systems for Software Engineering: Literature Review, Vision, and the Road Ahead. *ACM Transactions on Software Engineering and Methodology*. Also available as arXiv:2404.04834.

Parnas, D. L. (1972). On the Criteria To Be Used in Decomposing Systems into Modules. *Communications of the ACM*, 15(12), 1053-1058.

Sells, C. (2026). Announcing Gas City 1.0. Sells Brothers blog, https://sellsbrothers.com/announcing-gas-city-1-0.

Sutton, R. (2019). The Bitter Lesson. Personal blog, http://www.incompleteideas.net/IncIdeas/BitterLesson.html.

Tang, Y., & Runkler, T. (2026). LLM-Based Agentic Systems for Software Engineering: Challenges and Opportunities. arXiv preprint arXiv:2601.09822.

Wang, J. J., & Wang, V. X. (2025). Assessing consistency and reproducibility in the outputs of large language models: Evidence across diverse finance and accounting tasks. arXiv preprint.

Wang, S., et al. (2025). AI-Generated Code Is Not Reproducible (Yet): An Empirical Study. arXiv preprint arXiv:2512.22387.

Yegge, S. (2026). Beads. Open-source software repository.

Yegge, S. (2026). Welcome to Gas City. Medium blog, https://steve-yegge.medium.com/welcome-to-gas-city-57f564bb3607.

---

*This thesis was prepared in mid-2026 and last revised 2026-05-25. Specific pack-version and Gas City build claims point to each project's repository for the current release. Quantitative anchors in the case study (PR counts, line counts, story counts) carry their measurement date so a future reader can place each number in time. Both layers continue to evolve.*
