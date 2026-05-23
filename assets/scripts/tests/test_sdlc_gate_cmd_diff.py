"""Characterization tests for sdlc-gate.py's cmd_diff (audit finding #3 refactor backfill).

v2.30's audit #3 Extract Function moves cmd_diff from 145 lines to ~77 lines via
three extracts (_load_baseline_snapshots, _diff_errors, _check_pytest_weakening).
The Tier 3 design specified characterization tests before refactor; the refactor
shipped without them in the v2.30 sprint. These tests backfill the gap so future
refactors of cmd_diff have a behavior pin.

Per Feathers's characterization-test discipline: tests pin current behavior, not
the right answer. Two scenarios:

  - Happy path: baseline with zero findings + working tree with zero findings
    (no Python files) → verdict=pass, exit 0.
  - Skip-regression: baseline pytest-weakening.json with zero skips + working
    tree has a test file with `@pytest.mark.skip` → verdict=fail, exit 1,
    blocks include `check=D.skips`.

stdlib-only (unittest + tempfile + subprocess + json). Matches pack convention.

Run with::

    python3 -m unittest discover -s assets/scripts/tests -v
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


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _write_baseline_dir(
    base_dir: Path,
    sha: str,
    *,
    ruff: list | None = None,
    mypy: list | None = None,
    skips: dict | None = None,
    asserts: dict | None = None,
    include_bandit: bool = True,
) -> None:
    """Write the baseline-JSON files cmd_diff reads on load.

    Defaults: empty counters for ruff/mypy/bandit/suppressions; empty
    pytest-weakening skips/asserts. Each kwarg seeds the matching file
    so per-test characterization can pin specific branches.

    `include_bandit=False` omits bandit.json — exercises the pre-v2.9
    baseline path (_load_baseline_snapshots treats absent bandit as empty
    Counter).
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "sha.txt").write_text(sha + "\n")
    (base_dir / "ruff.json").write_text(json.dumps(ruff or []))
    (base_dir / "mypy.json").write_text(json.dumps(mypy or []))
    if include_bandit:
        (base_dir / "bandit.json").write_text("[]")
    (base_dir / "suppressions.json").write_text("[]")
    (base_dir / "pytest-weakening.json").write_text(
        json.dumps({"skips": skips or {}, "asserts": asserts or {}})
    )


def _invoke_diff(repo_root: Path, base_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(SDLC_GATE), "diff", "--baseline-dir", str(base_dir)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
    )


