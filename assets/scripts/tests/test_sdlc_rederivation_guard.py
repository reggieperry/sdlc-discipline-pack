"""Tests for sdlc-rederivation-guard.py (pack #197 Part 2).

Part 1 (the sdlc-kickoff guard, v2.37.0) stops a *kickoff* from re-arming a
bead already parked with requires_human_decision. Part 2 closes the other
re-arm path: the tester's bounce-to-worker. When a phase is about to bounce
a bead whose gate.blocks are *identical* to the previous cycle's — the
worker resumed, could not change the gate result, and the gate re-derived
the same failure — that is a confirmed dead-end. The guard parks the bead
(requires_human_decision=true, status=blocked, routing cleared, witness
mailed) instead of bouncing it back into the loop. The Part 1 kickoff guard
then refuses to re-arm it.

The guard decides from two inputs: the freshly-computed blocks (env
GATE_BLOCKS) and the prior gate.blocks already on the bead (bd show). It
compares them structurally (parsed JSON, so whitespace/key-order noise does
not read as "changed") and never parks on an empty/trivial block set.

Test scaffold: inline bd fake (answers `bd show <id> --json` with a
configurable prior gate.blocks; logs every `bd update` argv) and a gc fake
(logs `gc mail send` argv). stdlib-only; matches pack convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_sdlc_rederivation_guard -v
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

GUARD = (
    Path(__file__).resolve().parents[3]
    / "overlay"
    / "per-provider"
    / "claude"
    / ".claude"
    / "sdlc-discipline"
    / "sdlc-rederivation-guard.py"
)
assert GUARD.exists(), f"sdlc-rederivation-guard.py not found at {GUARD}"

BEAD = "el-test197"
WORKER_TARGET = "rig/sdlc-discipline.worker"
WITNESS_TARGET = "rig/witness"

# Two distinct, structurally-valid gate.blocks payloads.
BLOCKS_A = json.dumps([{"check": "D.asserts", "file": "tests/unit/test_x.py", "detail": "lost 9"}])
BLOCKS_B = json.dumps([{"check": "B.suppress", "file": "core/y.py", "detail": "added # noqa"}])


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_bd_fake(tmp: Path, prior_blocks: str | None) -> None:
    meta = {"gc.routed_to": WORKER_TARGET}
    if prior_blocks is not None:
        meta["gate.blocks"] = prior_blocks
    show_json = json.dumps([{"id": BEAD, "status": "open", "metadata": meta}])
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


def _make_gc_fake(tmp: Path) -> None:
    gc = tmp / "gc"
    body = f'#!/bin/bash\necho "$@" >> "{tmp}/gc-argv.log"\nexit 0\n'
    _write_exec(gc, body)


def _run_guard(tmp: Path, current_blocks: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{tmp}:{os.environ.get('PATH', '')}",
        "GATE_BLOCKS": current_blocks,
    }
    return subprocess.run(
        ["python3", str(GUARD), BEAD, WORKER_TARGET, WITNESS_TARGET],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


class RederivationGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = TemporaryDirectory()
        self._tmp = Path(self._ctx.name)
        _make_gc_fake(self._tmp)

    def tearDown(self) -> None:
        self._ctx.cleanup()

    def _bd_calls(self) -> list[str]:
        log = self._tmp / "bd-argv.log"
        return log.read_text().splitlines() if log.exists() else []

    def _gc_calls(self) -> list[str]:
        log = self._tmp / "gc-argv.log"
        return log.read_text().splitlines() if log.exists() else []

    def _parked(self) -> bool:
        return any("update" in c and "requires_human_decision=true" in c for c in self._bd_calls())

    def _routed_to_worker(self) -> bool:
        return any("update" in c and f"gc.routed_to={WORKER_TARGET}" in c for c in self._bd_calls())

    def _mailed_witness(self) -> bool:
        return any("mail" in c and WITNESS_TARGET in c for c in self._gc_calls())

    def test_identical_blocks_parks(self) -> None:
        """Prior == current (non-empty) → park, do not bounce, mail witness."""
        _make_bd_fake(self._tmp, prior_blocks=BLOCKS_A)
        result = _run_guard(self._tmp, BLOCKS_A)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(self._parked(), msg=f"expected park; bd calls: {self._bd_calls()}")
        self.assertFalse(
            self._routed_to_worker(), msg="must NOT bounce to worker on identical re-derivation"
        )
        self.assertTrue(self._mailed_witness(), msg="park must mail the witness")

    def test_changed_blocks_bounces(self) -> None:
        """Prior != current → normal bounce to worker, no park."""
        _make_bd_fake(self._tmp, prior_blocks=BLOCKS_A)
        result = _run_guard(self._tmp, BLOCKS_B)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(
            self._routed_to_worker(), msg=f"expected bounce; bd calls: {self._bd_calls()}"
        )
        self.assertFalse(self._parked(), msg="changed blocks must not park")

    def test_no_prior_bounces(self) -> None:
        """First derivation (no prior gate.blocks) → bounce, never park."""
        _make_bd_fake(self._tmp, prior_blocks=None)
        result = _run_guard(self._tmp, BLOCKS_A)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(self._routed_to_worker(), msg="first derivation must bounce")
        self.assertFalse(self._parked(), msg="no prior → cannot be a re-derivation")

    def test_empty_blocks_never_parks(self) -> None:
        """An empty/trivial block set ([]), even if 'unchanged', is never a
        dead-end to park on — it means the gate found nothing to block."""
        _make_bd_fake(self._tmp, prior_blocks="[]")
        result = _run_guard(self._tmp, "[]")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertFalse(self._parked(), msg="empty blocks must never park")

    def test_whitespace_noise_still_parks(self) -> None:
        """Structural equality: the same blocks with cosmetic JSON whitespace
        differences are still 'identical' and must park (not falsely bounce)."""
        _make_bd_fake(self._tmp, prior_blocks=BLOCKS_A)
        noisy = BLOCKS_A.replace(", ", ",  ").replace(": ", ":  ")
        result = _run_guard(self._tmp, noisy)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(
            self._parked(), msg="cosmetic whitespace must not read as a changed block set"
        )


if __name__ == "__main__":
    unittest.main()
