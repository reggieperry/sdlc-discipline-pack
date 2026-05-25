"""Tests for stories.py cmd_file --kickoff flag (pack #158).

Symptom this regression test pins: filing a story creates the bead but does
not set `gc.routed_to`. Without a follow-up kickoff invocation, the bead is
invisible to the chain pool reconciler and no worker spawns. The --kickoff
flag composes file + kickoff so the operator doesn't have to remember the
two-step sequence.

The flag is opt-in for v1 (default off) — existing call sites without the
flag must not change behavior.

Test scaffold: inline `bd` fake on PATH for the bead-create step, inline
fake kickoff script via STORIES_KICKOFF_OVERRIDE env var. The fake kickoff
records its argv to a log so tests can assert per-bead invocation count
and per-bead success/failure. Filesystem rig at a tempdir with one
status=ready story spec.

Three scenarios:

1. --kickoff flag absent → no kickoff invocation, current behavior unchanged.
2. --kickoff flag set, kickoff succeeds → one kickoff call per filed bead,
   "kickoff: <bead> OK" surfaced in stdout, exit zero.
3. --kickoff flag set, kickoff fails → "kickoff: <bead> FAILED — <stderr>"
   surfaced in stderr, exit non-zero; bead still filed (no rollback).

stdlib-only (unittest + tempfile + subprocess + textwrap). Matches pack
convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_stories_cmd_file_kickoff_flag -v
"""

from __future__ import annotations

import os
import stat
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STORIES_PY = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "overlay"
    / "per-provider"
    / "claude"
    / ".claude"
    / "sdlc-discipline"
    / "stories.py"
)
assert STORIES_PY.exists(), f"stories.py not found at {STORIES_PY}"


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_bd_fake(tmp: Path, succ_bead: str) -> Path:
    """Inline bd fake covering bd create --graph. Argv logged to bd-argv.log."""
    bd = tmp / "bd"
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/bd-argv.log"\n'
        'if [ "$1" = "create" ] && [ "$2" = "--graph" ]; then\n'
        f'    echo "EL-101 -> {succ_bead}"\n'
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_exec(bd, body)
    return bd


def _make_kickoff_fake(tmp: Path, *, exit_code: int = 0, stderr_line: str = "") -> Path:
    """Inline kickoff fake. Records argv to kickoff-argv.log. Exit and stderr
    are configurable per test."""
    kickoff = tmp / "fake-kickoff.sh"
    stderr_emit = f'echo "{stderr_line}" >&2\n' if stderr_line else ""
    body = f'#!/bin/bash\necho "$@" >> "{tmp}/kickoff-argv.log"\n{stderr_emit}exit {exit_code}\n'
    _write_exec(kickoff, body)
    return kickoff


def _write_spec(stories_dir: Path, story_id: str, body: str) -> Path:
    path = stories_dir / f"{story_id}-test.md"
    path.write_text(body)
    return path


def _make_rig(tmp: Path) -> Path:
    rig = tmp / "rig"
    rig.mkdir()
    (rig / "stories").mkdir()
    beads_dir = rig / ".beads"
    beads_dir.mkdir()
    (beads_dir / "config.yaml").write_text('issue-prefix: "bd"\n')
    return rig


def _run_cmd_file(
    rig: Path, tmp: Path, *args: str, kickoff_override: Path | None = None
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{tmp}:{os.environ.get('PATH', '')}",
    }
    if kickoff_override is not None:
        env["STORIES_KICKOFF_OVERRIDE"] = str(kickoff_override)
    return subprocess.run(
        ["python3", str(STORIES_PY), "file", *args],
        cwd=rig,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


SINGLE_READY_SPEC = textwrap.dedent("""\
    ---
    story_id: EL-101
    title: Single ready story
    status: ready
    ---

    # body
    """)


class CmdFileKickoffFlagTests(unittest.TestCase):
    """Pack #158 — --kickoff flag composes file + kickoff in one call."""

    def setUp(self) -> None:
        self._tmpdir_ctx = TemporaryDirectory()
        self._tmp = Path(self._tmpdir_ctx.name)
        self._rig = _make_rig(self._tmp)

    def tearDown(self) -> None:
        self._tmpdir_ctx.cleanup()

    def _kickoff_calls(self) -> list[str]:
        log = self._tmp / "kickoff-argv.log"
        return log.read_text().strip().splitlines() if log.exists() else []

    def test_no_flag_means_no_kickoff(self) -> None:
        """Default behavior unchanged: filing without --kickoff doesn't invoke
        the kickoff script."""
        _write_spec(self._rig / "stories", "EL-101", SINGLE_READY_SPEC)
        _make_bd_fake(self._tmp, succ_bead="bd-succ001")
        kickoff = _make_kickoff_fake(self._tmp)

        result = _run_cmd_file(self._rig, self._tmp, "EL-101", kickoff_override=kickoff)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            self._kickoff_calls(),
            [],
            msg=f"kickoff should NOT have run without --kickoff; got {self._kickoff_calls()}",
        )

    def test_kickoff_flag_invokes_kickoff_once_per_bead(self) -> None:
        """With --kickoff, the kickoff script runs once per newly-assigned bead.
        Successful kickoff is surfaced as 'kickoff: <bead> OK' in stdout.
        """
        _write_spec(self._rig / "stories", "EL-101", SINGLE_READY_SPEC)
        _make_bd_fake(self._tmp, succ_bead="bd-succ001")
        kickoff = _make_kickoff_fake(self._tmp, exit_code=0)

        result = _run_cmd_file(
            self._rig, self._tmp, "EL-101", "--kickoff", kickoff_override=kickoff
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._kickoff_calls()
        self.assertEqual(len(calls), 1, msg=f"expected exactly one kickoff invocation; got {calls}")
        self.assertEqual(calls[0], "bd-succ001", msg="kickoff invoked with the new bead id")
        self.assertIn("kickoff: bd-succ001 OK", result.stdout)

    def test_kickoff_failure_surfaces_per_bead_and_sets_nonzero_exit(self) -> None:
        """When kickoff fails, the bead is still filed (no rollback), the
        failure is surfaced per-bead with the stderr summary, and the function
        exits non-zero so the operator notices.
        """
        _write_spec(self._rig / "stories", "EL-101", SINGLE_READY_SPEC)
        _make_bd_fake(self._tmp, succ_bead="bd-succ001")
        kickoff = _make_kickoff_fake(
            self._tmp, exit_code=1, stderr_line="kickoff-internal-error: rig not found"
        )

        result = _run_cmd_file(
            self._rig, self._tmp, "EL-101", "--kickoff", kickoff_override=kickoff
        )

        self.assertNotEqual(result.returncode, 0, msg="non-zero exit expected on kickoff failure")
        self.assertIn("kickoff: bd-succ001 FAILED", result.stderr)
        self.assertIn("kickoff-internal-error: rig not found", result.stderr)
        # Filing itself succeeded: the spec frontmatter was updated by cmd_file
        # before the kickoff loop ran. Verify via the story-file writeback.
        spec_text = (self._rig / "stories" / "EL-101-test.md").read_text()
        self.assertIn("status: filed", spec_text)
        self.assertIn("filed_as_bead: bd-succ001", spec_text)


if __name__ == "__main__":
    unittest.main()
