"""Tests for sdlc-rule-checks/deferred_work_prose.py (pack #123).

Sets up a tempdir git repo with a baseline commit + feature branch
carrying deferred-work comments, then invokes the checker. Pins:

  - Bare `# TODO` without tracking ref → flagged
  - `# TODO (see #99)` → not flagged (tracking ref in same line)
  - `# Removal in v2.30` followed by `# tracked at #114` → not flagged
    (tracking ref within ±2 lines)
  - `# noqa: deferred-work` opt-out → not flagged
  - Pre-existing `# TODO` not in the diff → not flagged (scope = added lines only)
  - Bash files (`*.sh`) also scanned by default

stdlib-only (unittest + tempfile + subprocess + textwrap). Matches pack
convention (`test_function_body_length.py`).

Run with::

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

CHECKER = Path(__file__).resolve().parent.parent / "sdlc-rule-checks" / "deferred_work_prose.py"
assert CHECKER.exists(), f"deferred_work_prose.py not found at {CHECKER}"


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


def _make_repo(tmp: Path, baseline_files: dict[str, str], feature_files: dict[str, str]) -> None:
    """Init repo with `baseline_files` on `main` + `feature_files` on a feature branch."""
    _git(tmp, "init", "-q", "-b", "main")
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
    cwd: Path, diff_range: str = "main..HEAD", paths_include: list[str] | None = None
) -> subprocess.CompletedProcess:
    cmd = ["python3", str(CHECKER), "--diff-range", diff_range]
    if paths_include:
        for glob in paths_include:
            cmd.extend(["--paths-include", glob])
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=15)


class DeferredWorkProseTests(unittest.TestCase):
    def test_bare_todo_without_tracking_flagged(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo(
                tmp,
                baseline_files={},
                feature_files={"core/foo.py": "def f():\n    # TODO: handle edge\n    return 0\n"},
            )
            result = _invoke(tmp)
            self.assertEqual(result.returncode, 1, f"stderr={result.stderr!r}")
            findings = [json.loads(line) for line in result.stdout.strip().splitlines()]
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["file"], "core/foo.py")
            self.assertEqual(findings[0]["pattern"], "TODO")
            self.assertIn("file an issue or bead", findings[0]["remediation"])

    def test_todo_with_tracking_ref_passes(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo(
                tmp,
                baseline_files={},
                feature_files={
                    "core/foo.py": "def f():\n    # TODO: handle edge (see #99)\n    return 0\n"
                },
            )
            result = _invoke(tmp)
            self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r}")

    def test_removal_comment_with_nearby_tracking_ref_passes(self) -> None:
        """Tracking ref within ±2 lines suppresses the finding."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            body = "def f():\n    # Removal in v2.30\n    # tracked at #114\n    return 0\n"
            _make_repo(tmp, baseline_files={}, feature_files={"core/foo.py": body})
            result = _invoke(tmp)
            self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r}")

    def test_noqa_opt_out_passes(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo(
                tmp,
                baseline_files={},
                feature_files={
                    "core/foo.py": "def f():\n    # TODO informational  # noqa: deferred-work\n    return 0\n"
                },
            )
            result = _invoke(tmp)
            self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r}")

    def test_pre_existing_todo_not_flagged(self) -> None:
        """Pre-existing # TODO comments outside the diff are not retroactively flagged."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo(
                tmp,
                baseline_files={"core/foo.py": "def f():\n    # TODO: legacy\n    return 0\n"},
                feature_files={
                    # Feature commit adds a separate function — the TODO line is
                    # untouched.
                    "core/foo.py": (
                        "def f():\n    # TODO: legacy\n    return 0\n\ndef g():\n    return 1\n"
                    )
                },
            )
            result = _invoke(tmp)
            self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r}")

    def test_bash_file_also_scanned(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo(
                tmp,
                baseline_files={},
                feature_files={
                    "assets/scripts/foo.sh": "#!/bin/bash\n# TODO: rewrite the gate\nexit 0\n"
                },
            )
            result = _invoke(tmp)
            self.assertEqual(result.returncode, 1, f"stderr={result.stderr!r}")
            findings = [json.loads(line) for line in result.stdout.strip().splitlines()]
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["file"], "assets/scripts/foo.sh")

    def test_fixme_pattern_flagged(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo(
                tmp,
                baseline_files={},
                feature_files={
                    "core/foo.py": "def f():\n    # FIXME: this is broken\n    return 0\n"
                },
            )
            result = _invoke(tmp)
            self.assertEqual(result.returncode, 1, f"stderr={result.stderr!r}")
            findings = [json.loads(line) for line in result.stdout.strip().splitlines()]
            self.assertEqual(findings[0]["pattern"], "FIXME")

    def test_removal_in_v_pattern_flagged(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo(
                tmp,
                baseline_files={},
                feature_files={"core/foo.py": "# Removal in v2.30\nx = 1\n"},
            )
            result = _invoke(tmp)
            self.assertEqual(result.returncode, 1, f"stderr={result.stderr!r}")
            findings = [json.loads(line) for line in result.stdout.strip().splitlines()]
            self.assertEqual(len(findings), 1)
            self.assertIn("removal", findings[0]["pattern"].lower())

    def test_bead_id_recognized_as_tracking_ref(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo(
                tmp,
                baseline_files={},
                feature_files={
                    "core/foo.py": "def f():\n    # TODO refactor (el-abc123)\n    return 0\n"
                },
            )
            result = _invoke(tmp)
            self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r}")

    def test_el_story_id_recognized_as_tracking_ref(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_repo(
                tmp,
                baseline_files={},
                feature_files={
                    "core/foo.py": "def f():\n    # follow-up needed (EL-007)\n    return 0\n"
                },
            )
            result = _invoke(tmp)
            self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r}")


if __name__ == "__main__":
    unittest.main()
