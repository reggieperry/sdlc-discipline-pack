# The operator deep-reasoning pattern

A pattern for delegating multi-file research, evaluation, and synthesis to a fresh-context Opus subagent. The subagent runs at maximum effort against a self-contained prompt and returns a single report. The operator reads that report and acts on it.

The pattern is useful precisely when the main session is poorly placed to answer the question — either because the task is too large to keep in working memory, or because the session has accumulated state that would bias the analysis. The fresh-context subagent has neither limitation: it starts empty, reads what the prompt provides, and produces a verdict that is uncontaminated by the conversation that produced the prompt.

This document is the public companion to the SDLC discipline pack's story-development methodology. It assumes a reader who has installed the pack, is operating a chain against a project, and now needs the second leverage point — the cross-PR reasoning capability the chain's per-PR review cannot supply.

## When to use

Six self-triggers cover the cases where the cost of the subagent is justified. Each names a class of work; the criterion for invocation is whether the work in front of the operator fits the class.

1. **About to draft an ADR, design doc, or multi-story design pack.** Pressure-test the model before the second draft, not after the fifth. The subagent reads the existing relevant files, holds them against the proposed direction, and either confirms the framing or names where it breaks. Drafting twice is cheaper than drafting five times against a thesis no one stress-tested.

2. **About to make a verdict-shaped, hard-to-reverse commit.** Tag pushes, pull-request merges, destructive operations against shared state. The cost of the subagent is small relative to the cost of reversing the action when the verdict turns out to be wrong.

3. **Defending a position under operator pressure across more than one message.** When the main session has staked a position and is being pushed against it, the second opinion breaks the tie between sycophancy (capitulating to pressure) and genuine correction (holding the position when the evidence supports it). The subagent's verdict resolves the deadlock with material the operator can act on rather than with one more round of debate.

4. **The question requires synthesizing across five or more files or repositories** held in working memory. The main session's context is finite; tasks that exceed it produce shallow synthesis. The subagent has full budget to read what it needs and return what it found.

5. **About to recommend an action based on a memory citing a specific identifier** — story ID, function name, commit SHA, build-plan item number — without having freshly verified the identifier exists and is current. Memories drift. The subagent verifies the identifier against current source before the recommendation is acted on.

6. **Audit-shape or review-shape questions whose answers are verdicts.** "Is this done?" "Are these issues real?" "Does this plan hold?" "Is this PR safe to merge?" The subagent's output spec is the verdict; the prompt's job is to give it the material it needs to reach one.

## When NOT to use

The subagent costs roughly two to fifteen minutes of wall-clock time and a non-trivial number of tokens. The cost is worth it when the question warrants it and wasted when it does not. Skip the pattern for:

- Single-file edits. The main session is the right tool; call its editing primitives directly.
- Lookups with a known target — file path, function name, identifier. A grep or a read is faster than spinning up a fresh agent to find what you already know how to find.
- Tasks doable in one to three tool calls. The agent's spin-up cost dominates at this scale.
- Implementation work. Delegating implementation is fine; delegating *understanding* is the trap. A prompt that says "based on your findings, fix the bug" pushes synthesis onto the agent rather than doing it yourself. Write prompts that prove you understood the problem — name the files, the line numbers, the specific thing to evaluate.

## How to invoke

The subagent is launched through whatever agent-invocation primitive the operator's runtime provides. Parameters that matter for the pattern:

| Parameter | Value | Why |
|---|---|---|
| Agent type | Generic Claude or specific reasoning type | Generic reasoning fits the default |
| Model | Opus (or the highest-capability available) | Maximum reasoning is the load-bearing capability |
| Background mode | False (run in foreground) | The result has to come back before the next step |
| Description | A 3- to 5-word verb phrase | For telemetry and display |
| Prompt | Self-contained, multi-paragraph | The agent has no context from your session |

Run in foreground unless there is genuinely independent work to do in parallel. Background mode hides the agent's progress and complicates merging its result back into the operator's reasoning.

## The prompt template

The prompt is the load-bearing artifact. The agent has no context from the operator's conversation — what the prompt says is what the agent has. A good prompt has six sections.

