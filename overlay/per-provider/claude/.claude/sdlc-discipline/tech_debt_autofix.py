#!/usr/bin/env python3
"""SDLC tech-debt autofix orchestrator: spawn story specs from autofix-safe issues.

Reads open `tech-debt:autofix-safe` GitHub issues filed by the pack's tech-debt
automation (sub-A; see `tech_debt.py`) and converts each one into a story
spec at `<rig-root>/stories/EL-NNN-<slug>.md`, `status: ready`. Comments back
on the issue with a marker that makes re-runs idempotent.

This is the *consumer* of the routing labels that pack v2.15.0 introduced.
It does NOT sling the story. The operator slings manually after reviewing
the generated spec, because the spec is auto-generated from a structured
issue body and benefits from a sanity-check before chain time gets spent.

Subcommands
-----------
spawn      For each open `tech-debt:autofix-safe` issue, generate a story
           spec and write to `<rig-root>/stories/EL-NNN-<slug>.md`. Comment
           on the issue with a marker. With `--dry-run`, print specs to
           stdout without writing or commenting. `--issue N` restricts the
           run to one issue. `--limit N` caps the batch size (default 10).

Idempotency
-----------
The marker `<!-- tech-debt-autofix-spawned story=EL-NNN -->` is appended to
the orchestrator's reply comment on the issue. A second run reads each
issue's comments and skips any that already carry the marker.

Story id allocation
-------------------
Listing `<rig-root>/stories/EL-*.md` for `max(N)+1`. Within a single batch
the allocator also tracks ids it has just emitted (so dry-run doesn't
collide). A benign race exists if two orchestrator runs overlap — realistic
operational mode is one cron-fired run at a time, and a same-id collision
surfaces as a normal `git pull` conflict the operator resolves.

Triggering
----------
v0 is operator-invoked or cron-fired:

    python3 tech_debt_autofix.py spawn --rig-root /path/to/elder --dry-run
    python3 tech_debt_autofix.py spawn --rig-root /path/to/elder

Sub-B (LLM fallback for `tech-debt:defer-to-llm` items) is a separate
module; this orchestrator only touches `tech-debt:autofix-safe`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_STORY_ID_PATTERN = re.compile(r"^([A-Z]+)-(\d+)-")
_MARKER_TEMPLATE = "<!-- tech-debt-autofix-spawned story={story_id} -->"
_MARKER_PROBE = "<!-- tech-debt-autofix-spawned "
_TITLE_PREFIX = "[tech-debt] "


def parse_issue_body(body: str) -> dict[str, str] | None:
    """Extract structured fields from a tech-debt issue body.

    The body shape is fixed by `tech_debt.build_issue_body`. Returns None
    when any required field is absent — the orchestrator cannot generate
    a coherent spec from a partial body, and the realistic failure mode
    is a human-edited issue rather than a malformed-from-birth one.
    """
    fields: dict[str, str] = {}
    target = re.search(r"\| Target \| `([^`]+)` \(lines ([^)]+)\) \|", body)
    if target:
        fields["target_path"] = target.group(1)
        fields["target_lines"] = target.group(2)
    sev = re.search(r"\| Severity \| \*\*([^*]+)\*\* \|", body)
    if sev:
        fields["severity"] = sev.group(1).strip()
    cat = re.search(r"\| Category \| `([^`]+)` \|", body)
    if cat:
        fields["category"] = cat.group(1)
    sf = re.search(r"## Suggested fix\s*\n\s*\n(.*?)\n\s*\n## ", body, re.DOTALL)
    if sf:
        fields["suggested_fix"] = sf.group(1).strip()
    required = {"target_path", "target_lines", "severity", "category", "suggested_fix"}
    if not required.issubset(fields):
        return None
    return fields


def slug_from_summary(summary: str, max_chars: int = 60) -> str:
    """Kebab-case slug suitable for `stories/EL-NNN-<slug>.md`.

    Treats `_`, `.`, and other word-internal punctuation as word
    separators so identifiers like `_stdin_prompt` and `LLMDiaryResponse`
    survive as `stdin-prompt` and `llmdiaryresponse` rather than being
    smashed into single tokens. Truncates at the last `-` before
    `max_chars` so the slug doesn't end mid-word.
    """
    # Convert every non-alnum, non-dash char to a space so identifier
    # punctuation (`_`, `.`, parens, commas) becomes a word break rather
    # than getting silently smashed out. Then collapse whitespace into
    # single dashes.
    intermediate = re.sub(r"[^a-zA-Z0-9-]+", " ", summary).strip().lower()
    cleaned = re.sub(r"\s+", "-", intermediate)
    cleaned = re.sub(r"-+", "-", cleaned)
    if not cleaned:
        return "tech-debt"
    if len(cleaned) <= max_chars:
        return cleaned.rstrip("-")
    # Truncate at the last dash within the limit; if no dash is found
    # within max_chars, fall back to a hard cut.
    cut = cleaned[:max_chars]
    last_dash = cut.rfind("-")
    if last_dash > max_chars // 2:
        return cut[:last_dash]
    return cut.rstrip("-") or "tech-debt"


def strip_title_prefix(title: str) -> str:
    """Drop the leading `[tech-debt] ` prefix from an issue title."""
    return title[len(_TITLE_PREFIX) :] if title.startswith(_TITLE_PREFIX) else title


def next_free_story_id(stories_dir: Path, prefix: str = "EL") -> str:
    """Return the next free `<prefix>-NNN` (3-digit zero-padded).

    Scans `stories_dir` for `<prefix>-NNN-*.md`. Files whose numeric
    portion isn't an integer are ignored. Returns `<prefix>-001` when
    no matches exist.
    """
    max_n = 0
    for path in stories_dir.glob(f"{prefix}-*.md"):
        m = _STORY_ID_PATTERN.match(path.name)
        if m and m.group(1) == prefix:
            try:
                n = int(m.group(2))
            except ValueError:
                continue
            max_n = max(max_n, n)
    return f"{prefix}-{max_n + 1:03d}"


def render_story_spec(
    *,
    story_id: str,
    title: str,
    issue_number: int,
    issue_url: str,
    fields: dict[str, str],
) -> str:
    """Render a story-spec markdown body for one autofix-safe issue."""
    return (
        "---\n"
        f"story_id: {story_id}\n"
        f"title: {title}\n"
        "phase: 0\n"
        "build_item:\n"
        "deps: []\n"
        "parent:\n"
        "labels:\n"
        "  - tech-debt\n"
        "  - autofix-safe\n"
        "  - auto-spawned\n"
        "sensitive_files: []\n"
        "status: ready\n"
        "filed_as_bead:\n"
        "---\n"
        "\n"
        f"# {story_id} {title}\n"
        "\n"
        "## Outcome\n"
        "\n"
        f"Apply the suggested fix from autofix-safe-classified tech-debt issue "
        f"#{issue_number} at `{fields['target_path']}` "
        f"(lines {fields['target_lines']}). Category `{fields['category']}`, "
        f"severity `{fields['severity']}`.\n"
        "\n"
        "## Acceptance criteria\n"
        "\n"
        f"- [ ] Fix applied at `{fields['target_path']}:"
        f"{fields['target_lines']}`: {fields['suggested_fix']}\n"
        "- [ ] All tests pass: `uv run pytest tests/ -v`\n"
        "- [ ] Static checks pass: `uv run ruff check .` + `uv run mypy .` "
        "differentially clean against baseline\n"
        f"- [ ] PR closes the source issue via `Closes #{issue_number}` in "
        "the description\n"
        "\n"
        "## Scope\n"
        "\n"
        f"**In:** `{fields['target_path']}` plus directly-coupled tests under "
        "`tests/`.\n"
        "\n"
        f"**Out:** any change outside `{fields['target_path']}` not strictly "
        "required by the suggested fix. Drift in any path on the rig's "
        "sensitive-files allowlist is out-of-scope; the reviewer flags it.\n"
        "\n"
        "## Sensitive files\n"
        "\n"
        "None. The pack's tech-debt classifier filed this issue as "
        "`tech-debt:autofix-safe`, a verdict that excludes sensitive-file "
        "paths by classification rule.\n"
        "\n"
        "## Notes\n"
        "\n"
        f"Auto-spawned from GitHub issue #{issue_number} by the SDLC "
        "discipline pack's autofix-orchestrator (Pack #32 sub-C). The "
        "spec is generated, not authored — the operator reviews it before "
        "slinging.\n"
        "\n"
        "## Cross-references\n"
        "\n"
        f"- GitHub issue: {issue_url}\n"
    )


def already_spawned(comments: list[dict[str, Any]]) -> bool:
    """Return True if any issue comment carries the orchestrator marker."""
    return any(_MARKER_PROBE in (c.get("body") or "") for c in comments)


def _run_gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False)


def list_autofix_issues(
    gh_runner: Any = None,
    *,
    limit: int = 50,
    issue_number: int | None = None,
) -> list[dict[str, Any]]:
    """List open `tech-debt:autofix-safe` issues, or fetch one by number.

    Returns an empty list on `gh` failure or when no issues match. With
    `issue_number` set, returns a single-issue list when that issue is
    open and carries the routing label; empty otherwise.
    """
    runner = gh_runner or _run_gh
    if issue_number is not None:
        result = runner(
            [
                "issue",
                "view",
                str(issue_number),
                "--json",
                "number,title,body,url,state,labels,comments",
            ],
        )
        if result.returncode != 0:
            print(
                f"[autofix] gh issue view {issue_number} failed: {result.stderr.strip()}",
                file=sys.stderr,
            )
            return []
        try:
            issue = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return []
        if issue.get("state") != "OPEN":
            return []
        names = {label.get("name") for label in issue.get("labels", []) if isinstance(label, dict)}
        if "tech-debt:autofix-safe" not in names:
            return []
        return [issue]
    result = runner(
        [
            "issue",
            "list",
            "--label",
            "tech-debt:autofix-safe",
            "--state",
            "open",
            "--json",
            "number,title,body,url,comments",
            "--limit",
            str(limit),
        ],
    )
    if result.returncode != 0:
        print(
            f"[autofix] gh issue list failed: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return []
    try:
        items = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return items if isinstance(items, list) else []


def comment_issue(issue_number: int, body: str, gh_runner: Any = None) -> bool:
    """Post a comment to an issue. Returns True on success."""
    runner = gh_runner or _run_gh
    result = runner(["issue", "comment", str(issue_number), "--body", body])
    if result.returncode != 0:
        print(
            f"[autofix] gh issue comment {issue_number} failed: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return False
    return True


def _allocate_story_id(stories_dir: Path, used_ids: set[str]) -> str:
    """Allocate the next free id avoiding within-batch collisions."""
    candidate = next_free_story_id(stories_dir)
    n = int(candidate.split("-")[1])
    while f"EL-{n:03d}" in used_ids:
        n += 1
    story_id = f"EL-{n:03d}"
    used_ids.add(story_id)
    return story_id


def _process_one(
    issue: dict[str, Any],
    stories_dir: Path,
    *,
    dry_run: bool,
    gh_runner: Any,
    used_ids: set[str],
) -> tuple[str, str]:
    """Process one issue. Returns ('spawned', story_id) or ('skipped', reason)."""
    number = int(issue["number"])
    if already_spawned(issue.get("comments") or []):
        return ("skipped", f"#{number}: already spawned (marker present)")
    fields = parse_issue_body(issue.get("body") or "")
    if fields is None:
        return ("skipped", f"#{number}: body missing required fields")
    title = strip_title_prefix(issue.get("title") or "")
    slug = slug_from_summary(title)
    story_id = _allocate_story_id(stories_dir, used_ids)
    spec = render_story_spec(
        story_id=story_id,
        title=title,
        issue_number=number,
        issue_url=issue.get("url", ""),
        fields=fields,
    )
    target = stories_dir / f"{story_id}-{slug}.md"
    if dry_run:
        print(f"--- {target} ---")
        print(spec)
        return ("spawned", story_id)
    target.write_text(spec)
    comment_body = (
        f"Auto-spawned as story `{story_id}` at `stories/{target.name}` by "
        "the SDLC discipline pack's autofix-orchestrator (Pack #32 sub-C). "
        "Operator reviews the spec before slinging.\n\n"
        f"{_MARKER_TEMPLATE.format(story_id=story_id)}"
    )
    if not comment_issue(number, comment_body, gh_runner=gh_runner):
        target.unlink(missing_ok=True)
        used_ids.discard(story_id)
        return ("skipped", f"#{number}: comment failed; story file removed")
    return ("spawned", story_id)


def spawn_command(args: argparse.Namespace, gh_runner: Any = None) -> int:
    rig_root = args.rig_root.resolve()
    stories_dir = rig_root / "stories"
    if not stories_dir.exists():
        print(f"[autofix] stories dir not found: {stories_dir}", file=sys.stderr)
        return 1
    issues = list_autofix_issues(
        gh_runner=gh_runner,
        limit=args.limit,
        issue_number=args.issue,
    )
    if not issues:
        print("[autofix] no open autofix-safe issues; nothing to do")
        return 0
    spawned = 0
    skipped = 0
    used_ids: set[str] = set()
    for issue in issues:
        outcome, detail = _process_one(
            issue,
            stories_dir,
            dry_run=args.dry_run,
            gh_runner=gh_runner,
            used_ids=used_ids,
        )
        if outcome == "spawned":
            spawned += 1
            print(f"[autofix] spawned {detail} from #{issue['number']}")
        else:
            skipped += 1
            print(f"[autofix] skip {detail}")
    mode = " (dry-run)" if args.dry_run else ""
    print(f"[autofix] summary{mode}: {spawned} spawned, {skipped} skipped")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_spawn = sub.add_parser(
        "spawn",
        help="Spawn story specs from open tech-debt:autofix-safe issues",
    )
    p_spawn.add_argument("--rig-root", required=True, type=Path)
    p_spawn.add_argument("--dry-run", action="store_true")
    p_spawn.add_argument("--issue", type=int, default=None)
    p_spawn.add_argument("--limit", type=int, default=10)
    p_spawn.set_defaults(func=spawn_command)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
