#!/usr/bin/env python3
"""SDLC differential gate: capture and compare static-analysis baselines.

Subcommands
-----------
baseline   Run ruff, mypy, and the suppression/pytest-weakening scans against
           the current working tree. Caller has typically checked out the
           merge-base SHA in a scratch worktree before invoking. Output is a
           directory of JSON files keyed for diff to consume.

diff       Run the same scans on the current branch tip and compare against a
           previously captured baseline directory. Emits a JSON verdict:
           pass, advisory, or fail. Exits 0 on pass/advisory, 1 on fail.

Identity model
--------------
Per-error identity is the (file, error-code) pair. Message text is dropped
because mypy and several ruff codes embed type names or other contextual
detail that legitimately changes across edits without representing a new
defect. Per-(file, code) multiset comparison catches swaps within a file
between distinct error codes; within-(file, code) swaps are a known v2.4
gap (would require AST-anchored identity).

Renames are tracked via `git diff --name-status -M` and applied to baseline
file paths before comparison.

Cross-file relocations whose global-(code) net is non-positive are
downgraded to advisories rather than blocks: a worker who moves a class
from a.py to b.py without changing its error count gets a soft signal
rather than an automatic bounce.

Suppressions (#type:ignore, #noqa, #pyright:ignore) are tracked
separately. Targeted suppressions count under their specific code keys;
blanket forms count under a "BLANKET" key. Adding a blanket suppression
where a targeted one existed registers as a new key, not a count
preservation, so scope-broadening is caught.

Pytest-anti-weakening tracks per-file count of pytest skip/xfail/skipif
markers (must not increase) and assert keywords (must not decrease per
file across rename map).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# --- Static-analysis runners --------------------------------------------------


def _rel(path: str, root: Path) -> str:
    """Return path relative to root if path is under root; else return as-is."""
    try:
        return str(Path(path).resolve().relative_to(root))
    except ValueError:
        return path


def run_ruff(root: Path) -> Counter:
    """Run ruff with JSON output. Returns Counter[(file, code)] keyed by repo-relative paths."""
    proc = subprocess.run(
        ["uv", "run", "ruff", "check", ".", "--output-format=json"],
        capture_output=True,
        text=True,
        check=False,
    )
    findings: list[dict] = []
    if proc.stdout.strip():
        try:
            findings = json.loads(proc.stdout)
        except json.JSONDecodeError:
            sys.stderr.write(
                "sdlc-gate: ruff produced non-JSON output; treating as no findings\n"
            )
            findings = []
    counter: Counter = Counter()
    for f in findings:
        path = _rel(f.get("filename", ""), root)
        code = f.get("code", "?")
        counter[(path, code)] += 1
    return counter


_MYPY_LINE = re.compile(r"^(?P<path>[^:]+?):\d+:.*?error:.*?\[(?P<code>[^\]]+)\]\s*$")


def run_mypy(root: Path) -> Counter:
    """Run mypy and parse the [error-code] suffix. Returns Counter[(file, code)] keyed by repo-relative paths."""
    proc = subprocess.run(
        ["uv", "run", "mypy", ".", "--show-error-codes", "--no-error-summary"],
        capture_output=True,
        text=True,
        check=False,
    )
    counter: Counter = Counter()
    for line in proc.stdout.splitlines():
        m = _MYPY_LINE.match(line)
        if m:
            counter[(_rel(m.group("path"), root), m.group("code"))] += 1
    return counter


# --- Suppression scan ---------------------------------------------------------


_SUPPRESSION_PATTERNS: list[tuple[re.Pattern, callable]] = [
    (
        re.compile(r"#\s*type:\s*ignore\[(?P<code>[^\]]+)\]"),
        lambda m: f"type:ignore[{m.group('code').strip()}]",
    ),
    (
        re.compile(r"#\s*type:\s*ignore(?!\[)"),
        lambda m: "type:ignore[BLANKET]",
    ),
    (
        re.compile(r"#\s*noqa:\s*(?P<code>[A-Za-z0-9, ]+)"),
        lambda m: f"noqa[{m.group('code').strip()}]",
    ),
    (
        re.compile(r"#\s*noqa(?![:\w])"),
        lambda m: "noqa[BLANKET]",
    ),
    (
        re.compile(r"#\s*pyright:\s*ignore"),
        lambda m: "pyright:ignore",
    ),
]


def _walk_python(root: Path):
    """Yield .py files under root, skipping hidden dirs, .venv, build dirs."""
    skip = {
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".gc",
        "build",
        "dist",
        ".git",
    }
    for path in root.rglob("*.py"):
        if any(
            part in skip or part.startswith(".")
            for part in path.relative_to(root).parts[:-1]
        ):
            continue
        yield path


def scan_suppressions(root: Path) -> Counter:
    """Scan all .py files for suppression directives.

    Returns Counter[(relative_path, directive_key)].
    """
    counter: Counter = Counter()
    for path in _walk_python(root):
        rel = str(path.relative_to(root))
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for pattern, extract in _SUPPRESSION_PATTERNS:
            for m in pattern.finditer(text):
                counter[(rel, extract(m))] += 1
    return counter


# --- Pytest weakening scan ----------------------------------------------------


_SKIP_MARKER = re.compile(r"@pytest\.mark\.(?:skip|xfail|skipif)(?:\(|\b)")
_ASSERT_KEYWORD = re.compile(r"\bassert\b")


def scan_pytest_weakening(root: Path) -> dict:
    """Per-test-file counts of skip markers and assert statements.

    Returns {"skips": {path: count}, "asserts": {path: count}}.
    """
    skips: dict[str, int] = {}
    asserts: dict[str, int] = {}
    tests_dir = root / "tests"
    if not tests_dir.exists():
        return {"skips": skips, "asserts": asserts}
    for path in tests_dir.rglob("*.py"):
        rel = str(path.relative_to(root))
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        skips[rel] = len(_SKIP_MARKER.findall(text))
        asserts[rel] = len(_ASSERT_KEYWORD.findall(text))
    return {"skips": skips, "asserts": asserts}


# --- Serialization helpers ----------------------------------------------------


def _serialize(counter: Counter) -> list[list]:
    """JSON can't key on tuples; emit list of [file, code, count]."""
    return [[k[0], k[1], v] for k, v in sorted(counter.items())]


