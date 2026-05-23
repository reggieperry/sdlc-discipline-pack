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


def _write_baseline_dir(base_dir: Path, sha: str, *, skips: dict | None = None) -> None:
    """Write the 6 baseline-JSON files cmd_diff reads on load.

    Empty counters for ruff/mypy/bandit/suppressions; `skips` parameter
    seeds pytest-weakening's skips dict (defaults to empty) so the regression
    test can pin the D.skips check.
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "sha.txt").write_text(sha + "\n")
    (base_dir / "ruff.json").write_text("[]")
    (base_dir / "mypy.json").write_text("[]")
    (base_dir / "bandit.json").write_text("[]")
    (base_dir / "suppressions.json").write_text("[]")
    (base_dir / "pytest-weakening.json").write_text(
        json.dumps({"skips": skips or {}, "asserts": {}})
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


if __name__ == "__main__":
    unittest.main()