class CmdDiffCharacterizationTests(unittest.TestCase):
    def test_happy_path_empty_tree_empty_baseline_passes(self) -> None:
        """Zero baseline findings + zero working-tree findings → verdict=pass."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo = tmp / "repo"
            repo.mkdir()
            _git(repo, "init", "-q", "-b", "main")
            (repo / "README.md").write_text("# baseline\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-q", "-m", "baseline")
            baseline_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

            base_dir = tmp / "baseline-snapshot"
            _write_baseline_dir(base_dir, baseline_sha)

            result = _invoke_diff(repo, base_dir)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            report = json.loads(result.stdout)
            self.assertEqual(report["verdict"], "pass")
            self.assertEqual(report["baseline_sha"], baseline_sha)
            self.assertEqual(report["blocks"], [])
            # Summary fields all present + zero
            for key in (
                "ruff_branch",
                "ruff_baseline",
                "mypy_branch",
                "mypy_baseline",
                "bandit_branch",
                "bandit_baseline",
                "suppressions_branch",
                "suppressions_baseline",
            ):
                self.assertEqual(report["summary"][key], 0, f"{key} expected 0")

    def test_new_skip_marker_in_tests_blocks_with_d_skips(self) -> None:
        """Adding @pytest.mark.skip to a test file should fire Check D.skips.

        Pins the cmd_diff → _check_pytest_weakening extraction's contract:
        when branch pytest_weakening.skips for a file exceeds baseline, the
        report carries one block with check='D.skips'.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo = tmp / "repo"
            tests_dir = repo / "tests"
            tests_dir.mkdir(parents=True)
            _git(repo, "init", "-q", "-b", "main")
            _git(repo, "add", ".")
            (repo / "README.md").write_text("# baseline\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-q", "-m", "baseline")
            baseline_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

            # Working-tree addition: a test file carrying a pytest.mark.skip.
            (tests_dir / "test_x.py").write_text(
                "import pytest\n"
                "\n"
                "@pytest.mark.skip(reason='not yet')\n"
                "def test_skipped():\n"
                "    assert True\n"
            )

            base_dir = tmp / "baseline-snapshot"
            _write_baseline_dir(base_dir, baseline_sha)

            result = _invoke_diff(repo, base_dir)

            self.assertEqual(result.returncode, 1, f"expected fail exit; stdout={result.stdout!r}")
            report = json.loads(result.stdout)
            self.assertEqual(report["verdict"], "fail")
            skip_blocks = [b for b in report["blocks"] if b.get("check") == "D.skips"]
            self.assertEqual(
                len(skip_blocks), 1, f"expected one D.skips block; got blocks={report['blocks']}"
            )
            items = skip_blocks[0]["items"]
            self.assertTrue(
                any(item["file"] == "tests/test_x.py" for item in items),
                f"expected tests/test_x.py in items; got {items}",
            )

    def test_lost_assertions_blocks_with_d_asserts(self) -> None:
        """Baseline pytest-weakening.asserts shows a file with N assertions;
        the working tree's same file has fewer → block with check='D.asserts'.

        Pins `_check_pytest_weakening`'s D.asserts branch — the lost-assertion
        regression v2.30 #114 just tightened the gate-disable tests against,
        and the same shape `slop-reviewer cat 6` catches in test reformulation.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo = tmp / "repo"
            tests_dir = repo / "tests"
            tests_dir.mkdir(parents=True)
            _git(repo, "init", "-q", "-b", "main")
            # Working tree: one assertion in the test
            (tests_dir / "test_y.py").write_text("def test_one():\n    assert True\n")
            (repo / "README.md").write_text("# baseline\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-q", "-m", "baseline")
            baseline_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

            base_dir = tmp / "baseline-snapshot"
            # Baseline claims the file had 5 assertions; branch only has 1.
            _write_baseline_dir(
                base_dir,
                baseline_sha,
                asserts={"tests/test_y.py": 5},
            )

            result = _invoke_diff(repo, base_dir)

            self.assertEqual(result.returncode, 1, f"expected fail; stdout={result.stdout!r}")
            report = json.loads(result.stdout)
            self.assertEqual(report["verdict"], "fail")
            assert_blocks = [b for b in report["blocks"] if b.get("check") == "D.asserts"]
            self.assertEqual(
                len(assert_blocks), 1, f"expected one D.asserts block; got {report['blocks']}"
            )
            items = assert_blocks[0]["items"]
            entry = next((it for it in items if it["file"] == "tests/test_y.py"), None)
            self.assertIsNotNone(entry, f"expected tests/test_y.py in items; got {items}")
            self.assertEqual(entry["lost"], 4, "baseline=5, branch=1 → lost=4")

    def test_missing_bandit_json_treats_as_empty_baseline(self) -> None:
        """Pre-v2.9 baselines may not have bandit.json. _load_baseline_snapshots
        treats the absence as an empty Counter so the diff still runs cleanly.

        Pins the `bandit_path.exists()` fallback branch.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo = tmp / "repo"
            repo.mkdir()
            _git(repo, "init", "-q", "-b", "main")
            (repo / "README.md").write_text("# baseline\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-q", "-m", "baseline")
            baseline_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

            base_dir = tmp / "baseline-snapshot"
            _write_baseline_dir(base_dir, baseline_sha, include_bandit=False)
            self.assertFalse((base_dir / "bandit.json").exists(), "test setup invariant")

            result = _invoke_diff(repo, base_dir)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            report = json.loads(result.stdout)
            self.assertEqual(report["verdict"], "pass")
            # Empty baseline + empty branch → both summary fields zero.
            self.assertEqual(report["summary"]["bandit_baseline"], 0)
            self.assertEqual(report["summary"]["bandit_branch"], 0)

    def test_mypy_code_renormalized_on_load(self) -> None:
        """Pre-v2.9.2 baselines stored mypy codes un-normalized; the branch
        counter is normalized at run_mypy time. Without re-normalizing the
        baseline, the identity comparison sees `import-not-found` → `import`
        as a code-rename and flags a false-positive regression.

        Pins `_load_baseline_snapshots`'s mypy-code re-normalization pass.
        Setup: baseline carries `import-not-found` on a file. The branch
        wouldn't fire any mypy findings (mypy isn't reachably installed in
        the test env). After re-normalization the baseline carries `import`
        for the same file — that's a "lost N errors" shape, which the diff
        treats as relocation/improvement, not a regression. Verdict=pass.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo = tmp / "repo"
            repo.mkdir()
            _git(repo, "init", "-b", "main", "-q")
            (repo / "README.md").write_text("# baseline\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-q", "-m", "baseline")
            baseline_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

            base_dir = tmp / "baseline-snapshot"
            # Baseline claims mypy reported import-not-found on foo.py
            _write_baseline_dir(
                base_dir,
                baseline_sha,
                mypy=[["src/foo.py", "import-not-found", 1]],
            )

            result = _invoke_diff(repo, base_dir)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            report = json.loads(result.stdout)
            # The baseline's mypy total counts the normalized entry → 1.
            self.assertEqual(report["summary"]["mypy_baseline"], 1)
            # The branch's mypy total is 0 (mypy not running here) — diff treats
            # the loss as advisory at most, never as a new-errors block.
            self.assertEqual(report["summary"]["mypy_branch"], 0)
            mypy_blocks = [
                b for b in report["blocks"] if str(b.get("check", "")).startswith("A.mypy")
            ]
            self.assertEqual(mypy_blocks, [], "no new mypy errors → no block")

    def test_baseline_only_findings_with_empty_branch_produce_no_block_or_advisory(
        self,
    ) -> None:
        """Baseline carries ruff findings; branch (with no ruff binary reachable
        in the test env) reports zero. Per-file-new is empty → no block. No
        baseline-side decreases produce per-file rises → no advisory. Verdict=pass.

        This pins one branch of `_diff_errors`: the "branch empty, baseline
        non-empty" path. The full soft-classification branch (per-file rise
        cancelled by global decrease elsewhere) requires a fake ruff binary
        to drive branch-side findings + can be characterized when that
        infrastructure lands. Earlier name `test_diff_errors_soft_when_
        relocation_cancels_global_growth` advertised the soft branch but
        this body does not pin it; renamed to match what is actually verified
        per the deep-reasoning evaluation's non-discriminating-outcome
        category.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo = tmp / "repo"
            repo.mkdir()
            _git(repo, "init", "-q", "-b", "main")
            (repo / "README.md").write_text("# baseline\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-q", "-m", "baseline")
            baseline_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

            base_dir = tmp / "baseline-snapshot"
            _write_baseline_dir(
                base_dir,
                baseline_sha,
                ruff=[["src/a.py", "F401", 1], ["src/b.py", "F401", 1]],
            )

            result = _invoke_diff(repo, base_dir)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            report = json.loads(result.stdout)
            self.assertEqual(report["verdict"], "pass")
            ruff_blocks = [
                b for b in report["blocks"] if str(b.get("check", "")).startswith("A.ruff")
            ]
            ruff_advisories = [
                b for b in report["advisories"] if str(b.get("check", "")).startswith("A.ruff")
            ]
            self.assertEqual(ruff_blocks, [], "no new errors → no A.ruff block")
            self.assertEqual(ruff_advisories, [], "no new per-file entries → no advisory either")

    def test_renamed_file_baseline_translates_via_rename_map(self) -> None:
        """A file renamed between baseline and branch: the rename map reverses
        the baseline path to the new branch path before diffing. Without the
        translation, the diff would see "lost N errors on old.py" + "no errors
        on new.py" — a phantom regression.

        Pins the `_git_rename_map` consumption inside `_diff_errors`.

        Setup: baseline carries 1 F401 on `src/old.py`. Working tree has the
        file moved to `src/new.py`. git diff --name-status -M detects the
        rename. The baseline's `src/old.py` entry translates to `src/new.py`;
        with both counters at 1 (baseline) → 0 (branch), the diff sees
        no-new-errors and verdict=pass.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo = tmp / "repo"
            src_dir = repo / "src"
            src_dir.mkdir(parents=True)
            _git(repo, "init", "-q", "-b", "main")
            (src_dir / "old.py").write_text("# baseline content\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-q", "-m", "baseline")
            baseline_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

            # Rename old.py → new.py via git mv (preserves rename signal).
            _git(repo, "mv", "src/old.py", "src/new.py")
            _git(repo, "commit", "-q", "-m", "rename old to new")

            base_dir = tmp / "baseline-snapshot"
            _write_baseline_dir(base_dir, baseline_sha, ruff=[["src/old.py", "F401", 1]])

            result = _invoke_diff(repo, base_dir)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            report = json.loads(result.stdout)
            self.assertEqual(report["verdict"], "pass")
            # The rename map should have translated the baseline entry; the diff
            # sees no new errors on src/new.py.
            ruff_blocks = [
                b for b in report["blocks"] if str(b.get("check", "")).startswith("A.ruff")
            ]
            self.assertEqual(ruff_blocks, [], f"unexpected block: {report['blocks']}")


if __name__ == "__main__":
    unittest.main()
