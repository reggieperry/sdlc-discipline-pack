"""Tests for sdlc-slop-metadata.sh (G0 Arm-C extraction of slop step 2).

Records the slop-reviewer phase's start metadata on the story bead:
``session_id`` and ``started_at``. Black-box subprocess with a ``bd`` spy on
PATH that records argv. stdlib-only. Matches pack convention.

Run with::

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spies import spy_recorder  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "sdlc-slop-metadata.sh"


def _invoke(tmp: Path, *args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{tmp}:{env['PATH']}"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=tmp,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


class MetadataTests(unittest.TestCase):
    def test_records_session_id_and_started_at(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            spy_recorder(tmp, "bd")
            result = _invoke(tmp, "EL-1", env_extra={"GC_SESSION_ID": "sess-123"})
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            log = (tmp / "bd-argv.log").read_text()
            self.assertIn("update EL-1", log, f"bd update not called for the story; log={log!r}")
            self.assertIn("slop-reviewer.session_id=sess-123", log)
            self.assertIn("slop-reviewer.started_at=", log)

    def test_session_id_defaults_to_unknown(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            spy_recorder(tmp, "bd")
            # No GC_SESSION_ID in the environment.
            result = _invoke(tmp, "EL-2", env_extra={"GC_SESSION_ID": ""})
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            log = (tmp / "bd-argv.log").read_text()
            self.assertIn("slop-reviewer.session_id=unknown", log)

    def test_missing_story_id_fails(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            spy_recorder(tmp, "bd")
            result = _invoke(tmp)  # no STORY_ID
            self.assertNotEqual(result.returncode, 0, "missing STORY_ID must fail, not silently no-op")
            self.assertFalse((tmp / "bd-argv.log").exists(), "must not call bd without a story id")


if __name__ == "__main__":
    unittest.main()
