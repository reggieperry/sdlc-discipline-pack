#!/usr/bin/env python3
"""SDLC tech-debt automation: file reviewer tech-debt findings as GitHub issues.

Reads the JSON `tech_debt_trailer` block from a review file and creates GitHub
issues in the rig's repo, one per finding. Dedups against existing open
tech-debt issues by title. The mechanism is the *capture* half of the
tech-debt-to-bead arc — humans triage the resulting issues afterward.

Subcommands
-----------
file       Parse a review file's `tech_debt_trailer` block and call
           `gh issue create` per non-duplicate item. Returns 0 on success
           or no-op; non-zero only if `gh` itself fails irrecoverably.

Configuration
-------------
The rig opts in via its `architecture.toml`:

    [tech_debt_automation]
    enabled = true

Default off. The opt-in keeps the v2.11 rollout safe — rigs that pull the
new pack version see no behavior change until they flip the gate.

Trailer schema
--------------
The reviewer emits a JSON array at the bottom of the review file, fenced as:

    ```json tech_debt_trailer
    [
      {
        "target_path": "core/coordinator.py",
        "target_lines": "267-282",
        "severity": "med",
        "category": "docstring-vs-code",
        "summary": "Docstring claim doesn't match code behavior",
        "suggested_fix": "Tighten docstring or add flag-check in _walk_stages"
      }
    ]
    ```

All six fields are required. `severity` must be `low`, `med`, or `high`.
`category` is free-text (kebab-case preferred). Absence of the trailer = no
issues filed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

SEVERITY_VALUES = frozenset({"low", "med", "high"})
REQUIRED_FIELDS = (
    "target_path",
    "target_lines",
    "severity",
    "category",
    "summary",
    "suggested_fix",
)

# Match a fenced JSON block whose info-string includes `tech_debt_trailer`.
# Non-greedy capture of body; DOTALL so `.` matches newlines.
_TRAILER_PATTERN = re.compile(r"```json\s+tech_debt_trailer\s*\n(.*?)\n```", re.DOTALL)


def parse_trailer(review_path: Path) -> list[dict[str, Any]]:
    """Extract the tech_debt_trailer JSON array from a review file.

    Returns an empty list if the file is missing, the trailer fence is
    absent, the JSON is malformed, or the top-level value is not a list.
    Errors are logged to stderr but do not raise — a malformed trailer
    must not block the finalizer.
    """
    if not review_path.exists():
        return []
    content = review_path.read_text()
    match = _TRAILER_PATTERN.search(content)
    if not match:
        return []
    raw = match.group(1)
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[tech-debt] trailer JSON malformed in {review_path}: {exc}", file=sys.stderr)
        return []
    if not isinstance(items, list):
        print(f"[tech-debt] trailer is not a list in {review_path}", file=sys.stderr)
        return []
    return items


def validate_item(item: Any) -> str | None:
    """Return None if the item is structurally valid; else a reason string."""
    if not isinstance(item, dict):
        return "not a JSON object"
    missing = [k for k in REQUIRED_FIELDS if k not in item]
    if missing:
        return f"missing fields: {', '.join(missing)}"
    severity = item["severity"]
    if severity not in SEVERITY_VALUES:
        return f"severity must be one of {sorted(SEVERITY_VALUES)}; got {severity!r}"
    summary = item["summary"]
    if not isinstance(summary, str) or not summary.strip():
        return "summary must be a non-empty string"
    return None


def is_enabled(rig_root: Path) -> bool:
    """Return True if the rig has opted in via architecture.toml.

    Search order matches the pack convention: `.claude/rules/project/`
    (where `sensitive_files`, `domain_model_files`, and `protocol_modules`
    live) takes precedence over a top-level `architecture.toml`. The
    top-level path is retained as a fallback for rigs that haven't
    adopted the `.claude/rules/project/` layout.
    """
    candidates = (
        rig_root / ".claude" / "rules" / "project" / "architecture.toml",
        rig_root / "architecture.toml",
    )
    config_path = next((p for p in candidates if p.exists()), None)
    if config_path is None:
        return False
    try:
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"[tech-debt] failed to read {config_path}: {exc}", file=sys.stderr)
        return False
    section = data.get("tech_debt_automation", {})
    if not isinstance(section, dict):
        return False
    return bool(section.get("enabled", False))


def build_issue_body(item: dict[str, Any], pr_url: str, review_path_rel: str) -> str:
    """Compose the GitHub issue body for one tech-debt item."""
    return (
        "## Tech-debt finding\n"
        "\n"
        "| Field | Value |\n"
        "| --- | --- |\n"
        f"| Target | `{item['target_path']}` (lines {item['target_lines']}) |\n"
        f"| Severity | **{item['severity']}** |\n"
        f"| Category | `{item['category']}` |\n"
        "\n"
        "## Suggested fix\n"
        "\n"
        f"{item['suggested_fix']}\n"
        "\n"
        "## Source\n"
        "\n"
        f"- Parent PR: {pr_url}\n"
        f"- Review file: `{review_path_rel}`\n"
        "\n"
        "Filed automatically by the SDLC discipline pack's tech-debt automation. "
        "Triage by adding context, closing as won't-fix, or routing through the chain.\n"
    )


def ensure_label(gh_runner: Any = None) -> bool:
    """Ensure the `tech-debt` label exists in the rig's GitHub repo.

    `gh issue create --label tech-debt` fails on a repo where the label
    has not been pre-created, with `could not add label: 'tech-debt' not
    found`. This idempotent provisioner runs before any create call: it
    checks for the label, creates it with a neutral color if absent, and
    treats a race-condition "already exists" failure as success.

    Returns True on success or already-exists; False on a hard failure
    (network down, no auth). Callers should not proceed with create
    operations if this returns False.
    """
    runner = gh_runner or _run_gh
    listing = runner(["label", "list", "--search", "tech-debt", "--json", "name"])
    if listing.returncode == 0:
        try:
            labels = json.loads(listing.stdout or "[]")
        except json.JSONDecodeError:
            labels = []
        if any(item.get("name") == "tech-debt" for item in labels):
            return True
    # Label not present (or list failed); attempt create. The create call
    # is the source of truth — if it succeeds or returns "already exists",
    # we proceed.
    created = runner(
        [
            "label",
            "create",
            "tech-debt",
            "--color",
            "fbca04",
            "--description",
            "Reviewer-flagged tech debt (filed by SDLC pack automation)",
        ],
    )
    if created.returncode == 0:
        return True
    # gh returns non-zero for "already exists" — treat that as success.
    if "already exists" in created.stderr.lower():
        return True
    print(f"[tech-debt] label create failed: {created.stderr.strip()}", file=sys.stderr)
    return False


def issue_exists(title: str, gh_runner: Any = None) -> bool:
    """Return True if an open `tech-debt`-labeled issue with this title exists.

    The search uses `gh issue list --label tech-debt --state open --search`
    and compares exact title matches in the returned set. On gh failure
    (network, missing token), returns False — better to risk a duplicate
    than to silently skip filing.
    """
    runner = gh_runner or _run_gh
    result = runner(
        [
            "issue",
            "list",
            "--label",
            "tech-debt",
            "--state",
            "open",
            "--search",
            title,
            "--json",
            "title",
        ],
    )
    if result.returncode != 0:
        print(f"[tech-debt] gh issue list failed: {result.stderr.strip()}", file=sys.stderr)
        return False
    try:
        existing = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return False
    return any(i.get("title") == title for i in existing)


def create_issue(title: str, body: str, gh_runner: Any = None) -> str | None:
    """Run `gh issue create`. Return the new issue URL on success, None on failure."""
    runner = gh_runner or _run_gh
    result = runner(
        ["issue", "create", "--title", title, "--label", "tech-debt", "--body", body],
    )
    if result.returncode != 0:
        print(f"[tech-debt] gh issue create failed: {result.stderr.strip()}", file=sys.stderr)
        return None
    url = result.stdout.strip()
    return url or None


def _run_gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Default `gh` subprocess runner."""
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False)


