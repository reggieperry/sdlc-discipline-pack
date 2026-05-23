"""Tests for sdlc-scope-drift-audit.sh (pack#83 Prong 2).

Black-box subprocess tests. Each test stands up a tempdir as a git
repo with a main branch + a remote-tracking ref + a feature branch
with one or more files changed, then invokes the script against a
plan file written with a specific `**In:**` shape. Asserts exit code
and stdout file list.

stdlib-only (unittest + tempfile + subprocess + textwrap). Matches
pack convention.

Run with:
    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-scope-drift-audit.sh"
assert SCRIPT_PATH.exists(), f"sdlc-scope-drift-audit.sh not found at {SCRIPT_PATH}"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in cwd with deterministic identity + no GPG."""
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


def _make_repo_with_main_and_feature(
    tmp: Path,
    feature_files: dict[str, str],
) -> None:
    """Initialize a repo with one commit on main, an origin/main ref, and a
    feature branch carrying feature_files as new content."""
    _git(tmp, "init", "-q", "-b", "main")
    (tmp / "README.md").write_text("base\n")
    _git(tmp, "add", "README.md")
    _git(tmp, "commit", "-q", "-m", "base")
    # Create the remote-tracking ref so origin/main resolves without a
    # real remote.
    _git(tmp, "update-ref", "refs/remotes/origin/main", "main")

    _git(tmp, "checkout", "-q", "-b", "feature")
    for path, content in feature_files.items():
        full = tmp / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        _git(tmp, "add", path)
    if feature_files:
        _git(tmp, "commit", "-q", "-m", "feature changes")


def _write_plan(tmp: Path, in_section: str, out_section: str = "Anything else.") -> Path:
    """Write a plan file with the given **In:** content (raw markdown)."""
    plan = tmp / "plans" / "el-test.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(
        textwrap.dedent(
            f"""\
            # el-test plan

            ## Scope

            **In:** {in_section}

            **Out:** {out_section}

            ## Notes
            """
        )
    )
    return plan


def _invoke(plan: Path, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT_PATH), "--plan", str(plan), "--target", "main"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
    )


class NoDriftTests(unittest.TestCase):
    def test_empty_diff_exits_clean(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo_with_main_and_feature(tmp, feature_files={})
            plan = _write_plan(tmp, "`core/foo.py`, `tests/test_foo.py`.")

            result = _invoke(plan, tmp)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertEqual(result.stdout, "")

    def test_diff_matches_in_list_exits_clean(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo_with_main_and_feature(
                tmp,
                feature_files={
                    "core/foo.py": "x\n",
                    "tests/test_foo.py": "y\n",
                },
            )
            plan = _write_plan(tmp, "`core/foo.py`, `tests/test_foo.py`.")

            result = _invoke(plan, tmp)

            self.assertEqual(
                result.returncode, 0, f"matched in-scope diff → exit 0; stderr={result.stderr!r}"
            )

    def test_glob_in_in_list_matches_multiple_files(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo_with_main_and_feature(
                tmp,
                feature_files={
                    "tests/test_a.py": "x\n",
                    "tests/test_b.py": "y\n",
                },
            )
            plan = _write_plan(tmp, "`tests/test_*.py`.")

            result = _invoke(plan, tmp)

            self.assertEqual(result.returncode, 0, f"glob match → exit 0; stderr={result.stderr!r}")


class DriftDetectedTests(unittest.TestCase):
    def test_single_file_outside_scope_exits_one_with_file_listed(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo_with_main_and_feature(
                tmp,
                feature_files={
                    "core/foo.py": "x\n",  # in scope
                    "stories/EL-bad.md": "drift\n",  # out of scope
                },
            )
            plan = _write_plan(tmp, "`core/foo.py`.")

            result = _invoke(plan, tmp)

            self.assertEqual(result.returncode, 1, f"stderr={result.stderr!r}")
            self.assertIn("stories/EL-bad.md", result.stdout)
            self.assertNotIn("core/foo.py", result.stdout)

    def test_multiple_files_outside_scope_all_listed(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo_with_main_and_feature(
                tmp,
                feature_files={
                    "core/foo.py": "x\n",
                    "stories/EL-a.md": "drift-a\n",
                    "stories/EL-b.md": "drift-b\n",
                },
            )
            plan = _write_plan(tmp, "`core/foo.py`.")

            result = _invoke(plan, tmp)

            self.assertEqual(result.returncode, 1, f"stderr={result.stderr!r}")
            self.assertIn("stories/EL-a.md", result.stdout)
            self.assertIn("stories/EL-b.md", result.stdout)


class FailOpenTests(unittest.TestCase):
    """Plan can't be parsed → fail-open (no drift flagged)."""

    def test_missing_plan_file_exits_clean(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo_with_main_and_feature(
                tmp,
                feature_files={"core/foo.py": "x\n"},
            )

            result = subprocess.run(
                [
                    str(SCRIPT_PATH),
                    "--plan",
                    str(tmp / "nonexistent-plan.md"),
                    "--target",
                    "main",
                ],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(
                result.returncode, 0, f"missing plan → fail-open; stderr={result.stderr!r}"
            )

    def test_plan_without_in_section_exits_clean(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo_with_main_and_feature(
                tmp,
                feature_files={"core/foo.py": "x\n"},
            )
            plan = tmp / "plans" / "el-no-in.md"
            plan.parent.mkdir(parents=True, exist_ok=True)
            plan.write_text("# plan with no scope section\n\njust prose\n")

            result = _invoke(plan, tmp)

            self.assertEqual(
                result.returncode,
                0,
                f"plan without **In:** → fail-open; stderr={result.stderr!r}",
            )

    def test_plan_with_in_section_but_no_backticked_paths_exits_clean(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo_with_main_and_feature(
                tmp,
                feature_files={"core/foo.py": "x\n"},
            )
            plan = _write_plan(tmp, "Some prose without backticks.")

            result = _invoke(plan, tmp)

            self.assertEqual(
                result.returncode,
                0,
                f"plan with prose **In:** → fail-open; stderr={result.stderr!r}",
            )


if __name__ == "__main__":
    unittest.main()
