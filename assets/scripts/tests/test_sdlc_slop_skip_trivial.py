"""Tests for sdlc-slop-skip-trivial.sh (pack #78 v1).

Black-box subprocess against a tempdir git repo with main + feature
branch + origin/main remote-tracking ref. Asserts exit 0 (skip) vs
exit 1 (run slop pass) for various diff shapes.

stdlib-only. Matches pack convention.

Run with::

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT = Path(__file__).resolve().parent.parent / "sdlc-slop-skip-trivial.sh"
assert SCRIPT.exists(), f"sdlc-slop-skip-trivial.sh not found at {SCRIPT}"


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


def _setup_repo(tmp: Path) -> None:
    """Create repo with one baseline commit on main + origin/main ref."""
    _git(tmp, "init", "-q", "-b", "main")
    (tmp / "README.md").write_text("baseline\n")
    _git(tmp, "add", "README.md")
    _git(tmp, "commit", "-q", "-m", "baseline")
    _git(tmp, "update-ref", "refs/remotes/origin/main", "main")
    _git(tmp, "checkout", "-q", "-b", "feature")


def _commit(tmp: Path, files: dict[str, str], msg: str = "feature") -> None:
    for path, content in files.items():
        full = tmp / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        _git(tmp, "add", path)
    _git(tmp, "commit", "-q", "-m", msg)


def _invoke(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), "--target", "main"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
    )


class TrivialAllowlistTests(unittest.TestCase):
    """Diffs that touch only allowlisted files → skip."""

    def test_empty_diff_skips(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _setup_repo(tmp)
            result = _invoke(tmp)
            self.assertEqual(result.returncode, 0, f"empty diff → skip; stderr={result.stderr!r}")

    def test_story_frontmatter_only_skips(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _setup_repo(tmp)
            _commit(tmp, {"stories/EL-1-test.md": "---\nstatus: closed\n---\n"})
            result = _invoke(tmp)
            self.assertEqual(result.returncode, 0, f"stories-only → skip; stderr={result.stderr!r}")

    def test_archive_moves_only_skips(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _setup_repo(tmp)
            _commit(tmp, {"stories/_archive/EL-1-old.md": "---\nstatus: closed\n---\n"})
            result = _invoke(tmp)
            self.assertEqual(result.returncode, 0, f"archive-only → skip; stderr={result.stderr!r}")

    def test_dep_bump_only_skips(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _setup_repo(tmp)
            _commit(
                tmp,
                {
                    "pyproject.toml": '[project]\nname = "x"\nversion = "0.2"\n',
                    "uv.lock": "lock = '0.2'\n",
                },
            )
            result = _invoke(tmp)
            self.assertEqual(
                result.returncode, 0, f"dep-bump-only → skip; stderr={result.stderr!r}"
            )


class CodeDiffTests(unittest.TestCase):
    """Diffs that touch real code files → run the slop pass."""

    def test_python_code_change_runs_slop(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _setup_repo(tmp)
            # 20 lines of real Python — well above the 10-LOC threshold AND
            # with non-comment statements.
            body = "\n".join(f"x_{i} = {i}" for i in range(20))
            _commit(tmp, {"core/foo.py": body + "\n"})
            result = _invoke(tmp)
            self.assertEqual(
                result.returncode, 1, f"code diff → run slop; stderr={result.stderr!r}"
            )

    def test_mixed_code_and_doc_runs_slop(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _setup_repo(tmp)
            body = "\n".join(f"x_{i} = {i}" for i in range(15))
            _commit(
                tmp,
                {
                    "stories/EL-1.md": "---\nstatus: ready\n---\n",
                    "core/foo.py": body + "\n",
                },
            )
            result = _invoke(tmp)
            self.assertEqual(
                result.returncode,
                1,
                f"mixed code+doc → run slop (any code file disqualifies allowlist); stderr={result.stderr!r}",
            )


class SmallDiffTests(unittest.TestCase):
    """Small diffs where the only added lines are blank or comment-only → skip."""

    def test_small_comment_only_addition_skips(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _setup_repo(tmp)
            # Pre-create a code file on main so the diff can MODIFY it
            # with comment-only additions.
            _git(tmp, "checkout", "-q", "main")
            (tmp / "core").mkdir()
            (tmp / "core" / "foo.py").write_text("def x():\n    return 1\n")
            _git(tmp, "add", "core/foo.py")
            _git(tmp, "commit", "-q", "-m", "baseline-code")
            _git(tmp, "update-ref", "refs/remotes/origin/main", "main")
            _git(tmp, "checkout", "-q", "feature")
            _git(tmp, "rebase", "-q", "main")
            # Now feature-branch the file with only comment additions
            (tmp / "core" / "foo.py").write_text(
                "# A comment header.\n# Another comment.\ndef x():\n    return 1\n"
            )
            _git(tmp, "add", "core/foo.py")
            _git(tmp, "commit", "-q", "-m", "doc-only")

            result = _invoke(tmp)

            self.assertEqual(
                result.returncode,
                0,
                f"small comment-only addition → skip; stderr={result.stderr!r}",
            )


if __name__ == "__main__":
    unittest.main()
