# gh-stats: list-prs --repo OWNER/REPO with json and table output

Original v1.3 first-solo-validation story for gh-stats. Bead `gs-id4` on rig `gh-stats`.
Filed: 2026-05-09T19:24:14Z. Closed: 2026-05-09T19:53:42Z. Local-only branch (no GitHub PR — gh-stats has no remote).

---

## Outcome

Users can run `gh-stats list-prs --repo OWNER/REPO` to print open pull requests for a repository as JSON to stdout, with an optional `--format table` for a tabular text rendering.

## Acceptance criteria

- [ ] `gh-stats list-prs --help` exits 0 and lists `--repo` (required) and `--format` (default `json`).
- [ ] `gh-stats list-prs --repo OWNER/REPO` exits 0 and emits a JSON array of open PR records, each with at least `number`, `title`, `author`, `created_at`, `url`.
- [ ] `gh-stats list-prs --repo OWNER/REPO --format table` exits 0 and emits a tabular text rendering with one header row and one row per PR.
- [ ] `--format` rejects values other than `json` and `table` with a usage error.
- [ ] When the repository does not exist, the CLI exits non-zero with a domain message containing `not found` (case-insensitive).
- [ ] Multi-page results are aggregated correctly. A test mocks two pages (the API uses `Link: <...>; rel="next"` for pagination) and asserts the combined result contains every PR from both pages.

## Scope

**In:**
- `gh_stats/metrics/prs.py` — new metric module with `list_prs(client, repo) -> list[PullRequest]` returning a typed dataclass with the fields above.
- `gh_stats/client.py` — extend with `paged_get(path) -> list[Any]` that follows the `Link: rel="next"` header until exhausted. The existing `get` method is unchanged.
- `gh_stats/formats.py` — add `format_table(items)` that produces a fixed-width text table for any list of dataclass records (column headers from field names; column widths from the longest value per column).
- `gh_stats/__main__.py` — register a `list-prs` subcommand with `--repo` and `--format` options.
- `tests/test_list_prs.py` — acceptance tests covering each criterion above. Reuse the `github_mock` and `client` fixtures from `tests/conftest.py`. The HTTP boundary stays mocked via `respx`; never hit the real GitHub API.

**Out:**
- Closed and merged PRs (open-only for v1; a `--state` flag is a future story).
- Authentication with `GITHUB_TOKEN` is not load-bearing here; the existing client already handles it transparently.
- Caching of API responses (a separate story will introduce `gh_stats/cache.py`).
- Markdown output format (added by a later story alongside `time-to-merge`).

## Sensitive files

None. Neither `gh-stats/CLAUDE.md` nor any auto-loading rule declares a sensitive-files list that overlaps with this story's surface.

## Notes

This is the first solo validation story for the `sdlc-discipline` pack against `gh-stats` — a second-pilot project distinct from `csv2json` in code shape (HTTP integration, paginated responses, multi-format output). The chain should pick up pack-supplied discipline rules from `<workspace>/csv2json/packs/sdlc-discipline/rules/` via the rig's `.claude/` symlinks, follow R/G/R discipline per `tdd.md`, keep function bodies under the 25-line cap from `python.md`, and produce a small clean PR (or a clean local branch — the rig's `SDLC_OPEN_PR_DEFAULT` is currently `false`, so the chain pushes nowhere external).

Glance auto-merge is also disabled on this rig; the chain stops at the documenter and leaves the branch ready for human inspection. That is intentional for the validation run — we want to read the worker's output before considering the pack's behavior on this project shape "validated."
