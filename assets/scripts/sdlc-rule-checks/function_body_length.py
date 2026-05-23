"""function_body_length — rule checker for pack #52 worker self-audit.

Walks every Python function (def + async def) introduced or modified by
the diff and flags any whose body exceeds a configurable line cap. Used
by the worker's self-audit phase when the story spec lists this rule in
its `self_audit_rules:` frontmatter.

The checker computes body length as `end_lineno - first_body_lineno + 1`
where first_body_lineno is the first statement's lineno (skipping any
leading docstring expression). This matches the conventional reading of
"function body length" as a measure of executable content, not the full
signature-to-end span.

Behavior:
  - Reads `git diff --name-only` against the baseline ref, filters to
    `*.py` paths inside the rig.
  - For each changed file, parses the working-tree contents (post-change
    state) via `ast.parse`. Pre-existing functions that don't appear in
    the changed line-range are NOT flagged — only added or touched
    functions count.
  - A function is "touched" if any line of its body span overlaps the
    diff's added-or-modified line ranges.

Args:
  --diff-range REF1..REF2   Required. Passed to `git diff --name-only`
                            and used by `git diff -U0` for the line-range
                            extraction.
  --max-lines N             Default 25. Cap; bodies strictly greater
                            than N are flagged.
  --paths-include PATTERN   Optional, repeatable. Glob pattern; only
                            files matching at least one pattern are
                            checked. Default: all `*.py` paths.

Exit codes:
  0   no new violations
  1   violations found; one JSON line per violation written to stdout
  2   invocation error (bad args, git failure)

Output (when violations exist):
  Newline-delimited JSON, one per violation:
    {"file": "core/foo.py", "function": "do_thing", "lines": 27,
     "max_lines": 25, "span": [42, 68]}

Closes pack issue #52 (initial checker).
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Flag Python functions whose body exceeds the line cap, "
        "scoped to functions touched by the diff."
    )
    parser.add_argument(
        "--diff-range",
        required=True,
        help="Git ref range, e.g. 'origin/main...HEAD' or 'origin/main..HEAD'.",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=25,
        help="Body line cap (default 25). Bodies strictly greater than N flag.",
    )
    parser.add_argument(
        "--paths-include",
        action="append",
        default=None,
        help="Glob to include (repeatable). Default: all *.py paths.",
    )
    return parser.parse_args(argv)


def changed_py_files(diff_range: str, paths_include: list[str] | None) -> list[str]:
    """Return *.py paths touched by the diff, filtered by include globs."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", diff_range],
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"function_body_length: git diff failed: {exc}\n")
        sys.exit(2)
    if proc.returncode != 0:
        sys.stderr.write(f"function_body_length: git diff exited {proc.returncode}\n")
        sys.stderr.write(proc.stderr)
        sys.exit(2)
    files = [line for line in proc.stdout.splitlines() if line.endswith(".py")]
    if paths_include:
        files = [f for f in files if any(fnmatch.fnmatch(f, p) for p in paths_include)]
    return files


# Extract added-or-modified line ranges from `git diff -U0` for one file.
# Each hunk header reads `@@ -OLD,COUNT +NEW,COUNT @@`; we want the NEW
# side (post-change line numbers). COUNT defaults to 1 when absent.
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_line_ranges(diff_range: str, file_path: str) -> list[tuple[int, int]]:
    """Return [(start, end), ...] of post-change line ranges touched by the diff."""
    try:
        proc = subprocess.run(
            ["git", "diff", "-U0", diff_range, "--", file_path],
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.SubprocessError:
        return []
    if proc.returncode != 0:
        return []
    ranges: list[tuple[int, int]] = []
    for line in proc.stdout.splitlines():
        m = _HUNK_RE.match(line)
        if m is None:
            continue
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        if count == 0:
            # Pure deletion — no NEW-side line touched on this file.
            continue
        ranges.append((start, start + count - 1))
    return ranges


def function_body_span(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int, int]:
    """Return (first_body_lineno, end_lineno, length) for a function node.

    Skips a leading docstring expression when computing first_body_lineno
    so a function whose body is a one-line docstring followed by 25 stmts
    is measured as 25 lines, not 26. `length` is the inclusive span.
    """
    body = node.body
    if not body:
        return node.lineno, node.lineno, 0
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
        and len(body) > 1
    ):
        first = body[1]
    end = body[-1].end_lineno or body[-1].lineno
    start = first.lineno
    return start, end, end - start + 1


def function_touches_changed_lines(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    changed_ranges: list[tuple[int, int]],
) -> bool:
    """True if the function's body span overlaps any changed range."""
    start, end, _ = function_body_span(node)
    return any(not (end < rs or start > re_) for rs, re_ in changed_ranges)


def check_file(path: Path, diff_range: str, max_lines: int) -> list[dict[str, object]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []

    changed_ranges = changed_line_ranges(diff_range, str(path))
    if not changed_ranges:
        return []

    violations: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not function_touches_changed_lines(node, changed_ranges):
            continue
        start, end, length = function_body_span(node)
        if length > max_lines:
            violations.append(
                {
                    "file": str(path),
                    "function": node.name,
                    "lines": length,
                    "max_lines": max_lines,
                    "span": [start, end],
                }
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    files = changed_py_files(args.diff_range, args.paths_include)
    if not files:
        return 0

    violations: list[dict[str, object]] = []
    for f in files:
        path = Path(f)
        if not path.exists():
            continue
        violations.extend(check_file(path, args.diff_range, args.max_lines))

    if not violations:
        return 0
    for v in violations:
        print(json.dumps(v))
    return 1


if __name__ == "__main__":
    sys.exit(main())
