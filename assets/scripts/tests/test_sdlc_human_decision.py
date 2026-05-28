"""Tests for sdlc-human-decision.sh (pack #198).

The recorded exit lever for a bead parked under `requires_human_decision`
(#197). Once a phase escalates a decision the chain can't make, the bead
sits `blocked` with the flag set and the kickoff guard refuses to
re-route it. This helper is how a human un-parks it through a recorded
action rather than an ad-hoc `bd update`:

    sdlc-human-decision.sh resolve <bead> --action merge|rescope|waive [--reason "..."]

All actions clear the park (`requires_human_decision=resolved`) and record
the action + timestamp (+ optional reason) in bead metadata for the audit
trail. `rescope` additionally re-opens the bead (`--status=open`) so a
fresh kickoff can re-route it; `merge` and `waive` leave the status alone
(the operator performs the merge / accepts the waiver out of band).

Test scaffold: inline `bd` fake on PATH answering `bd show <id> --json`
and logging every `bd update` argv. stdlib-only. Matches pack convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_sdlc_human_decision -v
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT = Path(__file__).resolve().parent.parent / "sdlc-human-decision.sh"
assert SCRIPT.exists(), f"sdlc-human-decision.sh not found at {SCRIPT}"

BEAD = "el-test001"


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_bd_fake(tmp: Path, *, exists: bool = True) -> None:
    """bd fake: `show <id> --json` returns the bead (or empty when not
    exists); every argv is logged to bd-argv.log."""
    show_json = (
        json.dumps(
            [{"id": BEAD, "status": "blocked", "metadata": {"requires_human_decision": "true"}}]
        )
        if exists
        else ""
    )
    bd = tmp / "bd"
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/bd-argv.log"\n'
        'if [ "$1" = "show" ]; then\n'
        f"    cat <<'JSON'\n{show_json}\nJSON\n"
        f"    {'exit 0' if exists else 'exit 1'}\n"
        "fi\n"
        "exit 0\n"
    )
    _write_exec(bd, body)


def _run(tmp: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PATH": f"{tmp}:{os.environ.get('PATH', '')}"}
    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


class HumanDecisionResolve(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = TemporaryDirectory()
        self._tmp = Path(self._ctx.name)

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def _updates(self) -> str:
        log = self._tmp / "bd-argv.log"
        return log.read_text() if log.exists() else ""

    def test_merge_clears_park_and_records_without_reopening(self) -> None:
        _make_bd_fake(self._tmp)
        r = _run(self._tmp, "resolve", BEAD, "--action", "merge")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        updates = self._updates()
        self.assertIn("requires_human_decision=resolved", updates)
        self.assertIn("human_decision_action=merge", updates)
        self.assertIn("human_decision_at=", updates)
        self.assertNotIn("--status=open", updates)

    def test_rescope_reopens_for_re_kickoff(self) -> None:
        _make_bd_fake(self._tmp)
        r = _run(self._tmp, "resolve", BEAD, "--action", "rescope")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        updates = self._updates()
        self.assertIn("requires_human_decision=resolved", updates)
        self.assertIn("human_decision_action=rescope", updates)
        self.assertIn("--status=open", updates)

    def test_waive_records_reason(self) -> None:
        _make_bd_fake(self._tmp)
        r = _run(
            self._tmp, "resolve", BEAD, "--action", "waive", "--reason", "accepted-gate-exception"
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        updates = self._updates()
        self.assertIn("human_decision_action=waive", updates)
        self.assertIn("human_decision_reason=accepted-gate-exception", updates)

    def test_invalid_action_errors(self) -> None:
        _make_bd_fake(self._tmp)
        r = _run(self._tmp, "resolve", BEAD, "--action", "frobnicate")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("requires_human_decision=resolved", self._updates())

    def test_missing_bead_errors(self) -> None:
        _make_bd_fake(self._tmp, exists=False)
        r = _run(self._tmp, "resolve", BEAD, "--action", "merge")
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
