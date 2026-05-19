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

# Sibling-import the classifier. The script is invoked directly via
# `python3 tech_debt.py file ...`, so the sibling lives in the same
# directory. Inject the script's parent into sys.path before the import
# so it resolves regardless of cwd.
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
from tech_debt_classifier import Verdict, classify_by_rules  # noqa: E402

SEVERITY_VALUES = frozenset({"low", "med", "high"})

# Trailer uses `med`; the classifier's rules use `medium`. Normalize at the
# boundary so the classifier's contract stays canonical and the trailer's
# wire format stays backward-compatible.
_SEVERITY_TO_CLASSIFIER = {"low": "low", "med": "medium", "high": "high"}

# Verdict → GitHub label string. The base `tech-debt` label is applied
# regardless; the verdict label is the routing signal a downstream auto-fix
# orchestrator (sub-stories B + C of pack #32) reads to pick eligible items.
_VERDICT_LABELS = {
    Verdict.AUTOFIX_SAFE: "tech-debt:autofix-safe",
    Verdict.NEEDS_HUMAN: "tech-debt:needs-human",
    Verdict.DEFER_TO_LLM: "tech-debt:defer-to-llm",
}
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


def _load_architecture(rig_root: Path) -> dict[str, Any] | None:
    """Load architecture.toml from the rig's `.claude/rules/project/` directory.

    Falls back to a top-level `architecture.toml` for rigs that haven't
    adopted the `.claude/rules/project/` layout. Returns the parsed dict
    or None if no config exists / can't be read.
    """
    candidates = (
        rig_root / ".claude" / "rules" / "project" / "architecture.toml",
        rig_root / "architecture.toml",
    )
    config_path = next((p for p in candidates if p.exists()), None)
    if config_path is None:
        return None
    try:
        with config_path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"[tech-debt] failed to read {config_path}: {exc}", file=sys.stderr)
        return None


def is_enabled(rig_root: Path) -> bool:
    """Return True if the rig has opted in via architecture.toml."""
    data = _load_architecture(rig_root)
    if data is None:
        return False
    section = data.get("tech_debt_automation", {})
    if not isinstance(section, dict):
        return False
    return bool(section.get("enabled", False))


def read_sensitive_files(rig_root: Path) -> list[str]:
    """Return the rig's sensitive-files allowlist from architecture.toml.

    Falls back to an empty list if the config or array is missing. The
    classifier treats an empty list as "no path matches sensitive" — a
    permissive default that lets the OTHER classifier rules (severity,
    line span, category set) carry the safety. Rigs that want strict
    sensitive-touch=0 behavior should populate the array.
    """
    data = _load_architecture(rig_root)
    if data is None:
        return []
    raw = data.get("sensitive_files", [])
    if not isinstance(raw, list):
        return []
    return [str(p) for p in raw if isinstance(p, str)]


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


_REQUIRED_LABELS: tuple[tuple[str, str, str], ...] = (
    ("tech-debt", "fbca04", "Reviewer-flagged tech debt (filed by SDLC pack automation)"),
    ("tech-debt:autofix-safe", "0e8a16", "Tech-debt safe for downstream auto-fix orchestration"),
    ("tech-debt:needs-human", "d93f0b", "Tech-debt that requires human triage"),
    (
        "tech-debt:defer-to-llm",
        "5319e7",
        "Tech-debt with ambiguous category; LLM fallback classification queued",
    ),
)


