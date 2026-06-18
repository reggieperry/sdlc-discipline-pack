"""Tests for stories.py cmd_validate resolving deps against stories/_archive/.

Symptom: a dependency on a merged predecessor — one moved to stories/_archive/
by `stories.py archive` — produced a spurious
`dep '<id>' does not match any story_id` error, because cmd_validate built its
known-id set from active stories/ only. Observed 2026-06-18 on the Elder
backlog: 19 such errors after the calibration predecessors EL-273/EL-274 merged
and were archived, while their still-active successors (EL-276/EL-278/EL-280)
declared them as deps.

The fix: resolve deps against active story_ids PLUS the story_ids in
stories/_archive/ (closed/merged predecessors), without re-validating the
archived specs themselves. A genuinely dangling dep (no active or archived
match) must still error.

Black-box subprocess tests against the real stories.py; stdlib-only, matches
pack convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_stories_validate_archived_deps -v
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STORIES_PY = (
    Path(__file__).resolve().parents[3]
    / "overlay"
    / "per-provider"
    / "claude"
    / ".claude"
    / "sdlc-discipline"
    / "stories.py"
)
assert STORIES_PY.exists(), f"stories.py not found at {STORIES_PY}"


def _spec(story_id: str, status: str = "ready", deps: list[str] | None = None) -> str:
    lines = [
        "---",
        f"story_id: {story_id}",
        f"title: {story_id} title",
        "phase: 3",
        f"status: {status}",
    ]
    if deps:
        lines.append("deps:")
        lines += [f"  - {d}" for d in deps]
    lines += ["---", "", "# body", ""]
    return "\n".join(lines)


def _run_validate(rig: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(STORIES_PY), "validate"],
        cwd=rig,
        env={**os.environ},
        capture_output=True,
        text=True,
    )


class ArchivedDepResolutionTests(unittest.TestCase):
    """A dep on a story already moved to stories/_archive/ must resolve."""

    def test_dep_to_archived_story_resolves(self) -> None:
        with TemporaryDirectory() as d:
            rig = Path(d)
            (rig / "stories" / "_archive").mkdir(parents=True)
            (rig / "stories" / "EL-300-active.md").write_text(_spec("EL-300", deps=["EL-299"]))
            (rig / "stories" / "_archive" / "EL-299-merged.md").write_text(
                _spec("EL-299", status="closed")
            )
            result = _run_validate(rig)
            self.assertNotIn(
                "EL-299",
                result.stderr,
                f"dep on archived EL-299 should resolve, not error.\nstderr:\n{result.stderr}",
            )
            self.assertEqual(
                result.returncode, 0, f"validate should pass.\nstderr:\n{result.stderr}"
            )

    def test_dep_to_truly_missing_story_still_errors(self) -> None:
        """Guard: the archive resolution must not mask a genuinely dangling dep."""
        with TemporaryDirectory() as d:
            rig = Path(d)
            (rig / "stories").mkdir()
            (rig / "stories" / "EL-300-active.md").write_text(_spec("EL-300", deps=["EL-999"]))
            result = _run_validate(rig)
            self.assertIn("EL-999", result.stderr)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
