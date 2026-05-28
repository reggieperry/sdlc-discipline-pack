"""Tests for the spec-declared assertion-loss migration waiver (pack #199).

Check D hard-fails any per-file assertion-count loss vs the merge-base, with
no override — which false-positives on legitimate test-consolidation stories
where assertions migrated to a sibling test (Elder EL-173). This adds an
opt-in, mechanically-verified waiver: a story declares the loss + where it
went, and the gate downgrades the matching D.asserts block to an advisory
ONLY when three git-only checks pass:

  1. delta-exactness     measured per-file loss == |expected_delta|
  2. sibling-grew        migrated_to_test exists at branch tip with >= loss asserts
  3. predicate-text      each removed assertion's predicate text appears in the
                         sibling's collected test_ functions (AST-scoped)

The waiver reaches the gate as a --assertion-loss-waiver JSON arg (the caller
reads it from bead metadata). The gate stays a pure function — no bd coupling.

The forge test is the load-bearing one: a sibling that grew but does NOT carry
the removed predicates must still block (you can't dress a deletion as a
migration without really relocating the predicates).

stdlib-only. Matches pack convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_sdlc_gate_assertion_waiver -v
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SDLC_GATE = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "overlay"
    / "per-provider"
    / "claude"
    / ".claude"
    / "sdlc-discipline"
    / "sdlc-gate.py"
)
assert SDLC_GATE.exists(), f"sdlc-gate.py not found at {SDLC_GATE}"

# Baseline test file: 5 assertions. The branch keeps 1 and removes 4.
SMOKE_BASELINE = (
    "def test_smoke():\n"
    "    assert entry.status == ENTERED\n"
    "    assert fill.cumulative == shares\n"
    "    assert mod.action == MODIFY\n"
    "    assert stop.price == expected\n"
    "    assert order.id is not None\n"
)
SMOKE_BRANCH = "def test_smoke():\n    assert order.id is not None\n"

# Sibling at baseline (1 assert) and at branch tip (carries the 4 migrated
# predicates verbatim inside a test_ function + 1 of its own = 5).
SIBLING_BASELINE = "def test_fill_dispatch():\n    assert extra.thing == 1\n"
SIBLING_BRANCH = (
    "def test_fill_dispatch():\n"
    "    assert entry.status == ENTERED\n"
    "    assert fill.cumulative == shares\n"
    "    assert mod.action == MODIFY\n"
    "    assert stop.price == expected\n"
    "    assert extra.thing == 1\n"
)
# Forge sibling: grew to 5 asserts, but NONE are the removed predicates.
SIBLING_FORGE = (
    "def test_fill_dispatch():\n"
    "    assert foo == 1\n"
    "    assert foo == 2\n"
    "    assert foo == 3\n"
    "    assert foo == 4\n"
    "    assert foo == 5\n"
)

WAIVER = {
    "file": "tests/test_smoke.py",
    "expected_delta": -4,
    "migrated_to_test": "tests/test_sibling.py",
    "migrated_in": "EL-171",
}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _write_baseline_dir(base_dir: Path, sha: str, asserts: dict) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "sha.txt").write_text(sha + "\n")
    (base_dir / "ruff.json").write_text("[]")
    (base_dir / "mypy.json").write_text("[]")
    (base_dir / "bandit.json").write_text("[]")
    (base_dir / "suppressions.json").write_text("[]")
    (base_dir / "pytest-weakening.json").write_text(json.dumps({"skips": {}, "asserts": asserts}))


def _invoke_diff(repo: Path, base_dir: Path, waiver: object = None) -> subprocess.CompletedProcess:
    cmd = ["python3", str(SDLC_GATE), "diff", "--baseline-dir", str(base_dir)]
    if waiver is not None:
        cmd += ["--assertion-loss-waiver", json.dumps(waiver)]
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=60)


def _build_repo(tmp: Path, *, sibling_branch: str) -> tuple[Path, str]:
    """Repo whose baseline commit has the 5-assert smoke + 1-assert sibling;
    the working tree drops smoke to 1 assert and grows the sibling. Returns
    (repo, baseline_sha)."""
    repo = tmp / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "tests" / "test_smoke.py").write_text(SMOKE_BASELINE)
    (repo / "tests" / "test_sibling.py").write_text(SIBLING_BASELINE)
    (repo / "README.md").write_text("# baseline\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "baseline")
    baseline_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    # Working-tree change: smoke loses 4 asserts; sibling grows.
    (repo / "tests" / "test_smoke.py").write_text(SMOKE_BRANCH)
    (repo / "tests" / "test_sibling.py").write_text(sibling_branch)
    return repo, baseline_sha


# Baseline assert counts the gate compares the working tree against.
BASE_ASSERTS = {"tests/test_smoke.py": 5, "tests/test_sibling.py": 1}


class AssertionLossWaiver(unittest.TestCase):
    def test_no_waiver_blocks(self) -> None:
        """Baseline behavior: a lost-assertion file blocks when unwaived."""
        with TemporaryDirectory() as t:
            tmp = Path(t)
            repo, sha = _build_repo(tmp, sibling_branch=SIBLING_BRANCH)
            base_dir = tmp / "bl"
            _write_baseline_dir(base_dir, sha, BASE_ASSERTS)
            r = _invoke_diff(repo, base_dir)
            self.assertEqual(r.returncode, 1, msg=r.stdout)
            report = json.loads(r.stdout)
            self.assertEqual(report["verdict"], "fail")
            self.assertTrue(any(b.get("check") == "D.asserts" for b in report["blocks"]))

    def test_verified_waiver_downgrades_to_advisory(self) -> None:
        """All three checks pass → D.asserts loss is an advisory, verdict != fail."""
        with TemporaryDirectory() as t:
            tmp = Path(t)
            repo, sha = _build_repo(tmp, sibling_branch=SIBLING_BRANCH)
            base_dir = tmp / "bl"
            _write_baseline_dir(base_dir, sha, BASE_ASSERTS)
            r = _invoke_diff(repo, base_dir, waiver=WAIVER)
            self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
            report = json.loads(r.stdout)
            self.assertNotEqual(report["verdict"], "fail")
            self.assertFalse(
                any(b.get("check") == "D.asserts" for b in report["blocks"]),
                msg=f"D.asserts should not block under a verified waiver; blocks={report['blocks']}",
            )
            self.assertTrue(
                any(a.get("check") == "D.asserts" for a in report["advisories"]),
                msg=f"expected a D.asserts advisory; advisories={report['advisories']}",
            )

    def test_delta_mismatch_blocks(self) -> None:
        """Declared delta != measured loss → blocks (can't hide extra deletions)."""
        with TemporaryDirectory() as t:
            tmp = Path(t)
            repo, sha = _build_repo(tmp, sibling_branch=SIBLING_BRANCH)
            base_dir = tmp / "bl"
            _write_baseline_dir(base_dir, sha, BASE_ASSERTS)
            bad = {**WAIVER, "expected_delta": -3}  # actual loss is 4
            r = _invoke_diff(repo, base_dir, waiver=bad)
            self.assertEqual(r.returncode, 1, msg=r.stdout)
            self.assertTrue(
                any(b.get("check") == "D.asserts" for b in json.loads(r.stdout)["blocks"])
            )

    def test_forge_sibling_without_predicates_blocks(self) -> None:
        """The forge defense: a sibling that GREW but does not carry the removed
        predicates must still block — a deletion dressed as a migration."""
        with TemporaryDirectory() as t:
            tmp = Path(t)
            repo, sha = _build_repo(tmp, sibling_branch=SIBLING_FORGE)
            base_dir = tmp / "bl"
            _write_baseline_dir(base_dir, sha, BASE_ASSERTS)
            r = _invoke_diff(repo, base_dir, waiver=WAIVER)
            self.assertEqual(r.returncode, 1, msg=r.stdout)
            self.assertTrue(
                any(b.get("check") == "D.asserts" for b in json.loads(r.stdout)["blocks"]),
                msg="forged migration (predicates absent from sibling) must block",
            )


if __name__ == "__main__":
    unittest.main()
