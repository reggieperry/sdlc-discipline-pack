#!/usr/bin/env python3
"""SDLC differential gate: capture and compare static-analysis baselines.

Subcommands
-----------
baseline   Run ruff, mypy, bandit, and the suppression/pytest-weakening scans against
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

Suppressions (#type:ignore, #noqa, #pyright:ignore, #nosec) are tracked
separately. Targeted suppressions count under their specific code keys;
blanket forms count under a "BLANKET" key. Adding a blanket suppression
where a targeted one existed registers as a new key, not a count
preservation, so scope-broadening is caught.

Note on #nosec: bandit 1.9.4 silently treats `# nosec B603,B607`
(comma-separated rule IDs) as non-matching, while `# nosec B603 B607`
(space-separated) works correctly. The gate's pattern recognizes both
forms for anti-weakening accounting — if the worker adds either, it
counts as a new suppression. The pack's templates and prose direct
operators to use the space-separated form so the in-source intent
actually takes effect at bandit-execution time.

Pytest-anti-weakening tracks per-file count of pytest skip/xfail/skipif
markers (must not increase) and assert keywords (must not decrease per
file across rename map).
"""

from __future__ import annotations

import argparse
import ast
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
            sys.stderr.write("sdlc-gate: ruff produced non-JSON output; treating as no findings\n")
            findings = []
    counter: Counter = Counter()
    for f in findings:
        path = _rel(f.get("filename", ""), root)
        code = f.get("code", "?")
        counter[(path, code)] += 1
    return counter


_MYPY_LINE = re.compile(r"^(?P<path>[^:]+?):\d+:.*?error:.*?\[(?P<code>[^\]]+)\]\s*$")

# mypy emits semantically-equivalent codes whose exact spelling depends on
# tree state (whether an import is resolvable, whether a stub package is
# installed in the current worktree, etc.). The per-(file, code) identity
# model in this gate sees a code flip as "lost N errors of code X, gained
# N errors of code Y" on the same line — a false-positive block. The
# normalization map collapses known-equivalent codes to a canonical key
# so the diff sees no net change when the only difference is which
# spelling mypy chose.
#
# Each entry is conservative: only codes that fire on the same underlying
# defect class for the same line. Codes that look related but actually
# distinguish different defects (e.g. `import-not-found` vs
# `import-untyped` — module-missing vs stub-missing) are NOT collapsed.
_MYPY_CODE_ALIASES: dict[str, str] = {
    # mypy 2.x split [import] into more specific subcodes. Whether a
    # given site fires as the parent or the subcode depends on tree
    # state (e.g. is the test's import target on the path when mypy
    # walks this worktree?). Hit on Elder REFACTOR-001 tester
    # 2026-05-11: same line, baseline=`[import]`, branch=`[import-not-found]`.
    "import-not-found": "import",
}


def _normalize_mypy_code(code: str) -> str:
    """Collapse equivalent mypy codes to a canonical key for diff identity."""
    return _MYPY_CODE_ALIASES.get(code, code)


def run_mypy(root: Path) -> Counter:
    """Run mypy and parse the [error-code] suffix. Returns Counter[(file, code)] keyed by repo-relative paths.

    Applies _normalize_mypy_code to each captured code so tree-state-dependent
    code spellings (e.g. [import] vs [import-not-found]) collapse to a
    canonical key. See _MYPY_CODE_ALIASES for the conservative alias list.
    """
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
            code = _normalize_mypy_code(m.group("code"))
            counter[(_rel(m.group("path"), root), code)] += 1
    return counter