```
**You are evaluating [GOAL].** [One-sentence framing of the question.]

**Working environment**:
- Repo / path: [absolute paths]
- Key files: [the files the agent should know exist, with paths]
- External systems: [APIs, repositories, databases the agent should know about]

**Context**:
- [Background the agent needs to evaluate the question]
- [Recent events that frame why the question matters now]
- [Constraints — known-bad, known-good, prior verdicts]

**Steps**:
1. [Concrete first step — usually "locate / read X"]
2. [Concrete second step]
...
N. [Concrete final step — usually "synthesize and report"]

**Output**: a markdown report, [WORD COUNT] words, with these sections:
- [Section 1 name]: [what goes here]
- [Section 2 name]: [what goes here]
...

**Discipline**:
- [Specific anti-patterns to avoid for this task]
- [Verification standards — "verify every cited identifier"]
- [Scope limits — "do not modify files; evaluate only"]
```

The six pieces matter for different reasons:

- **Goal and framing** anchors the agent's reading of everything else. Without it, the agent infers from context and frequently infers wrong.
- **Working environment** saves the agent a discovery pass it would otherwise spend tool calls on. Naming the absolute paths up front is cheap insurance.
- **Context** is the part that gives the agent your judgment about why this matters. Without it, the agent gives generic doctrine answers. The reader of generic answers learns nothing the agent did not already know before the question.
- **Steps** bound the agent's exploration. Too few and it wanders; too many and you have written the report yourself.
- **Output spec** controls length and structure so the result is digestible when it comes back. The reader's bandwidth is finite; a 4,000-word report buries the verdict.
- **Discipline** names the failure modes specific to this task. The agent does not know which traps you have hit before; the prompt has to name them.

## Worked example: design-doc thesis evaluation

The shape applies whenever a design document needs validation against accumulated operational evidence. The prompt enumerates the document, the evidence, the question, and the output shape.

```
You are evaluating whether the design-doc thesis at docs/architecture.md
needs updating given recent operational data.

**Working environment**:
- Repo: /path/to/project
- Thesis doc: docs/architecture.md
- Recent incident reports: docs/postmortems/*.md
- Memory dir: ~/.claude/projects/<project>/memory/

**Context**:
- The thesis was written six months ago; the team has shipped 40+ features
  since.
- Three production incidents in the past month touched the load-bearing
  claims.
- Question: which thesis claims are reinforced, which are weakened, which
  gaps need filling.

**Steps**:
1. Read docs/architecture.md in full; extract the load-bearing claims.
2. Read each postmortem in docs/postmortems/*.md.
3. Cross-reference each claim against the operational data.
4. Classify each claim: REINFORCED / WEAKENED / GAP / REINFORCED-WITH-CAVEAT.
5. Propose concrete diff updates for the top 3-7 most impactful claims.

**Output**: a markdown report, 1000-1500 words, with these sections:
- Current thesis: 200-300 word summary of the load-bearing claims.
- What changed since the thesis was anchored: 200-300 words on the data.
- Per-claim assessment: numbered list with classifications.
- Proposed updates: 3-7 concrete diffs (quote current text, show
  replacement, justify in 1-2 sentences).
- Things NOT to change: 1-2 paragraphs naming tempting-but-premature
  revisions.

**Discipline**:
- Verify every identifier you cite — file paths, function names, line
  numbers, commit SHAs. Do not fabricate.
- The thesis doc is the current source of truth; memory is a snapshot.
  If memory contradicts the doc, the doc wins.
- Do not propose changes you cannot defend with concrete operational
  evidence.
- You are evaluating, not editing. Do not modify any files.
```

## Worked example: pull-request review

The shape applies whenever a pull request warrants a second opinion uncontaminated by the conversation that produced it. The prompt names the diff, the spec it implements, and the audit lens.

```
You are reviewing PR #N in repo X.

**Working environment**:
- Repo: /path/to/repo
- PR branch: feature/abc, base main
- Story spec: stories/STORY-ID.md (read first)
- Related Protocols / types the implementation extends: [list]

**Context**:
- The story implements [one-sentence description].
- The previous PR in this area was #M which introduced [...].
- Sensitive files list: .claude/rules/project/sensitive-files.md.

**Steps**:
1. `gh pr diff N` and `gh pr view N --json files,body` — read the full
   diff and metadata.
2. Read stories/STORY-ID.md — extract the acceptance criteria.
3. Verify each acceptance criterion against the diff.
4. Apply the slop rubric: hallucinated APIs, silent failure, test
   mirroring, scope creep, defensive impossibility, over-commenting,
   type escape hatches.
5. Run differential gates: ruff, mypy, tests on changed files only.
6. Sensitive-files check.
7. Recommend: merge / changes / human-required.

**Output**: a markdown review report, 500-800 words, with:
- Verdict: GLANCE-MERGE / REVIEW-ENCOURAGED / HUMAN-REQUIRED / CHANGES.
- Spec-coverage table (criterion + PASS/FAIL + evidence).
- Slop trailer (findings by tier, with file:line).
- Differential gate results.
- Recommendation: explicit "merge as-is" or "merge after [N] changes."

**Discipline**:
- Cite file:line for every observation. Never paraphrase-and-quote;
  only quote what you read via tool call.
- Verify column names and Protocol signatures against actual source,
  not against the PR description's claims.
- Do not invent identifiers. If something is uncertain, grep first.
```

