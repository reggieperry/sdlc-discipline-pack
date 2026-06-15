"""Tests for sdlc-slop-inputs.sh (G0 Arm-C extraction of slop step 5).

Assembles the audit inputs for the generative call: the cumulative diff
(``git diff origin/$TARGET...HEAD``), the reviewer's prior verdict (the
review file), and — when present — the story spec (the story file). Each
section is emitted to STDOUT under a clear delimiter header.

Black-box subprocess against a real tempdir git repo (main + origin/main
ref + a feature commit with a diff) plus a ``bd`` spy on PATH that
dispatches ``bd show <STORY_ID> --json``. ``jq`` is real; only ``bd`` is
spied. stdlib-only. Matches pack convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_sdlc_slop_inputs -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spies import spy_bd_dispatch  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "sdlc-slop-inputs.sh"


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
    """Repo with a baseline commit on main + origin/main ref + a feature commit.

    The feature commit adds a recognizable hunk so the test can assert the
    script's diff section carries it.
    """
    _git(tmp, "init", "-q", "-b", "main")
    (tmp / "core").mkdir()
    (tmp / "core" / "foo.py").write_text("def x():\n    return 1\n")
    _git(tmp, "add", "core/foo.py")
    _git(tmp, "commit", "-q", "-m", "baseline")
    _git(tmp, "update-ref", "refs/remotes/origin/main", "main")
    _git(tmp, "checkout", "-q", "-b", "feature")
    # A recognizable added line on the feature branch.
    (tmp / "core" / "foo.py").write_text(
        "def x():\n    return 1\n\n\ndef SENTINEL_ADDED():\n    return 99\n"
    )
    _git(tmp, "add", "core/foo.py")
    _git(tmp, "commit", "-q", "-m", "feature change")


def _bead_json(review_file: str, story_file: str | None) -> str:
    metadata: dict[str, str] = {"review_file": review_file}
    if story_file is not None:
        metadata["story_file"] = story_file
    return json.dumps([{"id": "EL-1", "metadata": metadata}])


def _invoke(tmp: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{tmp}:{env['PATH']}"
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=tmp,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


class InputsTests(unittest.TestCase):
    def test_emits_diff_review_and_story(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _setup_repo(tmp)
            review = tmp / "review.md"
            review.write_text("REVIEWER_SENTINEL verdict body\n")
            story = tmp / "story.md"
            story.write_text("STORY_SENTINEL spec body\n")
            spy_bd_dispatch(
                tmp, {"EL-1": _bead_json(str(review), str(story))}
            )

            result = _invoke(tmp, "EL-1", "main")

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            out = result.stdout
            # Section headers present.
            self.assertIn("=== DIFF ===", out)
            self.assertIn("=== REVIEWER VERDICT ===", out)
            self.assertIn("=== STORY SPEC ===", out)
            # Diff hunk content.
            self.assertIn("SENTINEL_ADDED", out, f"diff hunk missing; out={out!r}")
            # Review file content.
            self.assertIn("REVIEWER_SENTINEL", out)
            # Story file content.
            self.assertIn("STORY_SENTINEL", out)

    def test_omits_story_section_when_absent(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _setup_repo(tmp)
            review = tmp / "review.md"
            review.write_text("REVIEWER_SENTINEL verdict body\n")
            # No story_file key in the metadata at all.
            spy_bd_dispatch(tmp, {"EL-1": _bead_json(str(review), None)})

            result = _invoke(tmp, "EL-1", "main")

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            out = result.stdout
            self.assertIn("=== DIFF ===", out)
            self.assertIn("=== REVIEWER VERDICT ===", out)
            self.assertIn("REVIEWER_SENTINEL", out)
            self.assertNotIn(
                "=== STORY SPEC ===",
                out,
                f"story section must be omitted when story_file absent; out={out!r}",
            )

    def test_missing_story_id_fails(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _setup_repo(tmp)
            result = _invoke(tmp)  # no STORY_ID, no TARGET
            self.assertNotEqual(
                result.returncode, 0, "missing STORY_ID must fail, not silently no-op"
            )

    def test_missing_target_fails(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _setup_repo(tmp)
            result = _invoke(tmp, "EL-1")  # STORY_ID but no TARGET
            self.assertNotEqual(
                result.returncode, 0, "missing TARGET must fail, not silently no-op"
            )


if __name__ == "__main__":
    unittest.main()
