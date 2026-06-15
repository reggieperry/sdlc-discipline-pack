"""Tests for sdlc-slop-finish.sh (G0 Arm-C extraction of slop steps 7+8).

Emits the real slop_trailer and hands off to the documenter:

  1. resolve ``review_file`` from the story bead,
  2. append a fenced ``## Slop trailer`` json block to the review file,
  3. git add / commit / push origin <branch>,
  4. ``bd update`` routing to the documenter with completed_at,
     findings_count, and gc.routed_to,
  5. ``gc runtime drain-ack``.

Black-box subprocess against a real tempdir git repo (with a bare
origin remote so the real push path is exercised), a ``bd`` spy that
dispatches ``bd show`` (review_file resolution) and records argv, and a
``gc`` recorder spy. ``jq`` is real. stdlib-only. Matches pack convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_sdlc_slop_finish -v
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

SCRIPT = Path(__file__).resolve().parent.parent / "sdlc-slop-finish.sh"

TRAILER = (
    '{\n'
    '  "skipped": false,\n'
    '  "model": "claude-opus-4-8",\n'
    '  "found": 2,\n'
    '  "by_severity": {"critical": 0, "high": 1, "medium": 1, "low": 0},\n'
    '  "findings": [\n'
    '    {"file": "core/foo.py:42-58", "category": "scope-creep", '
    '"severity": "high", "description": "x", "suggested_fix": "y"}\n'
    '  ]\n'
    '}'
)


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


def _setup_repo(tmp: Path) -> Path:
    """Create a work repo on branch ``feature`` with a bare ``origin`` remote.

    Seeds the review file so the script's append + commit has a real
    target. Returns the work-repo path (the script runs with cwd here).
    """
    bare = tmp / "origin.git"
    _git(tmp, "init", "-q", "--bare", str(bare))
    work = tmp / "work"
    _git(tmp, "init", "-q", "-b", "main", str(work))
    (work / "reviews").mkdir()
    (work / "reviews" / "EL-1.md").write_text("# Review for EL-1\n\nverdict: ok\n")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "baseline")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-q", "-u", "origin", "main")
    _git(work, "checkout", "-q", "-b", "feature")
    return work


def _bead_json(review_file: str) -> str:
    return json.dumps([{"metadata": {"review_file": review_file}}])


def _invoke(
    work: Path, spy_dir: Path, *args: str, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{spy_dir}:{env['PATH']}"
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


class FinishHappyPathTests(unittest.TestCase):
    def test_appends_trailer_routes_and_drains(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            work = _setup_repo(tmp)
            review_rel = "reviews/EL-1.md"
            spy_bd_dispatch(tmp, {"EL-1": _bead_json(review_rel)})
            spy_recorder(tmp, "gc")
            trailer_file = tmp / "trailer.json"
            trailer_file.write_text(TRAILER + "\n")

            result = _invoke(
                work,
                tmp,
                "EL-1",
                "feature",
                str(trailer_file),
                "2",
                env_extra={"GC_RIG": "elder"},
            )
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")

            # The fenced json trailer landed in the review file.
            review_text = (work / review_rel).read_text()
            self.assertIn("## Slop trailer", review_text)
            self.assertIn("```json", review_text)
            self.assertIn('"model": "claude-opus-4-8"', review_text)
            self.assertIn('"category": "scope-creep"', review_text)

            # bd update routed to documenter with the right metadata.
            bd_log = (tmp / "bd-argv.log").read_text()
            self.assertIn("update EL-1", bd_log)
            self.assertIn("--status=open", bd_log)
            self.assertIn("slop-reviewer.completed_at=", bd_log)
            self.assertIn("slop-reviewer.findings_count=2", bd_log)
            self.assertIn("gc.routed_to=elder/sdlc-discipline.documenter", bd_log)

            # gc runtime drain-ack was called.
            gc_log = (tmp / "gc-argv.log").read_text()
            self.assertIn("runtime drain-ack", gc_log)

    def test_trailer_is_pushed_to_origin(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            work = _setup_repo(tmp)
            review_rel = "reviews/EL-1.md"
            spy_bd_dispatch(tmp, {"EL-1": _bead_json(review_rel)})
            spy_recorder(tmp, "gc")
            trailer_file = tmp / "trailer.json"
            trailer_file.write_text(TRAILER + "\n")

            result = _invoke(work, tmp, "EL-1", "feature", str(trailer_file), "2")
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")

            # The feature branch reached origin with the trailer commit.
            bare = tmp / "origin.git"
            log = _git(bare, "log", "--oneline", "feature").stdout
            self.assertIn("slop-review: appended trailer for EL-1", log)


class FinishFailClosedTests(unittest.TestCase):
    def test_missing_all_args_fails(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            work = _setup_repo(tmp)
            spy_bd_dispatch(tmp, {"EL-1": _bead_json("reviews/EL-1.md")})
            spy_recorder(tmp, "gc")
            result = _invoke(work, tmp)
            self.assertNotEqual(result.returncode, 0, "missing args must fail-closed")
            self.assertFalse(
                (tmp / "gc-argv.log").exists(), "must not drain-ack on a bad invocation"
            )

    def test_missing_findings_count_fails(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            work = _setup_repo(tmp)
            spy_bd_dispatch(tmp, {"EL-1": _bead_json("reviews/EL-1.md")})
            spy_recorder(tmp, "gc")
            trailer_file = tmp / "trailer.json"
            trailer_file.write_text(TRAILER + "\n")
            # Only three of four required args.
            result = _invoke(work, tmp, "EL-1", "feature", str(trailer_file))
            self.assertNotEqual(result.returncode, 0, "missing findings count must fail")
            self.assertFalse((tmp / "gc-argv.log").exists())

    def test_unreadable_trailer_file_fails(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            work = _setup_repo(tmp)
            spy_bd_dispatch(tmp, {"EL-1": _bead_json("reviews/EL-1.md")})
            spy_recorder(tmp, "gc")
            missing = tmp / "does-not-exist.json"
            result = _invoke(work, tmp, "EL-1", "feature", str(missing), "2")
            self.assertNotEqual(
                result.returncode, 0, "unreadable trailer file must fail-closed"
            )
            self.assertFalse(
                (tmp / "gc-argv.log").exists(), "must not drain-ack when the trailer is unreadable"
            )


if __name__ == "__main__":
    unittest.main()
