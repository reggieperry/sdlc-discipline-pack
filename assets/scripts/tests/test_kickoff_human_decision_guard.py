"""Tests for the sdlc-kickoff requires_human_decision guard (pack #197).

Symptom this regression test pins: when a bead is parked for a human
decision (`requires_human_decision=true`, set by a phase that escalated a
decision the chain can't make — e.g. a gate block with no chain path), a
fresh `sdlc-kickoff` re-opens it and re-routes it to the worker pool,
re-spawning a worker that re-derives the identical failure and re-parks.
The flag was set but consumed by nothing; the pool admits on
`gc.routed_to` + `bd ready`, so any kickoff re-armed the loop. Observed on
Elder EL-173: three worker spawns into the same gate dead-end.

The guard: `sdlc-kickoff` reads `requires_human_decision` before routing
and refuses to re-route when it is `true` — it leaves the bead untouched,
logs the refusal, and exits 0. A non-`true` value (`resolved`, empty,
absent) does not block, so a resolved bead can be re-kicked normally.

Test scaffold: inline `bd` fake on PATH that answers `bd show <id> --json`
with configurable metadata and logs every `bd update` argv. Invoke the
real `commands/kickoff/run.sh` with `GC_RIG` set (so the rig-name lookup
is skipped). Assert on the bd-argv log + stdout/stderr + exit code.

stdlib-only (unittest + tempfile + subprocess). Matches pack convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_kickoff_human_decision_guard -v
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

KICKOFF = Path(__file__).resolve().parent.parent.parent.parent / "commands" / "kickoff" / "run.sh"
assert KICKOFF.exists(), f"kickoff run.sh not found at {KICKOFF}"

BEAD = "el-test001"
# The rig dir is created as tmp/rig; with GC_RIG unset, run.sh walks up to
# the .beads/ parent and falls back to the directory basename for the rig
# name. We deliberately do NOT set GC_RIG — it only overrides the rig name,
# not the directory discovery that sets DIR for the snapshot step.
RIG = "rig"
WORKER_TARGET = f"{RIG}/sdlc-discipline.worker"


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_bd_fake(tmp: Path, metadata: dict) -> None:
    """Inline bd fake. `bd show <id> --json` returns one bead carrying
    `metadata`; every invocation's argv is appended to bd-argv.log."""
    show_json = json.dumps([{"id": BEAD, "status": "blocked", "metadata": metadata}])
    bd = tmp / "bd"
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/bd-argv.log"\n'
        'if [ "$1" = "show" ]; then\n'
        f"    cat <<'JSON'\n{show_json}\nJSON\n"
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_exec(bd, body)


def _make_rig(tmp: Path) -> Path:
    rig = tmp / "rig"
    (rig / ".beads").mkdir(parents=True)
    return rig


def _run_kickoff(rig: Path, tmp: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{tmp}:{os.environ.get('PATH', '')}",
    }
    return subprocess.run(
        ["sh", str(KICKOFF), BEAD],
        cwd=rig,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


class KickoffHumanDecisionGuard(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = TemporaryDirectory()
        self._tmp = Path(self._ctx.name)
        self._rig = _make_rig(self._tmp)

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def _bd_calls(self) -> list[str]:
        log = self._tmp / "bd-argv.log"
        return log.read_text().splitlines() if log.exists() else []

    def _routed(self) -> bool:
        """True if any bd update set gc.routed_to to the worker target."""
        return any("update" in c and f"gc.routed_to={WORKER_TARGET}" in c for c in self._bd_calls())

    def test_refuses_to_route_when_flag_true(self) -> None:
        """requires_human_decision=true → no re-route, exit 0, refusal logged."""
        _make_bd_fake(self._tmp, {"requires_human_decision": "true"})
        result = _run_kickoff(self._rig, self._tmp)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertFalse(
            self._routed(),
            msg=f"kickoff re-routed a flagged bead; bd calls: {self._bd_calls()}",
        )
        self.assertIn("requires_human_decision", (result.stdout + result.stderr))

    def test_routes_normally_when_flag_absent(self) -> None:
        """No flag → normal routing to the worker pool."""
        _make_bd_fake(self._tmp, {})
        result = _run_kickoff(self._rig, self._tmp)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(
            self._routed(),
            msg=f"kickoff did not route an unflagged bead; bd calls: {self._bd_calls()}",
        )

    def test_routes_when_flag_resolved(self) -> None:
        """A resolved decision (non-'true' value) does not block re-kickoff."""
        _make_bd_fake(self._tmp, {"requires_human_decision": "resolved"})
        result = _run_kickoff(self._rig, self._tmp)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(
            self._routed(),
            msg=f"kickoff blocked a resolved bead; bd calls: {self._bd_calls()}",
        )


if __name__ == "__main__":
    unittest.main()