def _deserialize(data: list) -> Counter:
    return Counter({(f, c): n for f, c, n in data})


# --- Subcommands --------------------------------------------------------------


def cmd_baseline(args: argparse.Namespace) -> None:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    root = Path(args.root).resolve()

    sys.stderr.write(f"sdlc-gate: capturing baseline at {out_dir} (sha={args.sha})\n")
    ruff = run_ruff(root)
    mypy = run_mypy(root)
    suppressions = scan_suppressions(root)
    pytest_w = scan_pytest_weakening(root)

    (out_dir / "ruff.json").write_text(json.dumps(_serialize(ruff), indent=2))
    (out_dir / "mypy.json").write_text(json.dumps(_serialize(mypy), indent=2))
    (out_dir / "suppressions.json").write_text(
        json.dumps(_serialize(suppressions), indent=2)
    )
    (out_dir / "pytest-weakening.json").write_text(json.dumps(pytest_w, indent=2))
    (out_dir / "sha.txt").write_text(args.sha + "\n")

    sys.stdout.write(
        json.dumps(
            {
                "ok": True,
                "sha": args.sha,
                "out_dir": str(out_dir),
                "ruff_total": sum(ruff.values()),
                "ruff_keys": len(ruff),
                "mypy_total": sum(mypy.values()),
                "mypy_keys": len(mypy),
                "suppressions_total": sum(suppressions.values()),
                "suppressions_keys": len(suppressions),
                "pytest_skip_total": sum(pytest_w["skips"].values()),
                "pytest_assert_total": sum(pytest_w["asserts"].values()),
            },
            indent=2,
        )
        + "\n"
    )


