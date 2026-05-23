"""Tests for sdlc-rule-checks/function_body_length.py (pack #52).

Sets up a tempdir git repo with a main commit + feature branch carrying
Python files of various function shapes, then invokes the checker.

stdlib-only (unittest + tempfile + subprocess + textwrap). Matches pack
convention.

Run with::

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

CHECKER = Path(__file__).resolve().parent.parent / "sdlc-rule-checks" / "function_body_length.py"
assert CHECKER.exists(), f"function_body_length.py not found at {CHECKER}"


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


def _make_repo_with_main_and_feature(
    tmp: Path, baseline_files: dict[str, str], feature_files: dict[str, str]
) -> None:
    """Initialize repo: write baseline_files, commit on main; write feature_files
    on a feature branch."""
    _git(tmp, "init", "-q", "-b", "main")
    # Always create at least one baseline commit so `main` exists as a ref
    # (git diff main..HEAD needs both endpoints to resolve).
    (tmp / ".git-keep").write_text("baseline marker\n")
    _git(tmp, "add", ".git-keep")
    for path, content in baseline_files.items():
        full = tmp / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        _git(tmp, "add", path)
    _git(tmp, "commit", "-q", "-m", "baseline")
    _git(tmp, "checkout", "-q", "-b", "feature")
    for path, content in feature_files.items():
        full = tmp / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        _git(tmp, "add", path)
    if feature_files:
        _git(tmp, "commit", "-q", "-m", "feature")


def _invoke(
    cwd: Path, diff_range: str = "main..HEAD", max_lines: int = 25
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "python3",
            str(CHECKER),
            "--diff-range",
            diff_range,
            "--max-lines",
            str(max_lines),
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _func_body(body_lines: int) -> str:
    """Build a Python function whose body has body_lines statements."""
    stmts = "\n".join(f"    x = {i}" for i in range(body_lines))
    return stmts


class NoViolationTests(unittest.TestCase):
    def test_no_python_files_changed_no_violation(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo_with_main_and_feature(tmp, {}, {"README.md": "x\n"})

            result = _invoke(tmp)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")

    def test_short_function_within_cap_no_violation(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            content = f"def short():\n{_func_body(10)}\n"
            _make_repo_with_main_and_feature(tmp, {}, {"core/foo.py": content})

            result = _invoke(tmp, max_lines=25)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")

    def test_long_function_with_higher_cap_no_violation(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            content = f"def long_one():\n{_func_body(50)}\n"
            _make_repo_with_main_and_feature(tmp, {}, {"core/foo.py": content})

            result = _invoke(tmp, max_lines=60)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")


class ViolationTests(unittest.TestCase):
    def test_long_function_exceeds_cap_flagged(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            # 30-line body, cap 25 → flagged at 30 > 25
            content = f"def too_long():\n{_func_body(30)}\n"
            _make_repo_with_main_and_feature(tmp, {}, {"core/foo.py": content})

            result = _invoke(tmp, max_lines=25)

            self.assertEqual(result.returncode, 1, f"stderr={result.stderr!r}")
            self.assertIn('"function": "too_long"', result.stdout)
            self.assertIn('"lines": 30', result.stdout)
            self.assertIn('"max_lines": 25', result.stdout)

    def test_async_function_also_checked(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            content = f"async def async_long():\n{_func_body(30)}\n"
            _make_repo_with_main_and_feature(tmp, {}, {"core/foo.py": content})

            result = _invoke(tmp, max_lines=25)

            self.assertEqual(result.returncode, 1, f"stderr={result.stderr!r}")
            self.assertIn('"function": "async_long"', result.stdout)

    def test_docstring_not_counted_against_body(self) -> None:
        # Body of [docstring, then 25 stmts] is measured as 25 (within cap),
        # not 26 (would exceed). Docstring expression is skipped per AST walk.
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            content = f'def with_doc():\n    """one-liner docstring."""\n{_func_body(25)}\n'
            _make_repo_with_main_and_feature(tmp, {}, {"core/foo.py": content})

            result = _invoke(tmp, max_lines=25)

            self.assertEqual(
                result.returncode,
                0,
                f"docstring should not push body over cap; stdout={result.stdout!r}",
            )


class TouchedScopeTests(unittest.TestCase):
    """Pre-existing functions that the diff doesn't touch are NOT flagged."""

    def test_preexisting_long_function_untouched_not_flagged(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            # 30-line `pre_existing` is on main; on feature, we add a new
            # short function `new_short`. The diff touches `new_short`'s
            # lines, not `pre_existing`'s. Checker should not flag the
            # untouched pre-existing function.
            baseline = f"def pre_existing():\n{_func_body(30)}\n"
            updated = baseline + "\n\ndef new_short():\n    x = 0\n    y = 1\n"
            _make_repo_with_main_and_feature(
                tmp,
                {"core/foo.py": baseline},
                {"core/foo.py": updated},
            )

            result = _invoke(tmp, max_lines=25)

            self.assertEqual(
                result.returncode,
                0,
                f"untouched pre-existing function should not flag; stdout={result.stdout!r}",
            )

    def test_modifying_a_line_in_long_function_flags_it(self) -> None:
        """Touching even one line in a function whose total body exceeds the
        cap surfaces the violation — the story should know about it."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            # Build a 30-line body where one statement is modified on feature
            stmts = "\n".join(f"    x = {i}" for i in range(30))
            baseline = f"def big_one():\n{stmts}\n"
            modified_stmts = "\n".join(
                f"    x = {i}" if i != 15 else "    x = 999" for i in range(30)
            )
            updated = f"def big_one():\n{modified_stmts}\n"

            _make_repo_with_main_and_feature(
                tmp,
                {"core/foo.py": baseline},
                {"core/foo.py": updated},
            )

            result = _invoke(tmp, max_lines=25)

            self.assertEqual(result.returncode, 1, f"stderr={result.stderr!r}")
            self.assertIn('"function": "big_one"', result.stdout)


if __name__ == "__main__":
    unittest.main()
