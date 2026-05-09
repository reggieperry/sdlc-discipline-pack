# sdlc-discipline pack — version comparison artifacts

Direct-comparison data for evaluating pack architecture across versions. Each version is captured at a tagged commit with a baseline JSON and the verbatim story bodies that were run through the chain.

## Versions

- **v1.3** — five named-session phase agents (planner, implementor, tester, reviewer, documenter). Pack tagged `pack/v1.3` at csv2json commit `0812548`. Required the gascity#1893 workaround (kickoff-time named-session reset). Single chain at a time.
- **v2.0a** (interim, not shipped) — worker pool + reviewer pool + named documenter. Demonstrated polecat shape but stalled in the gh-stats validation due to pool-routing bug (`--assignee` set on pool target — see `reference_gascity_pool_routing.md`).
- **v2.0** — five pools (worker, tester, reviewer, documenter, finalizer); zero named sessions. Per-pool `gc.routed_to`-only routing convention. Unbounded story concurrency up to host and rate-limit ceilings.

## Files

- `v1.3-baseline.json` — per-phase wall-clock and session metrics for the two completed v1.3 chain runs
- `v2.0a-stall-record.json` — record of the gs-hdpw stall at documenter (single data point, partial chain)
- `stories/csv2json-cs-4b2q.md` — `--tab` flag story; canonical csv2json replay
- `stories/gh-stats-gs-id4.md` — `list-prs` story; canonical gh-stats replay
- `stories/gh-stats-gs-hdpw.md` — `time-to-merge` histogram story; the stall victim, replayable as a v2.0 validation
- `v2.0-results.json` — populated after Phase 4 of the v2.0 build plan (single-pilot replay + 10-story concurrent test)
- `v1.3-vs-v2.0.md` — populated after Phase 6, side-by-side comparison and ship/revert decision

## Replay convention

When replaying a story against v2.0, file the issue verbatim except for the `## Notes` section, which references the version under test. Substitute a v2.0 notes block on replay; keep `## Outcome`, `## Acceptance criteria`, `## Scope`, and `## Sensitive files` byte-identical so the chain's surface is unchanged.

The story files in `stories/` preserve the original `## Notes` block as written. Replay scripts read the file and override `## Notes` before filing.