def file_command(args: argparse.Namespace, gh_runner: Any = None) -> int:
    """Top-level file subcommand. Returns process exit code."""
    rig_root = args.rig_root.resolve()
    if not is_enabled(rig_root):
        print("[tech-debt] disabled in rig config; skipping")
        return 0
    review_path = args.review_file.resolve()
    items = parse_trailer(review_path)
    if not items:
        print(f"[tech-debt] no trailer found in {review_path}; skipping")
        return 0
    pr_url = args.pr_url or "(no PR URL)"
    try:
        review_rel = str(review_path.relative_to(rig_root))
    except ValueError:
        review_rel = str(review_path)

    # Provision the `tech-debt` label before any create call. gh issue
    # create fails on an unprovisioned label; provisioning is idempotent.
    if not ensure_label(gh_runner=gh_runner):
        print("[tech-debt] label provisioning failed; aborting", file=sys.stderr)
        return 0

    filed = 0
    skipped_dup = 0
    skipped_invalid = 0
    for item in items:
        reason = validate_item(item)
        if reason is not None:
            print(f"[tech-debt] skipping invalid item: {reason}", file=sys.stderr)
            skipped_invalid += 1
            continue
        title = f"[tech-debt] {item['summary']}"
        if issue_exists(title, gh_runner=gh_runner):
            print(f"[tech-debt] dup: {title}")
            skipped_dup += 1
            continue
        body = build_issue_body(item, pr_url, review_rel)
        url = create_issue(title, body, gh_runner=gh_runner)
        if url:
            print(f"[tech-debt] filed: {url}")
            filed += 1

    print(f"[tech-debt] summary: {filed} filed, {skipped_dup} dup, {skipped_invalid} invalid")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_file = subparsers.add_parser(
        "file", help="Parse a review file's trailer and file GitHub issues"
    )
    p_file.add_argument(
        "--review-file", required=True, type=Path, help="path to reviews/<bead-id>.md"
    )
    p_file.add_argument(
        "--rig-root",
        required=True,
        type=Path,
        help="path to the rig root (architecture.toml lives here)",
    )
    p_file.add_argument("--pr-url", default="", help="parent PR URL for the issue body (optional)")
    p_file.set_defaults(func=file_command)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