def run_bandit(root: Path) -> Counter:
    """Run bandit with JSON output. Returns Counter[(file, test_id)] keyed by repo-relative paths.

    Invokes via `uvx` so the rig is not required to carry bandit in its dev deps;
    rigs that have configured `[tool.bandit]` in `pyproject.toml` get their
    configuration honoured automatically (bandit auto-detects pyproject.toml
    from the cwd). The exit code is ignored — we only consume the JSON
    findings list, never bandit's own pass/fail signal.

    Failures to invoke bandit at all (uvx unavailable, network unreachable
    for the ephemeral install) are not treated as findings — the function
    returns an empty Counter and logs a one-line note to stderr. Rigs that
    intentionally opt out of bandit will see zero baseline and zero branch
    findings, which is a no-op for the differential gate.
    """
    # Write JSON to a temp file rather than stdout — `uvx` itself prints a
    # one-line progress indicator to stdout that contaminates the JSON when
    # bandit is invoked through it. The `-o` flag has bandit write the
    # report to a file, which we then read.
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        report_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            [
                "uvx",
                "bandit",
                "-c",
                "pyproject.toml",
                "-r",
                ".",
                "-f",
                "json",
                "-o",
                str(report_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode not in (0, 1):
            # 0 = clean; 1 = findings present; anything else is an invocation problem.
            sys.stderr.write(
                f"sdlc-gate: bandit invocation returned rc={proc.returncode}; "
                "treating as no findings\n",
            )
            return Counter()
        counter: Counter = Counter()
        try:
            report = json.loads(report_path.read_text() or "{}")
        except (json.JSONDecodeError, OSError):
            sys.stderr.write(
                "sdlc-gate: bandit produced non-JSON output; treating as no findings\n",
            )
            return counter
        for finding in report.get("results", []):
            path = _rel(finding.get("filename", ""), root)
            code = finding.get("test_id", "?")
            counter[(path, code)] += 1
        return counter
    finally:
        report_path.unlink(missing_ok=True)


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
    # nosec — both forms recognised so workers cannot weaken by suppressing
    # findings. Space-separated rule IDs work at bandit-execution time;
    # comma-separated is silently broken in bandit 1.9.4 but the worker
    # adding either is the act we want to catch.
    (
        re.compile(r"#\s*nosec\s+(?P<code>B\d+(?:[ ,]+B\d+)*)\b"),
        lambda m: f"nosec[{m.group('code').strip()}]",
    ),
    (
        # Blanket: no `:` or word char immediately after (`# nosec:foo` and
        # `# nosec` followed by alnum aren't blanket forms), AND no
        # whitespace-then-B-digit (those are the targeted form handled above).
        re.compile(r"#\s*nosec(?![:\w])(?!\s+B\d)"),
        lambda m: "nosec[BLANKET]",
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
        if any(part in skip or part.startswith(".") for part in path.relative_to(root).parts[:-1]):
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
    bandit = run_bandit(root)
    suppressions = scan_suppressions(root)
    pytest_w = scan_pytest_weakening(root)

    (out_dir / "ruff.json").write_text(json.dumps(_serialize(ruff), indent=2))
    (out_dir / "mypy.json").write_text(json.dumps(_serialize(mypy), indent=2))
    (out_dir / "bandit.json").write_text(json.dumps(_serialize(bandit), indent=2))
    (out_dir / "suppressions.json").write_text(json.dumps(_serialize(suppressions), indent=2))
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
                "bandit_total": sum(bandit.values()),
                "bandit_keys": len(bandit),
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


def _translate(counter: Counter, rename_map: dict[str, str], deleted: set[str]) -> Counter:
    """Apply rename map to baseline counter; drop entries for deleted files."""
    out: Counter = Counter()
    for (file, code), n in counter.items():
        if file in deleted:
            continue
        new_file = rename_map.get(file, file)
        out[(new_file, code)] += n
    return out


def _load_baseline_snapshots(base_dir: Path) -> dict:
    """Load all baseline JSON snapshots from `base_dir`.

    Re-normalizes mypy codes on load (pre-v2.9.2 baselines may carry
    un-normalized codes; without re-normalizing here the identity
    comparison sees a code-rename and flags false-positive regressions).
    Treats `bandit.json` as optional — pre-v2.9 baselines lack it.
    """
    baseline_sha = (base_dir / "sha.txt").read_text().strip()
    baseline_ruff = _deserialize(json.loads((base_dir / "ruff.json").read_text()))
    baseline_mypy_raw = _deserialize(json.loads((base_dir / "mypy.json").read_text()))
    baseline_mypy: Counter = Counter()
    for (file, code), n in baseline_mypy_raw.items():
        baseline_mypy[(file, _normalize_mypy_code(code))] += n
    bandit_path = base_dir / "bandit.json"
    baseline_bandit = (
        _deserialize(json.loads(bandit_path.read_text())) if bandit_path.exists() else Counter()
    )
    baseline_supp = _deserialize(json.loads((base_dir / "suppressions.json").read_text()))
    baseline_pytest = json.loads((base_dir / "pytest-weakening.json").read_text())
    return {
        "sha": baseline_sha,
        "ruff": baseline_ruff,
        "mypy": baseline_mypy,
        "bandit": baseline_bandit,
        "supp": baseline_supp,
        "pytest": baseline_pytest,
    }


def _diff_errors(
    branch_c: Counter,
    base_c: Counter,
    label: str,
    rename_map: dict[str, str],
    deleted: set[str],
) -> tuple[list[dict], list[dict]]:
    """Check A: per-(file, code) error identity diff against a translated baseline.

    A per-file count increase is "hard" (blocks) when the global count for
    that code also rose, "soft" (advisory) when the file's increase is
    cancelled by a decrease elsewhere — that's a relocation, not a new
    error. Returns (blocks_to_add, advisories_to_add) for the caller.
    """
    translated = _translate(base_c, rename_map, deleted)
    per_file_new: list[dict] = []
    for (file, code), n in branch_c.items():
        base_n = translated.get((file, code), 0)
        if n > base_n:
            per_file_new.append({"file": file, "code": code, "new": n - base_n})
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
    blocks_out: list[dict] = []
    advisories_out: list[dict] = []
    if hard:
        blocks_out.append({"check": f"A.{label}", "kind": "new_errors", "items": hard})
    if soft:
        advisories_out.append({"check": f"A.{label}", "kind": "relocated_errors", "items": soft})
    return blocks_out, advisories_out


def _parse_waivers(raw: str | None) -> list[dict]:
    """Parse the --assertion-loss-waiver JSON into a list of waiver dicts.

    Accepts a single object or a list. A malformed or incomplete entry is
    dropped — conservative, so the corresponding loss stays a hard block.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        sys.stderr.write("sdlc-gate: --assertion-loss-waiver is not valid JSON; ignoring\n")
        return []
    items = data if isinstance(data, list) else [data]
    out: list[dict] = []
    for w in items:
        if (
            isinstance(w, dict)
            and isinstance(w.get("file"), str)
            and isinstance(w.get("migrated_to_test"), str)
            and "expected_delta" in w
        ):
            out.append(w)
    return out


def _matching_waiver(waivers: list[dict], file: str) -> dict | None:
    for w in waivers:
        if w.get("file") == file:
            return w
    return None


def _removed_assert_predicates(baseline_sha: str, file: str) -> list[str]:
    """Normalized predicate text of each assertion removed from `file`
    between the baseline commit and the working tree. Empty on git failure
    (treated by the caller as a failed verification → block)."""
    try:
        out = subprocess.run(
            ["git", "diff", baseline_sha, "--", file],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except OSError:
        return []
    preds: list[str] = []
    for line in out.splitlines():
        if not line.startswith("-") or line.startswith("---"):
            continue
        body = line[1:]
        if not _ASSERT_KEYWORD.search(body):
            continue
        after = body.split("assert", 1)[1].strip()
        # Drop a trailing assertion message: `assert <expr>, "msg"`.
        after = re.split(r',\s*["\']', after, maxsplit=1)[0].strip()
        norm = " ".join(after.split())
        if norm:
            preds.append(norm)
    return preds


def _sibling_test_haystack(root: Path, sibling: str) -> str | None:
    """Whitespace-normalized source of the sibling file's collected `test_`
    functions. None if the file is missing or unparseable (→ verification
    fails). AST-scoping keeps module-level dead code / uncalled helpers from
    satisfying predicate containment."""
    try:
        src = (root / sibling).read_text()
    except OSError:
        return None
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    chunks: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test_"
        ):
            seg = ast.get_source_segment(src, node)
            if seg:
                chunks.append(" ".join(seg.split()))
    return " ".join(chunks)


def _waiver_verifies(
    waiver: dict,
    file: str,
    lost: int,
    branch_pytest: dict,
    baseline_sha: str,
    root: Path,
) -> bool:
    """Three mechanical, git-only checks; ALL must pass. Any failure or
    exception → False, so the loss stays a hard block (issue #199)."""
    try:
        # 1. Delta-exactness: declared delta must match the measured loss
        #    exactly — a worker can only waive precisely what it declared.
        expected_delta = int(waiver["expected_delta"])
        if expected_delta >= 0 or lost != -expected_delta:
            return False
        sibling = waiver["migrated_to_test"]
        # 2. Sibling-grew: the migration target carries >= lost assertions now.
        if branch_pytest["asserts"].get(sibling, 0) < lost:
            return False
        # 3. Predicate-text containment: every removed predicate's text appears
        #    in the sibling's collected test_ functions — a real relocation,
        #    not a deletion dressed as one.
        preds = _removed_assert_predicates(baseline_sha, file)
        if not preds:
            return False
        haystack = _sibling_test_haystack(root, sibling)
        if haystack is None:
            return False
        return all(p in haystack for p in preds)
    except (KeyError, ValueError, TypeError):
        return False


def _check_pytest_weakening(
    branch_pytest: dict,
    baseline_pytest: dict,
    rename_map: dict[str, str],
    deleted: set[str],
    waivers: list[dict] | None = None,
    baseline_sha: str = "",
    root: Path | None = None,
) -> tuple[list[dict], list[dict]]:
    """Check D: new pytest.mark.skip markers + dropped assertion counts.

    Returns (blocks, advisories). Skip-marker increases (Check D.skips) are
    always hard blocks. Assertion-count regressions (Check D.asserts) are
    hard blocks too, EXCEPT when a spec-declared, mechanically-verified
    migration waiver applies (issue #199) — those downgrade to an advisory.
    """
    waivers = waivers or []
    if root is None:
        root = Path().resolve()
    blocks_out: list[dict] = []
    advisories_out: list[dict] = []

    new_skips: list[dict] = []
    for file, n in branch_pytest["skips"].items():
        base_n = 0
        for bf, bn in baseline_pytest["skips"].items():
            if rename_map.get(bf, bf) == file:
                base_n = bn
                break
        if n > base_n:
            new_skips.append({"file": file, "new": n - base_n})
    if new_skips:
        blocks_out.append({"check": "D.skips", "kind": "new_skip_markers", "items": new_skips})

    lost_asserts: list[dict] = []
    waived_asserts: list[dict] = []
    for bf, base_n in baseline_pytest["asserts"].items():
        if bf in deleted:
            continue
        new_f = rename_map.get(bf, bf)
        n = branch_pytest["asserts"].get(new_f, 0)
        if n < base_n:
            lost = base_n - n
            waiver = _matching_waiver(waivers, new_f)
            if waiver is not None and _waiver_verifies(
                waiver, new_f, lost, branch_pytest, baseline_sha, root
            ):
                waived_asserts.append(
                    {
                        "file": new_f,
                        "lost": lost,
                        "migrated_to_test": waiver["migrated_to_test"],
                        "migrated_in": waiver.get("migrated_in", ""),
                    }
                )
            else:
                lost_asserts.append({"file": new_f, "lost": lost})
    if lost_asserts:
        blocks_out.append({"check": "D.asserts", "kind": "lost_assertions", "items": lost_asserts})
    if waived_asserts:
        advisories_out.append(
            {"check": "D.asserts", "kind": "waived_assertion_migration", "items": waived_asserts}
        )

    return blocks_out, advisories_out


def cmd_diff(args: argparse.Namespace) -> None:
    base_dir = Path(args.baseline_dir)
    if not base_dir.exists():
        sys.stderr.write(f"sdlc-gate: baseline dir {base_dir} missing\n")
        sys.exit(2)

    baseline = _load_baseline_snapshots(base_dir)
    rename_map, deleted = _git_rename_map(baseline["sha"])

    root = Path().resolve()
    branch_ruff = run_ruff(root)
    branch_mypy = run_mypy(root)
    branch_bandit = run_bandit(root)
    branch_supp = scan_suppressions(root)
    branch_pytest = scan_pytest_weakening(root)

    blocks: list[dict] = []
    advisories: list[dict] = []

    # Check A: error-identity diff for ruff, mypy, bandit
    for branch_c, base_c, label in (
        (branch_ruff, baseline["ruff"], "ruff"),
        (branch_mypy, baseline["mypy"], "mypy"),
        (branch_bandit, baseline["bandit"], "bandit"),
    ):
        a_blocks, a_advisories = _diff_errors(branch_c, base_c, label, rename_map, deleted)
        blocks.extend(a_blocks)
        advisories.extend(a_advisories)

    # Check B: new suppressions
    translated_supp = _translate(baseline["supp"], rename_map, deleted)
    new_supp: list[dict] = []
    for (file, directive), n in branch_supp.items():
        base_n = translated_supp.get((file, directive), 0)
        if n > base_n:
            new_supp.append({"file": file, "directive": directive, "new": n - base_n})
    if new_supp:
        blocks.append({"check": "B", "kind": "new_suppressions", "items": new_supp})

    # Check C: deleted test files (advisory)
    deleted_tests = sorted(f for f in deleted if f.startswith("tests/"))
    if deleted_tests:
        advisories.append({"check": "C", "kind": "test_deletions", "items": deleted_tests})

    # Check D: pytest weakening (skips + lost asserts). A spec-declared,
    # mechanically-verified migration waiver downgrades a matching D.asserts
    # loss to advisory (issue #199); everything else stays a hard block.
    waivers = _parse_waivers(getattr(args, "assertion_loss_waiver", None))
    d_blocks, d_advisories = _check_pytest_weakening(
        branch_pytest,
        baseline["pytest"],
        rename_map,
        deleted,
        waivers=waivers,
        baseline_sha=baseline["sha"],
        root=root,
    )
    blocks.extend(d_blocks)
    advisories.extend(d_advisories)

    if blocks:
        verdict = "fail"
    elif advisories:
        verdict = "advisory"
    else:
        verdict = "pass"

    report = {
        "verdict": verdict,
        "baseline_sha": baseline["sha"],
        "blocks": blocks,
        "advisories": advisories,
        "summary": {
            "ruff_branch": sum(branch_ruff.values()),
            "ruff_baseline": sum(baseline["ruff"].values()),
            "mypy_branch": sum(branch_mypy.values()),
            "mypy_baseline": sum(baseline["mypy"].values()),
            "bandit_branch": sum(branch_bandit.values()),
            "bandit_baseline": sum(baseline["bandit"].values()),
            "suppressions_branch": sum(branch_supp.values()),
            "suppressions_baseline": sum(baseline["supp"].values()),
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
    p_diff.add_argument(
        "--assertion-loss-waiver",
        default=None,
        help=(
            "JSON object (or list) declaring a sanctioned assertion-count loss: "
            '{"file", "expected_delta" (negative), "migrated_to_test", "migrated_in"}. '
            "A matching D.asserts loss downgrades to advisory only when the loss "
            "equals the declared delta exactly, the migration target carries at "
            "least that many assertions, and every removed predicate's text "
            "appears in the target's test_ functions. The caller reads this from "
            "the story's bead metadata (issue #199)."
        ),
    )
    p_diff.set_defaults(func=cmd_diff)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
