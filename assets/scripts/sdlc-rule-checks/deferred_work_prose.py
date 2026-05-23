"""deferred_work_prose — rule checker for v2.30 worker self-audit (issue #123).

Flags scheduled-deferral comments in the diff that lack a tracking-issue
reference. The recurring failure mode this catches: a commit lands a
`# Removal in v2.30` (or `# TODO: refactor later`) comment that encodes
a future commitment in prose only, with no tracked artifact (GitHub
issue, bead, or story ID) to forget about later.

The pack's convention per `feedback_pack_75_76_glance_protocol.md`
(operator memory) is to file an issue or bead for cross-version
deferrals; the comment then references the artifact. This rule fires
when a comment matches a deferral pattern but no tracking reference
appears within ±2 lines.

Patterns flagged:
  - TODO, FIXME, XXX, HACK (case-insensitive)
  - follow-up / follow up
  - defer to / deferred to
  - removal scheduled for
  - remove in v<digit> / removal in v<digit>
  - v<digit>.<digit> removal

Tracking references recognized:
  - GitHub issue: `#123`
  - Elder story:  `EL-001`
  - Bead ID:      `el-abc123`

Opt-out: `# noqa: deferred-work` on the same line or within ±2 lines
suppresses the finding.

Scope:
  - `*.py` and `*.sh` files by default (most of the pack's source).
  - Restrict via `--paths-include` glob (repeatable) when needed.
  - The check operates on the diff's `+` lines only; pre-existing
    deferred-work comments are not retroactively flagged.

Args:
  --diff-range REF1..REF2   Required. Passed to `git diff -U0`.
  --paths-include PATTERN   Optional, repeatable glob. Default:
                            `*.py`, `*.sh`.

Exit codes:
  0   no new violations
  1   violations found; one JSON line per violation written to stdout
  2   invocation error (bad args, git failure)

Output (when violations exist):
  Newline-delimited JSON, one per violation:
    {"file": "core/foo.py", "line": 42, "comment": "# TODO refactor",
     "pattern": "TODO",
     "remediation": "file an issue or bead, then reference it in the comment (#NNN, EL-NNN, or el-xxx)"}

Closes pack issue #123.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

DEFERRAL_PATTERN = re.compile(
    r"#\s*(?P<kind>"
    r"TODO|FIXME|XXX|HACK"
    r"|follow[- ]up"
    r"|defer(?:red)?\s+to"
    r"|removal\s+scheduled\s+for"
    r"|remov(?:e|al)\s+in\s+v\d"
    r"|v\d+\.\d+\s+removal"
    r")",
    re.IGNORECASE,
)

TRACKING_PATTERN = re.compile(r"(#\d+|EL-\d+|el-[a-z0-9]+)")

NOQA_PATTERN = re.compile(r"#\s*noqa:\s*deferred-work\b")

DEFAULT_PATH_GLOBS = ("*.py", "*.sh")

REMEDIATION = "file an issue or bead, then reference it in the comment (#NNN, EL-NNN, or el-xxx)"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Flag deferred-work comments lacking a tracking reference, "
            "scoped to lines added by the diff."
        )
    )
    parser.add_argument(
        "--diff-range",
        required=True,
        help="Git ref range, e.g. 'origin/main...HEAD' or 'origin/main..HEAD'.",
    )
    parser.add_argument(
        "--paths-include",
        action="append",
        default=None,
        help="Glob to include (repeatable). Default: *.py, *.sh.",
    )
    return parser.parse_args(argv)


def _run_git(args: list[str]) -> str:
    """Run `git args` and return stdout. Exits 2 on git failure."""
    try:
        proc = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"deferred_work_prose: git {args} failed: {exc.stderr}", file=sys.stderr)
        sys.exit(2)
    return proc.stdout


def _matches_any_glob(path: str, globs: tuple[str, ...]) -> bool:
    return any(
        fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(Path(path).name, glob) for glob in globs
    )


def _added_lines_by_file(diff_range: str, path_globs: tuple[str, ...]) -> dict[str, set[int]]:
    """Parse `git diff -U0 <range>` and return {file_path: {added_lineno, ...}}.

    Uses unified diff with zero context lines so each added line's
    post-change line number is unambiguous from the hunk header.
    """
    diff_text = _run_git(["diff", "-U0", diff_range])
    result: dict[str, set[int]] = {}
    current_file: str | None = None
    current_lineno = 0

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            candidate = line[len("+++ b/") :]
            if _matches_any_glob(candidate, path_globs):
                current_file = candidate
                result.setdefault(current_file, set())
            else:
                current_file = None
            continue
        if line.startswith("+++ "):
            # Either "+++ /dev/null" (file deletion) or unprefixed; ignore.
            current_file = None
            continue
        if current_file is None:
            continue
        if line.startswith("@@"):
            # Hunk header: @@ -<old>,<count> +<new>,<count> @@
            m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if m:
                current_lineno = int(m.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            result[current_file].add(current_lineno)
            current_lineno += 1
        elif line.startswith("-"):
            # Removed lines don't advance the post-change line counter.
            continue
        else:
            # Context lines or other markers — advance with the post-change counter.
            current_lineno += 1
    return result


def _has_tracking_ref(window_text: str) -> bool:
    return bool(TRACKING_PATTERN.search(window_text))


def _has_noqa(window_text: str) -> bool:
    return bool(NOQA_PATTERN.search(window_text))


def _check_file(file_path: Path, added_linenos: set[int]) -> list[dict]:
    """Read the post-change file and flag deferral comments on added lines
    that lack a tracking ref within ±2 lines."""
    try:
        lines = file_path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[dict] = []
    for lineno in sorted(added_linenos):
        if lineno < 1 or lineno > len(lines):
            continue
        line = lines[lineno - 1]
        match = DEFERRAL_PATTERN.search(line)
        if not match:
            continue
        # ±2 line context window (1-indexed; clamp to file bounds).
        lo = max(1, lineno - 2)
        hi = min(len(lines), lineno + 2)
        window = "\n".join(lines[lo - 1 : hi])
        if _has_noqa(window):
            continue
        if _has_tracking_ref(window):
            continue
        findings.append(
            {
                "file": str(file_path),
                "line": lineno,
                "comment": line.strip(),
                "pattern": match.group("kind"),
                "remediation": REMEDIATION,
            }
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path_globs = tuple(args.paths_include) if args.paths_include else DEFAULT_PATH_GLOBS

    added_by_file = _added_lines_by_file(args.diff_range, path_globs)

    findings: list[dict] = []
    for file_str, linenos in added_by_file.items():
        if not linenos:
            continue
        findings.extend(_check_file(Path(file_str), linenos))

    if not findings:
        return 0

    for finding in findings:
        print(json.dumps(finding))
    return 1


if __name__ == "__main__":
    sys.exit(main())
