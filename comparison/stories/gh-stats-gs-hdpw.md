# gh-stats: time-to-merge histogram with json, table, markdown output

v2.0a validation story that stalled at documenter due to pool-routing bug. Bead `gs-hdpw` on rig `gh-stats`.
Filed: 2026-05-09T20:43:39Z. Status at capture: open, stuck at documenter routing. Worker and reviewer phases completed normally.

The stall was diagnosed and recorded in `reference_gascity_pool_routing.md` — the kickoff set both `--assignee=<rig>/sdlc-discipline.worker` and `gc.routed_to=<rig>/sdlc-discipline.worker` on the pool target, making the bead invisible to the pool's `EffectiveScaleCheck` (which filters `--unassigned`). Pool stayed at 0/5 for 13 minutes until the assignee was cleared.

This story is replayable against v2.0 as a regression test for the routing fix.

---

## Outcome

Users can run `gh-stats time-to-merge --repo OWNER/REPO` to see a histogram of merge durations (created → merged) for the most recently closed pull requests, output as JSON, table, or markdown.

## Acceptance criteria

- [ ] `gh-stats time-to-merge --help` exits 0 and lists `--repo` (required), `--limit N` (default 50), and `--format` (default `table`).
- [ ] `gh-stats time-to-merge --repo OWNER/REPO` queries the rig's GitHub API client for the last `--limit` closed pull requests, computes each one's merge duration as `(merged_at - created_at)` in seconds, and buckets the durations into four bins: `<= 1h`, `1h to 24h`, `24h to 168h` (one week), and `> 168h`.
- [ ] PRs with `merged_at = null` (closed without merging) are excluded from the histogram and counted separately as `unmerged`.
- [ ] `--format table` produces a readable text table with bucket label, count, and a horizontal bar of `#` characters scaled to the largest bucket.
- [ ] `--format markdown` produces a markdown table with the same columns.
- [ ] `--format json` produces a JSON object with one key per bucket plus `unmerged`, mapping bucket label to count.
- [ ] When the repository does not exist, the CLI exits non-zero with a domain message containing `not found`.
- [ ] Property: bucket counts plus `unmerged` always equal the input count for any input.

## Scope

**In:**
- `gh_stats/metrics/merge_time.py` — new metric module with `compute_merge_time_histogram(prs) -> Histogram` returning a typed dataclass with the bucket counts, and `list_closed_prs(client, repo, limit) -> list[ClosedPR]` querying `/repos/{repo}/pulls?state=closed`.
- `gh_stats/formats.py` — add `format_markdown` (markdown table) alongside the existing `format_json` and `format_table` from the prior story. The bar-rendering helper for `format_table` lives here too.
- `gh_stats/__main__.py` — register a `time-to-merge` subcommand with `--repo`, `--limit`, and `--format` options.
- `tests/test_merge_time.py` — acceptance tests covering each criterion. One property test using `hypothesis` that asserts bucket counts plus `unmerged` equal the input count for arbitrary integer-second durations.

**Out:**
- Time-zone normalization beyond what `datetime.fromisoformat` handles natively. The GitHub API returns UTC ISO 8601; that is good enough for v0.
- Per-author breakdowns of merge time (a different metric, not this one).
- Caching of API responses (the existing client does not cache; that is a separate story).
- Sub-second precision in the histogram (seconds is the unit).

## Sensitive files

None. Neither `gh-stats/CLAUDE.md` nor any auto-loading rule declares a sensitive-files list that overlaps with this story's surface.

## Notes

This is a v2.0 validation story for the `sdlc-discipline` pack. The chain runs through the new polecat-pattern architecture: a single worker session walks `mol-sdlc-work`'s seven steps in one conversation (no inter-session bead handoffs between plan, build, test, and self-review), then hands off to a reviewer pool (one of up to three), which hands off to the documenter (named, single-instance, on_demand). Per-bead worktree at `.gc/worktrees/gh-stats/sdlc/<bead-id>`.

Glance auto-merge is disabled on this rig; the chain stops at the documenter and leaves the branch ready for inspection.
