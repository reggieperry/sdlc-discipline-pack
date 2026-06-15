"""Tests for sdlc-slop-skip-emit.sh (G0 Arm-C extraction of slop step 4, skip branch).

When the skip-trivial gate decides to skip, this script emits the skip trailer
into the bead's review file, commits + pushes it, then routes the story to the
documenter (status=open, assignee cleared, completed_at + skipped metadata +
gc.routed_to) and drains.

Black-box subprocess against a real tempdir git repo with a bare ``origin``
remote (so add/commit/push are genuinely exercised), a ``bd`` spy that resolves
the review_file via ``bd show <id> --json | jq`` and records the ``bd update``
argv, and a ``gc`` spy that records ``gc runtime drain-ack``. jq is real.

stdlib-only. Matches pack convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_sdlc_slop_skip_emit -v
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
from _spies import spy_bd_dispatch, spy_recorder  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "sdlc-slop-skip-emit.sh"

STORY_ID = "EL-1"


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


def _setup_repo(tmp: Path) -> tuple[Path, str]:
    """Build a work repo on branch ``feature`` with a bare ``origin`` remote.

    Returns (work_dir, review_file_relpath). The review file is committed on
    the baseline so the skip-emit append has something to modify.
    """
    bare = tmp / "origin.git"
    work = tmp / "work"
    _git(tmp, "init", "-q", "--bare", str(bare))
    _git(tmp, "init", "-q", "-b", "main", str(work))
    review_rel = "reviews/EL-1-review.md"
    review = work / review_rel
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text("# Review for EL-1\n\nprior reviewer verdict\n")
    _git(work, "add", review_rel)
    _git(work, "commit", "-q", "-m", "baseline review")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-q", "origin", "main")
    _git(work, "checkout", "-q", "-b", "feature")
    _git(work, "push", "-q", "-u", "origin", "feature")
    return work, review_rel


def _bead_json(review_relpath: str) -> str:
    return json.dumps([{"metadata": {"review_file": review_relpath}}])


def _invoke(
    work: Path,
    tmp: Path,
    *args: str,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{tmp}{os.pathsep}{env['PATH']}"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )


class SkipEmitHappyPathTests(unittest.TestCase):
    def test_appends_skip_trailer_to_review_file(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            work, review_rel = _setup_repo(tmp)
            spy_bd_dispatch(tmp, {STORY_ID: _bead_json(review_rel)})
            spy_recorder(tmp, "gc")
            result = _invoke(work, tmp, STORY_ID, "feature", env_extra={"GC_RIG": "elder"})
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            review_text = (work / review_rel).read_text()
            self.assertIn("## Slop trailer", review_text)
            self.assertIn('"skipped": true', review_text)
            self.assertIn('"reason"', review_text)

    def test_commits_and_pushes_review_file(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            work, review_rel = _setup_repo(tmp)
            spy_bd_dispatch(tmp, {STORY_ID: _bead_json(review_rel)})
            spy_recorder(tmp, "gc")
            result = _invoke(work, tmp, STORY_ID, "feature", env_extra={"GC_RIG": "elder"})
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            # The HEAD commit message records the skip.
            log = _git(work, "log", "-1", "--pretty=%s").stdout
            self.assertIn(STORY_ID, log)
            self.assertIn("skip", log.lower())
            # The trailer reached the remote (origin/feature == HEAD).
            local_head = _git(work, "rev-parse", "HEAD").stdout.strip()
            remote_head = _git(work, "rev-parse", "origin/feature").stdout.strip()
            self.assertEqual(local_head, remote_head, "review commit was not pushed")

    def test_routes_to_documenter_with_skip_metadata(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            work, review_rel = _setup_repo(tmp)
            spy_bd_dispatch(tmp, {STORY_ID: _bead_json(review_rel)})
            spy_recorder(tmp, "gc")
            result = _invoke(work, tmp, STORY_ID, "feature", env_extra={"GC_RIG": "elder"})
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            bd_log = (tmp / "bd-argv.log").read_text()
            self.assertIn(f"update {STORY_ID}", bd_log, f"bd update not called; log={bd_log!r}")
            self.assertIn("--status=open", bd_log)
            self.assertIn("slop-reviewer.skipped=true", bd_log)
            self.assertIn("slop-reviewer.completed_at=", bd_log)
            self.assertIn("gc.routed_to=elder/sdlc-discipline.documenter", bd_log)

    def test_calls_drain_ack(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            work, review_rel = _setup_repo(tmp)
            spy_bd_dispatch(tmp, {STORY_ID: _bead_json(review_rel)})
            spy_recorder(tmp, "gc")
            result = _invoke(work, tmp, STORY_ID, "feature", env_extra={"GC_RIG": "elder"})
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            gc_log = (tmp / "gc-argv.log").read_text()
            self.assertIn("runtime drain-ack", gc_log, f"gc drain-ack not called; log={gc_log!r}")


class SkipEmitFailClosedTests(unittest.TestCase):
    def test_missing_story_id_fails(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            work, _ = _setup_repo(tmp)
            spy_bd_dispatch(tmp, {})
            spy_recorder(tmp, "gc")
            result = _invoke(work, tmp)  # no args at all
            self.assertNotEqual(result.returncode, 0, "missing STORY_ID must fail, not silently no-op")
            self.assertFalse((tmp / "bd-argv.log").exists(), "must not touch bd without a story id")
            self.assertFalse((tmp / "gc-argv.log").exists(), "must not drain without a story id")

    def test_missing_branch_fails(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            work, _ = _setup_repo(tmp)
            spy_bd_dispatch(tmp, {})
            spy_recorder(tmp, "gc")
            result = _invoke(work, tmp, STORY_ID)  # STORY_ID but no BRANCH
            self.assertNotEqual(result.returncode, 0, "missing BRANCH must fail, not silently no-op")
            self.assertFalse((tmp / "gc-argv.log").exists(), "must not drain without a branch")


if __name__ == "__main__":
    unittest.main()