## Discipline rules

Six rules apply in every prompt. They are the failure modes the pattern has surfaced often enough to be worth codifying.

1. **Never delegate understanding.** Phrasing like "based on your findings, fix the bug" or "based on the research, implement it" pushes synthesis onto the agent instead of doing it yourself. Write prompts that prove you understood the problem: include the file paths, the line numbers, what specifically to evaluate, what specifically not to touch. The agent's job is to execute the evaluation you designed, not to design the evaluation.

2. **Identifier discipline.** Tell the agent to verify every named identifier — file path, function name, line number, commit SHA — against actual source via tool call. LLMs hallucinate plausible-looking names when verification would require an extra step. The prompt has to make verification cheaper than fabrication.

3. **Output cap.** Specify word count. Without it, agents produce verbose reports that are harder to act on than tight ones. A 500-word report with a clear verdict beats a 2,000-word report with a buried one.

4. **Scope fence.** Tell the agent explicitly what it should and should not do. "Evaluate only, do not modify files." "Propose diffs but do not commit." "Recommend, do not act." The fence prevents the agent from over-helping into territory the operator wanted to keep.

5. **Cite evidence.** Tell the agent every claim in the report must be grounded in something it observed via tool call. Generic doctrine answers are the failure mode the rule prevents. If the prompt does not require evidence, the report will contain assertions the operator cannot verify.

6. **Verify before acting.** When the agent is delegated work that changes state — closing issues, writing files, opening PRs — tell it to dry-run or verify first. "List what you would do before doing it" is cheap insurance against the agent acting on a misreading of the prompt.

## Tone notes

The agent reads what the prompt provides; it does not infer warmth or urgency from intonation. Two tonal habits earn their place anyway.

Brief the agent like a smart colleague who has just walked into the room. Explain what you are trying to accomplish and why. Describe what you have already learned, what you have already ruled out, what you suspect. Terse command-style prompts produce shallow, generic work; conversational briefing produces work that engages the actual question.

If the agent's task is to produce a verdict (FILE NOW / DEFER, MERGE / HOLD, REAL / FALSE-POSITIVE), put the verdict shape in the output spec. The agent will reach for the named shape rather than hedging across a spectrum the prompt did not constrain.

## How this compares to direct main-agent work

|  | Main agent | Reasoning subagent |
|---|---|---|
| Context budget | Shared with the conversation | Fresh — full budget for the task |
| Latency | Inline | 2-15 minutes for substantial tasks |
| Cost | Per-tool-call against the session | Full subagent run (typically a few dollars) |
| Bias | Carries session context | Reads what the prompt says, nothing else |
| Output | Streams through chat | Single report at the end |

The latency and cost are real. Use the pattern when you would rather see one good answer than five quick partial ones. For everything else, the main agent's per-tool-call mode is the right shape.

## After the agent returns

State the agent's verdict in two or three sentences and name what changed in the plan as a result. If nothing changed, that is also reportable — the agent confirmed the existing direction rather than corrected it. Both outcomes are useful; both are worth recording.

The agent's report is itself a verified artifact: it cites files and lines it read, and the operator can re-verify any claim by following the citation. Treat the report as evidence, not as fiat. The verdict carries weight because the underlying observations carry weight; the verdict without the observations is no better than the main session's guess.

## How this composes with the chain

The SDLC discipline pack's chain reviews each pull request against the rules and the differential gate. That review is per-PR by construction. Some questions are not per-PR — they span multiple merged changes, multiple specs, or multiple repositories. The chain cannot answer those questions because it does not see across the boundary.

The deep-reasoning subagent fills the cross-PR gap. Audits, architectural verdicts, multi-spec dependency checks, and "is this plan still load-bearing?" questions all benefit from a subagent that walks the problem from scratch. The chain and the subagent are complementary: the chain enforces consistency at the level of each change; the subagent enforces coherence at the level of the system the changes accumulate into.

New operators start using the subagent on the six self-triggers above. As experience accumulates, the operator's intuition for when the pattern earns its cost sharpens, and the explicit triggers become a checklist for unfamiliar situations rather than a constant prompt.