def _git_rename_map(baseline_sha: str) -> tuple[dict[str, str], set[str]]:
    """Returns (rename_map: baseline_path -> branch_path, deleted_paths)."""
    proc = subprocess.run(
        ["git", "diff", "--name-status", "-M", f"{baseline_sha}..HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    rename_map: dict[str, str] = {}
    deleted: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        status = parts[0]
        if status.startswith(("R", "C")) and len(parts) >= 3:
            rename_map[parts[1]] = parts[2]
        elif status == "D" and len(parts) >= 2:
            deleted.add(parts[1])
    return rename_map, deleted


def _translate(
    counter: Counter, rename_map: dict[str, str], deleted: set[str]
) -> Counter:
    """Apply rename map to baseline counter; drop entries for deleted files."""
    out: Counter = Counter()
    for (file, code), n in counter.items():
        if file in deleted:
            continue
        new_file = rename_map.get(file, file)
        out[(new_file, code)] += n
    return out


def cmd_diff(args: argparse.Namespace) -> None:
    base_dir = Path(args.baseline_dir)
    if not base_dir.exists():
        sys.stderr.write(f"sdlc-gate: baseline dir {base_dir} missing\n")
        sys.exit(2)

    baseline_sha = (base_dir / "sha.txt").read_text().strip()
    baseline_ruff = _deserialize(json.loads((base_dir / "ruff.json").read_text()))
    baseline_mypy = _deserialize(json.loads((base_dir / "mypy.json").read_text()))
    baseline_supp = _deserialize(
        json.loads((base_dir / "suppressions.json").read_text())
    )
    baseline_pytest = json.loads((base_dir / "pytest-weakening.json").read_text())

    rename_map, deleted = _git_rename_map(baseline_sha)

    root = Path().resolve()
    branch_ruff = run_ruff(root)
    branch_mypy = run_mypy(root)
    branch_supp = scan_suppressions(root)
    branch_pytest = scan_pytest_weakening(root)

    blocks: list[dict] = []
    advisories: list[dict] = []

    # --- Check A: per-(file, code) error identity diff ------------------------

    def diff_errors(branch_c: Counter, base_c: Counter, label: str) -> None:
        translated = _translate(base_c, rename_map, deleted)
        # Per-file new errors
        per_file_new: list[dict] = []
        for (file, code), n in branch_c.items():
            base_n = translated.get((file, code), 0)
            if n > base_n:
                per_file_new.append({"file": file, "code": code, "new": n - base_n})
        # Global net per code: did the total count for this code rise?
        global_branch: Counter = Counter()
        for (file, code), n in branch_c.items():
            global_branch[code] += n
        global_base: Counter = Counter()
        for (file, code), n in translated.items():
            global_base[code] += n
        hard: list[dict] = []
        soft: list[dict] = []
        for entry in per_file_new:
            net = global_branch[entry["code"]] - global_base[entry["code"]]
            entry["global_net"] = net
            if net <= 0:
                soft.append(entry)
            else:
                hard.append(entry)
        if hard:
            blocks.append({"check": f"A.{label}", "kind": "new_errors", "items": hard})
        if soft:
            advisories.append(
                {"check": f"A.{label}", "kind": "relocated_errors", "items": soft}
            )

    diff_errors(branch_ruff, baseline_ruff, "ruff")
    diff_errors(branch_mypy, baseline_mypy, "mypy")

    # --- Check B: suppression-count diff --------------------------------------

    translated_supp = _translate(baseline_supp, rename_map, deleted)
    new_supp: list[dict] = []
    for (file, directive), n in branch_supp.items():
        base_n = translated_supp.get((file, directive), 0)
        if n > base_n:
            new_supp.append({"file": file, "directive": directive, "new": n - base_n})
    if new_supp:
        blocks.append({"check": "B", "kind": "new_suppressions", "items": new_supp})

    # --- Check C: test deletions (advisory) -----------------------------------

    deleted_tests = sorted(f for f in deleted if f.startswith("tests/"))
    if deleted_tests:
        advisories.append(
            {"check": "C", "kind": "test_deletions", "items": deleted_tests}
        )

    # --- Check D: pytest weakening --------------------------------------------

    new_skips: list[dict] = []
    for file, n in branch_pytest["skips"].items():
        # Reverse-translate: find which baseline path maps to this branch path
        base_n = 0
        for bf, bn in baseline_pytest["skips"].items():
            if rename_map.get(bf, bf) == file:
                base_n = bn
                break
        if n > base_n:
            new_skips.append({"file": file, "new": n - base_n})
    if new_skips:
        blocks.append(
            {"check": "D.skips", "kind": "new_skip_markers", "items": new_skips}
        )

    lost_asserts: list[dict] = []
    for bf, base_n in baseline_pytest["asserts"].items():
        if bf in deleted:
            continue
        new_f = rename_map.get(bf, bf)
        n = branch_pytest["asserts"].get(new_f, 0)
        if n < base_n:
            lost_asserts.append({"file": new_f, "lost": base_n - n})
    if lost_asserts:
        blocks.append(
            {"check": "D.asserts", "kind": "lost_assertions", "items": lost_asserts}
        )

    # --- Verdict --------------------------------------------------------------

    if blocks:
        verdict = "fail"
    elif advisories:
        verdict = "advisory"
    else:
        verdict = "pass"

    report = {
        "verdict": verdict,
        "baseline_sha": baseline_sha,
        "blocks": blocks,
        "advisories": advisories,
        "summary": {
            "ruff_branch": sum(branch_ruff.values()),
            "ruff_baseline": sum(baseline_ruff.values()),
            "mypy_branch": sum(branch_mypy.values()),
            "mypy_baseline": sum(baseline_mypy.values()),
            "suppressions_branch": sum(branch_supp.values()),
            "suppressions_baseline": sum(baseline_supp.values()),
        },
    }
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    sys.exit(0 if verdict in ("pass", "advisory") else 1)


# --- Entry point --------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(prog="sdlc-gate")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_baseline = sub.add_parser(
        "baseline",
        help="Capture ruff/mypy/suppression/pytest baselines from the current tree",
    )
    p_baseline.add_argument("--sha", required=True, help="SHA being captured")
    p_baseline.add_argument("--out", required=True, help="Output directory")
    p_baseline.add_argument("--root", default=".", help="Project root (default: cwd)")
    p_baseline.set_defaults(func=cmd_baseline)

    p_diff = sub.add_parser(
        "diff",
        help="Diff the current tree against a captured baseline; emit verdict",
    )
    p_diff.add_argument("--baseline-dir", required=True)
    p_diff.set_defaults(func=cmd_diff)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
