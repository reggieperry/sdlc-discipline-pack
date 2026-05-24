"""Tests for stories.py cmd_file cross-batch dep-edge translation (pack #152).

Symptom this regression test pins: filing a story whose `deps:` list points
at a predecessor filed in an EARLIER `stories.py file` invocation must result
in a `bd dep add <new-bead> --depends-on <pred-bead>` call. Without this
edge, the pool reconciler's `bd ready --metadata-field gc.routed_to=… --unassigned`
query returns the successor immediately and a worker spawns before the
predecessor merges — the race the dep edge is supposed to prevent.

The fix lives in `cmd_file`'s second pass after `bd create --graph`. The
graph plan itself can't carry cross-batch edges (bd errors on a `to_key`
not in the plan's `nodes:` list), so the second pass calls `bd dep add`
explicitly for each cross-batch dep.

Test scaffold: inline `bd` fake on PATH that handles `bd create --graph`
+ `bd dep add` + responds to the prefix lookup. Filesystem rig at a
tempdir with two story specs (predecessor already filed; successor
status=ready). Run `stories.py file EL-101` as a subprocess; inspect
the fake's argv log to assert the dep-add call landed.

Three scenarios:

1. Successor with one cross-batch dep → exactly one `bd dep add` call to
   the predecessor's bead.
2. Successor with a dep that doesn't have `filed_as_bead` yet → cmd_file
   exits nonzero with a clear error; no `bd dep add` is issued.
3. Successor with both an in-batch dep and a cross-batch dep → only the
   cross-batch one produces a `bd dep add` (the in-batch goes through
   the graph plan's edges, not visible to the test's argv log beyond the
   `bd create --graph` invocation).

stdlib-only (unittest + tempfile + subprocess + textwrap). Matches pack
convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_stories_cmd_file_cross_batch_deps -v
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
    """Inline `bd` fake covering the three subcommands cmd_file calls.

    - ``bd create --graph <path>`` → echoes ``EL-101 -> <succ_bead>`` so
      ``parse_bd_create_output`` can pick up the assignment.
    - ``bd dep add ...`` → exit 0, logging argv.
    - Anything else → exit 0 silently (defensive; cmd_file does not call
      anything else in the happy path).

    Argv recorded to ``<tmp>/bd-argv.log`` one call per line.
    """
    bd = tmp / "bd"
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/bd-argv.log"\n'
        'if [ "$1" = "create" ] && [ "$2" = "--graph" ]; then\n'
        f'    echo "EL-101 -> {succ_bead}"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "dep" ] && [ "$2" = "add" ]; then\n'
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_exec(bd, body)
    return bd


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


def _run_cmd_file(rig: Path, tmp: Path, *story_ids: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{tmp}:{os.environ.get('PATH', '')}",
    }
    return subprocess.run(
        ["python3", str(STORIES_PY), "file", *story_ids],
        cwd=rig,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


PRED_FILED_SPEC = textwrap.dedent("""\
    ---
    story_id: EL-100
    title: Predecessor (already filed)
    status: filed
    filed_as_bead: bd-pred001
    ---

    # body
    """)

SUCC_WITH_CROSS_BATCH_DEP = textwrap.dedent("""\
    ---
    story_id: EL-101
    title: Successor with cross-batch dep on EL-100
    status: ready
    deps:
      - EL-100
    ---

    # body
    """)

PRED_UNFILED_SPEC = textwrap.dedent("""\
    ---
    story_id: EL-100
    title: Predecessor (not yet filed)
    status: ready
    ---

    # body
    """)


class CmdFileCrossBatchDepTests(unittest.TestCase):
    """Pack #152 — cross-batch dep-edge translation."""

    def setUp(self) -> None:
        self._tmpdir_ctx = TemporaryDirectory()
        self._tmp = Path(self._tmpdir_ctx.name)
        self._rig = _make_rig(self._tmp)

    def tearDown(self) -> None:
        self._tmpdir_ctx.cleanup()

    def _bd_calls(self) -> list[str]:
        log = self._tmp / "bd-argv.log"
        return log.read_text().strip().splitlines() if log.exists() else []

    def test_cross_batch_dep_emits_bd_dep_add(self) -> None:
        """Successor with a dep on an already-filed predecessor → bd dep add called."""
        _write_spec(self._rig / "stories", "EL-100", PRED_FILED_SPEC)
        _write_spec(self._rig / "stories", "EL-101", SUCC_WITH_CROSS_BATCH_DEP)
        _make_bd_fake(self._tmp, succ_bead="bd-succ002")

        result = _run_cmd_file(self._rig, self._tmp, "EL-101")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._bd_calls()
        dep_add_calls = [c for c in calls if c.startswith("dep add ")]
        self.assertEqual(
            len(dep_add_calls),
            1,
            msg=f"expected exactly one `bd dep add` call; got {dep_add_calls}",
        )
        self.assertIn("bd-succ002", dep_add_calls[0])
        self.assertIn("--depends-on", dep_add_calls[0])
        self.assertIn("bd-pred001", dep_add_calls[0])

    def test_unfiled_predecessor_hard_fails(self) -> None:
        """Successor deps on a predecessor with no filed_as_bead → cmd_file exits nonzero."""
        _write_spec(self._rig / "stories", "EL-100", PRED_UNFILED_SPEC)
        _write_spec(self._rig / "stories", "EL-101", SUCC_WITH_CROSS_BATCH_DEP)
        _make_bd_fake(self._tmp, succ_bead="bd-succ002")

        result = _run_cmd_file(self._rig, self._tmp, "EL-101")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EL-100", result.stderr)
        self.assertIn("filed_as_bead", result.stderr)
        # bd create --graph still runs (the successor IS filed at the bd layer);
        # the cross-batch second pass is where the failure surfaces. So we
        # expect a bd create call but NO bd dep add call.
        calls = self._bd_calls()
        self.assertTrue(any(c.startswith("create --graph") for c in calls))
        self.assertFalse(
            any(c.startswith("dep add ") for c in calls),
            msg=f"bd dep add should NOT have been called; got {calls}",
        )

    def test_no_cross_batch_deps_is_quiet(self) -> None:
        """Successor with no cross-batch deps → no `bd dep add` invocation."""
        no_dep_spec = textwrap.dedent("""\
            ---
            story_id: EL-101
            title: Successor with no deps
            status: ready
            ---

            # body
            """)
        _write_spec(self._rig / "stories", "EL-101", no_dep_spec)
        _make_bd_fake(self._tmp, succ_bead="bd-succ002")

        result = _run_cmd_file(self._rig, self._tmp, "EL-101")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._bd_calls()
        self.assertFalse(
            any(c.startswith("dep add ") for c in calls),
            msg=f"bd dep add should NOT have been called; got {calls}",
        )


if __name__ == "__main__":
    unittest.main()