def ensure_label(gh_runner: Any = None) -> bool:
    """Ensure the `tech-debt` base label + the three verdict labels exist.

    `gh issue create --label X` fails on an unprovisioned label. This
    provisioner runs idempotently before any create call: it lists every
    label in the repo once (avoiding `--search`'s punctuation/operator
    edge cases per the v2.12.1 fix in `issue_exists`), filters in Python,
    and creates only the missing labels.

    The four labels are: the base `tech-debt` plus
    `tech-debt:autofix-safe` / `tech-debt:needs-human` /
    `tech-debt:defer-to-llm`. Verdict labels are the routing signal a
    downstream auto-fix orchestrator (pack #32 sub-stories B + C, not yet
    built) reads to pick eligible items.

    Returns True if all four are present or successfully created; False on
    a hard failure of any one. Callers should not proceed with create
    operations if this returns False.
    """
    runner = gh_runner or _run_gh
    listing = runner(["label", "list", "--json", "name", "--limit", "200"])
    existing: set[str] = set()
    if listing.returncode == 0:
        try:
            existing = {
                item["name"]
                for item in json.loads(listing.stdout or "[]")
                if isinstance(item, dict) and "name" in item
            }
        except (json.JSONDecodeError, TypeError):
            existing = set()

    for name, color, description in _REQUIRED_LABELS:
        if name in existing:
            continue
        created = runner(
            ["label", "create", name, "--color", color, "--description", description],
        )
        if created.returncode != 0 and "already exists" not in created.stderr.lower():
            print(
                f"[tech-debt] label create failed for {name!r}: {created.stderr.strip()}",
                file=sys.stderr,
            )
            return False
    return True


def issue_exists(title: str, gh_runner: Any = None) -> bool:
    """Return True if an open `tech-debt`-labeled issue with this title exists.

    Fetches all open `tech-debt` issues (up to 200, well above the realistic
    operational ceiling) and filters by exact title in Python. The `--search`
    flag was removed in v2.12.1 because GitHub search-query syntax treats
    `[`, `]`, `.`, `:`, and em dashes as operators or word boundaries — a
    title like `[tech-debt] EquityCalculator.snapshot uses datetime.now(UTC) — …`
    silently returns empty results, defeating dedup. The `tech-debt` label
    keeps the candidate set small enough that the Python filter is not a
    performance concern. On gh failure (network, missing token), returns
    False — better to risk a duplicate than to silently skip filing.
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
            "--json",
            "title",
            "--limit",
            "200",
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


def classify_item(item: dict[str, Any], sensitive_files: list[str]) -> Verdict:
    """Classify one trailer item, normalizing the severity at the boundary.

    The trailer's wire format uses `low/med/high`; the classifier's rules
    use `low/medium`. We translate at the call site so the classifier's
    canonical contract is unchanged.
    """
    normalized = dict(item)
    normalized["severity"] = _SEVERITY_TO_CLASSIFIER.get(item.get("severity", ""), "")
    return classify_by_rules(normalized, sensitive_files)


def create_issue(
    title: str,
    body: str,
    verdict: Verdict | None = None,
    gh_runner: Any = None,
) -> str | None:
    """Run `gh issue create`. Return the new issue URL on success, None on failure.

    Applies the base `tech-debt` label plus, when `verdict` is supplied,
    the corresponding `tech-debt:<verdict>` routing label. Both labels
    must be provisioned (via `ensure_label`) before this call.
    """
    runner = gh_runner or _run_gh
    cmd = ["issue", "create", "--title", title, "--label", "tech-debt", "--body", body]
    if verdict is not None:
        cmd.extend(["--label", _VERDICT_LABELS[verdict]])
    result = runner(cmd)
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

    # Provision the `tech-debt` base label + the three verdict routing
    # labels before any create call. gh issue create fails on an
    # unprovisioned label; provisioning is idempotent.
    if not ensure_label(gh_runner=gh_runner):
        print("[tech-debt] label provisioning failed; aborting", file=sys.stderr)
        return 0

    # Read the rig's sensitive-files allowlist once. The classifier needs
    # it per-item; reading the TOML once and passing the list keeps the
    # hot path zero-syscall.
    sensitive_files = read_sensitive_files(rig_root)

    filed = 0
    skipped_dup = 0
    skipped_invalid = 0
    by_verdict: dict[Verdict, int] = dict.fromkeys(Verdict, 0)
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
        verdict = classify_item(item, sensitive_files)
        body = build_issue_body(item, pr_url, review_rel)
        url = create_issue(title, body, verdict=verdict, gh_runner=gh_runner)
        if url:
            print(f"[tech-debt] filed [{verdict.value}]: {url}")
            filed += 1
            by_verdict[verdict] += 1

    verdict_summary = ", ".join(f"{v.value}={by_verdict[v]}" for v in Verdict)
    print(
        f"[tech-debt] summary: {filed} filed ({verdict_summary}), "
        f"{skipped_dup} dup, {skipped_invalid} invalid"
    )
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
