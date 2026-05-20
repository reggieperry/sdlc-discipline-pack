"""Tests for sdlc-drain-ack-recover.sh (pack v2.19).

Black-box subprocess tests. Each test stands up a tempdir holding fake
`bd`, `gc`, `git`, and `sdlc-stall-recover.sh` binaries that record their
argv to log files. The script under test is invoked with controlled env;
assertions read the recorded argv to verify the call sequence.

Why fakes instead of mocks: the script's whole job is to compose peer
binaries in the right order with the right arguments. Mocking that out
would test the mock, not the composition. Real subprocess with recording
fakes verifies the call shape the way it ships.

stdlib-only (`unittest` + tempfile + subprocess + textwrap). Matches the
pack convention (`test_sdlc_notify.py`, `test_sdlc_stall_recover.py`).

Run with:

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-drain-ack-recover.sh"
assert SCRIPT_PATH.exists(), f"sdlc-drain-ack-recover.sh not found at {SCRIPT_PATH}"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_gc(
    tmp: Path,
    *,
    bead_json: str = "",
    show_exit: int = 0,
    kill_exit: int = 0,
    reload_exit: int = 0,
    update_exit: int = 0,
) -> Path:
    """Build a fake `gc` binary that dispatches on subcommand and records argv.

    Recorded:
    - argv → <tmp>/gc-argv.log (one line per invocation, space-separated)

    Dispatch:
    - `gc bd --rig <rig> show <bead-id> --json` → echoes bead_json, exits show_exit
    - `gc bd --rig <rig> update <bead-id> ...` → exits update_exit
    - `gc session kill <session-id>` → exits kill_exit
    - `gc supervisor reload` → exits reload_exit
    - everything else → exits 0 silently

    The fake covers every `gc` subcommand the subscriber invokes. Tests pass
    a non-zero exit for one subcommand at a time to verify fail-closed
    semantics without disturbing the other steps.
    """
    path = tmp / "gc"
    body = textwrap.dedent(
        f"""\
        #!/bin/bash
        echo "$@" >> "{tmp}/gc-argv.log"
        echo "gc $@" >> "{tmp}/call-sequence.log"
        if [ "$1" = "bd" ]; then
            shift
            # consume --rig <rig> if present
            if [ "${{1:-}}" = "--rig" ]; then shift 2; fi
            sub="${{1:-}}"
            if [ "$sub" = "show" ]; then
                cat <<'__BEAD_EOF__'
{bead_json}
__BEAD_EOF__
                exit {show_exit}
            elif [ "$sub" = "update" ]; then
                exit {update_exit}
            fi
            exit 0
        elif [ "$1" = "session" ] && [ "${{2:-}}" = "kill" ]; then
            exit {kill_exit}
        elif [ "$1" = "supervisor" ] && [ "${{2:-}}" = "reload" ]; then
            exit {reload_exit}
        fi
        exit 0
        """
    )
    _write_executable(path, body)
    return path


def _fake_recorder(tmp: Path, name: str, *, exit_code: int = 0) -> Path:
    """Build a fake binary that records argv to <tmp>/<name>-argv.log and exits.

    Also appends `<name> <argv>` to <tmp>/call-sequence.log so tests can
    verify ordering across peers in a single read.

    Used for peer binaries the subscriber calls but doesn't need a parameterized
    response from (git, sdlc-stall-recover.sh, sdlc-notify.sh).
    """
    path = tmp / name
    body = textwrap.dedent(
        f"""\
        #!/bin/bash
        echo "$@" >> "{tmp}/{name}-argv.log"
        echo "{name} $@" >> "{tmp}/call-sequence.log"
        cat >> "{tmp}/{name}-stdin.log" 2>/dev/null || true
        exit {exit_code}
        """
    )
    _write_executable(path, body)
    return path


def _make_bead_json(*, work_dir: str, branch: str) -> str:
    """Shape that bd / gc-bd return for `show <bead-id> --json` — a 1-element array."""
    return json.dumps(
        [
            {
                "id": "el-xyz",
                "status": "in_progress",
                "metadata": {
                    "work_dir": work_dir,
                    "branch": branch,
                    "rig": "elder",
                },
            }
        ]
    )


def _payload(
    *,
    session_id: str = "s-abc",
    bead_id: str = "el-xyz",
    template: str = "implementor",
    bead_status: str = "in_progress",
    reason: str = "drain-ack with stranded bead",
) -> str:
    return json.dumps(
        {
            "session_id": session_id,
            "bead_id": bead_id,
            "template": template,
            "bead_status": bead_status,
            "reason": reason,
        }
    )


class FeatureGateTests(unittest.TestCase):
    """Cycle 1 — the script ships disabled.

    SDLC_DRAIN_ACK_RECOVERY_ENABLED defaults to false. Without the operator
    explicitly flipping it on, the script exits 0 without touching any peer
    binary or writing any state. This is the demo-with-prod-risk discipline:
    rsyncing the pack to T7920 must not by itself start running recovery.
    """

    def test_exits_zero_when_enabled_env_unset(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            state_dir = tmp / "state"

            env = {
                **os.environ,
                "GC_EVENT_TYPE": "session.drain_acked_with_assigned_work",
                "GC_EVENT_PAYLOAD": _payload(),
                "SDLC_DRAIN_ACK_STATE_DIR": str(state_dir),
            }
            env.pop("SDLC_DRAIN_ACK_RECOVERY_ENABLED", None)

            result = subprocess.run(
                [str(SCRIPT_PATH)],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(
                result.returncode,
                0,
                f"disabled feature gate should exit 0; "
                f"stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            self.assertFalse(
                state_dir.exists(),
                f"disabled feature gate should not create state dir; found {state_dir}",
            )

    def test_exits_zero_when_enabled_env_is_false(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            state_dir = tmp / "state"

            env = {
                **os.environ,
                "SDLC_DRAIN_ACK_RECOVERY_ENABLED": "false",
                "GC_EVENT_TYPE": "session.drain_acked_with_assigned_work",
                "GC_EVENT_PAYLOAD": _payload(),
                "SDLC_DRAIN_ACK_STATE_DIR": str(state_dir),
            }

            result = subprocess.run(
                [str(SCRIPT_PATH)],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(
                result.returncode,
                0,
                f"feature gate set to 'false' should exit 0; "
                f"stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            self.assertFalse(
                state_dir.exists(),
                "disabled feature gate should not create state dir",
            )


class InputValidationTests(unittest.TestCase):
    """Cycle 2 — degenerate inputs are silent no-ops.

    The order trigger fires on every emission. Some emissions may arrive
    without a parseable payload (deferred-minor noted in the gascity#2380
    PR around Subject asymmetry, or a future emission site that omits a
    field). The script must not crash, alert, or create state — exit 0
    quietly so the order log stays clean.
    """

    def _run_with_payload(self, payload: str, state_dir: Path) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "SDLC_DRAIN_ACK_RECOVERY_ENABLED": "true",
            "GC_EVENT_TYPE": "session.drain_acked_with_assigned_work",
            "GC_EVENT_PAYLOAD": payload,
            "SDLC_DRAIN_ACK_STATE_DIR": str(state_dir),
        }
        return subprocess.run(
            [str(SCRIPT_PATH)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_empty_payload_is_silent_no_op(self) -> None:
        with TemporaryDirectory() as tmp_str:
            state_dir = Path(tmp_str) / "state"
            result = self._run_with_payload("", state_dir)
            self.assertEqual(
                result.returncode,
                0,
                f"empty payload should exit 0; stderr={result.stderr!r}",
            )
            self.assertFalse(
                state_dir.exists(),
                "empty payload should not create state dir",
            )

    def test_missing_session_id_is_silent_no_op(self) -> None:
        with TemporaryDirectory() as tmp_str:
            state_dir = Path(tmp_str) / "state"
            payload = json.dumps({"bead_id": "el-xyz"})
            result = self._run_with_payload(payload, state_dir)
            self.assertEqual(
                result.returncode,
                0,
                f"missing session_id should exit 0; stderr={result.stderr!r}",
            )

    def test_missing_bead_id_is_silent_no_op(self) -> None:
        with TemporaryDirectory() as tmp_str:
            state_dir = Path(tmp_str) / "state"
            payload = json.dumps({"session_id": "s-abc"})
            result = self._run_with_payload(payload, state_dir)
            self.assertEqual(
                result.returncode,
                0,
                f"missing bead_id should exit 0; stderr={result.stderr!r}",
            )

    def test_malformed_json_payload_is_silent_no_op(self) -> None:
        with TemporaryDirectory() as tmp_str:
            state_dir = Path(tmp_str) / "state"
            result = self._run_with_payload("{not-json", state_dir)
            self.assertEqual(
                result.returncode,
                0,
                f"malformed JSON should exit 0; stderr={result.stderr!r}",
            )


class BeadLookupTests(unittest.TestCase):
    """Cycle 3 — the subscriber resolves the stranded bead through gc.

    The event payload carries `bead_id` but not `work_dir` or `branch` —
    those live on the bead's metadata. The subscriber routes through
    `gc bd --rig <rig> show <bead-id> --json` (same routing convention as
    sdlc-cost-rollup.sh) and extracts the metadata it needs for the
    subsequent commit + push steps.

    Verifies the call shape, not the downstream effect — downstream effects
    are pinned in cycle 5's happy-path test.
    """

    def test_calls_gc_bd_show_with_rig_and_bead_id(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            state_dir = tmp / "state"
            work_dir = tmp / "rig" / ".gc" / "worktrees" / "ws-1"
            work_dir.mkdir(parents=True)
            bead_json = _make_bead_json(work_dir=str(work_dir), branch="feat/el-xyz")
            _fake_gc(tmp, bead_json=bead_json)
            # Also need fakes for downstream peers so the script doesn't
            # die at a missing binary before we can inspect gc's argv log.
            _fake_recorder(tmp, "sdlc-stall-recover.sh")
            _fake_recorder(tmp, "git")
            _fake_recorder(tmp, "sdlc-notify.sh")

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "SDLC_DRAIN_ACK_RECOVERY_ENABLED": "true",
                "GC_EVENT_TYPE": "session.drain_acked_with_assigned_work",
                "GC_EVENT_PAYLOAD": _payload(bead_id="el-xyz"),
                "GC_RIG": "elder",
                "SDLC_DRAIN_ACK_STATE_DIR": str(state_dir),
            }

            result = subprocess.run(
                [str(SCRIPT_PATH)],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            gc_log_path = tmp / "gc-argv.log"
            self.assertTrue(
                gc_log_path.exists(),
                f"gc should have been invoked; stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            gc_log = gc_log_path.read_text()
            self.assertIn(
                "bd --rig elder show el-xyz",
                gc_log,
                f"gc should be called as `bd --rig elder show el-xyz ...`; "
                f"got argv log: {gc_log!r}",
            )

    def test_bd_lookup_failure_alerts_and_exits_nonzero(self) -> None:
        """When `gc bd show` errors, the subscriber alerts and fails closed.

        The event was real — there's a stranded bead — so silently giving up
        would lose a recovery. Operator notification + non-zero exit code
        ensures the failure is visible.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            state_dir = tmp / "state"
            _fake_gc(tmp, bead_json="", show_exit=1)
            notify = _fake_recorder(tmp, "sdlc-notify.sh")
            _fake_recorder(tmp, "sdlc-stall-recover.sh")
            _fake_recorder(tmp, "git")

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "SDLC_DRAIN_ACK_RECOVERY_ENABLED": "true",
                "GC_EVENT_PAYLOAD": _payload(),
                "GC_RIG": "elder",
                "SDLC_DRAIN_ACK_STATE_DIR": str(state_dir),
            }

            result = subprocess.run(
                [str(SCRIPT_PATH)],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertNotEqual(
                result.returncode,
                0,
                f"bd lookup failure should exit nonzero; "
                f"stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            notify_log = tmp / "sdlc-notify.sh-argv.log"
            self.assertTrue(
                notify_log.exists(),
                f"bd lookup failure should send an alert via sdlc-notify.sh; "
                f"notify_log missing at {notify_log}",
            )


class HappyPathTests(unittest.TestCase):
    """Cycle 4 — the 5-step recipe runs in order on a clean event.

    With every peer cooperating, the subscriber drives:

      1. sdlc-stall-recover.sh --phase <template> --bead-id <bead>  (commit)
      2. git push -u origin <branch>
      3. gc bd --rig <rig> update <bead> --assignee "" --status open
      4. gc session kill <session>
      5. gc supervisor reload

    Verifies the call shape AND the order. Subsequent cycles pin behaviour
    when individual steps fail.
    """

    def test_runs_full_five_step_recipe_in_order(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            state_dir = tmp / "state"
            work_dir = tmp / "rig" / ".gc" / "worktrees" / "ws-1"
            work_dir.mkdir(parents=True)

            bead_json = _make_bead_json(work_dir=str(work_dir), branch="feat/el-xyz")
            _fake_gc(tmp, bead_json=bead_json)
            _fake_recorder(tmp, "sdlc-stall-recover.sh")
            _fake_recorder(tmp, "git")
            _fake_recorder(tmp, "sdlc-notify.sh")

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "SDLC_DRAIN_ACK_RECOVERY_ENABLED": "true",
                "GC_EVENT_TYPE": "session.drain_acked_with_assigned_work",
                "GC_EVENT_PAYLOAD": _payload(
                    session_id="s-abc",
                    bead_id="el-xyz",
                    template="implementor",
                ),
                "GC_RIG": "elder",
                "SDLC_DRAIN_ACK_STATE_DIR": str(state_dir),
            }

            result = subprocess.run(
                [str(SCRIPT_PATH)],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(
                result.returncode,
                0,
                f"happy path should exit 0; stdout={result.stdout!r} stderr={result.stderr!r}",
            )

            sequence = (tmp / "call-sequence.log").read_text().splitlines()
            joined = "\n".join(sequence)

            self.assertIn(
                "sdlc-stall-recover.sh --phase implementor --bead-id el-xyz",
                joined,
                f"step 1 (commit via sdlc-stall-recover.sh) missing; call sequence was:\n{joined}",
            )
            self.assertIn(
                "git push -u origin feat/el-xyz",
                joined,
                f"step 2 (git push) missing; call sequence was:\n{joined}",
            )
            self.assertIn(
                "gc bd --rig elder update el-xyz --assignee  --status open",
                joined,
                f"step 3 (bd assignee clear) missing; call sequence was:\n{joined}",
            )
            self.assertIn(
                "gc session kill s-abc",
                joined,
                f"step 4 (session kill) missing; call sequence was:\n{joined}",
            )
            self.assertIn(
                "gc supervisor reload",
                joined,
                f"step 5 (supervisor reload) missing; call sequence was:\n{joined}",
            )

            # Order check
            indices = {
                "commit": next(
                    (i for i, c in enumerate(sequence) if "sdlc-stall-recover.sh" in c),
                    -1,
                ),
                "push": next(
                    (i for i, c in enumerate(sequence) if c.startswith("git push")),
                    -1,
                ),
                "update": next(
                    (i for i, c in enumerate(sequence) if "bd --rig" in c and "update" in c),
                    -1,
                ),
                "kill": next(
                    (i for i, c in enumerate(sequence) if "session kill" in c),
                    -1,
                ),
                "reload": next(
                    (i for i, c in enumerate(sequence) if "supervisor reload" in c),
                    -1,
                ),
            }
            self.assertGreater(indices["push"], indices["commit"], "push must run after commit")
            self.assertGreater(indices["update"], indices["push"], "bd update must run after push")
            self.assertGreater(
                indices["kill"], indices["update"], "session kill must run after bd update"
            )
            self.assertGreater(
                indices["reload"], indices["kill"], "supervisor reload must run after session kill"
            )


class FailClosedTests(unittest.TestCase):
    """Cycle 5 — every step is fail-closed.

    When any one of the five steps errors, the script:
    - Halts immediately (no downstream steps run)
    - Sends an operator alert via sdlc-notify.sh
    - Exits with the step-specific code (3-7)

    Pinning each cascade independently. The operator's reference rule
    `claims_need_tests` requires this: the script's docstring promises
    fail-closed behaviour, so it gets a test per claim, not one omnibus test.
    """

    def _setup_scenario(
        self,
        tmp: Path,
        *,
        sr_exit: int = 0,
        git_exit: int = 0,
        update_exit: int = 0,
        kill_exit: int = 0,
        reload_exit: int = 0,
    ) -> dict[str, str]:
        state_dir = tmp / "state"
        work_dir = tmp / "rig" / ".gc" / "worktrees" / "ws-1"
        work_dir.mkdir(parents=True)

        bead_json = _make_bead_json(work_dir=str(work_dir), branch="feat/el-xyz")
        _fake_gc(
            tmp,
            bead_json=bead_json,
            update_exit=update_exit,
            kill_exit=kill_exit,
            reload_exit=reload_exit,
        )
        _fake_recorder(tmp, "sdlc-stall-recover.sh", exit_code=sr_exit)
        _fake_recorder(tmp, "git", exit_code=git_exit)
        _fake_recorder(tmp, "sdlc-notify.sh")

        return {
            **os.environ,
            "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
            "SDLC_DRAIN_ACK_RECOVERY_ENABLED": "true",
            "GC_EVENT_TYPE": "session.drain_acked_with_assigned_work",
            "GC_EVENT_PAYLOAD": _payload(
                session_id="s-abc", bead_id="el-xyz", template="implementor"
            ),
            "GC_RIG": "elder",
            "SDLC_DRAIN_ACK_STATE_DIR": str(state_dir),
        }

    def _run(self, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT_PATH)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_commit_failure_exits_3_alerts_and_skips_remaining_steps(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            env = self._setup_scenario(tmp, sr_exit=4)
            result = self._run(env)

            self.assertEqual(
                result.returncode,
                3,
                f"commit failure should exit 3; stderr={result.stderr!r}",
            )
            self.assertTrue(
                (tmp / "sdlc-notify.sh-argv.log").exists(),
                "commit failure should send an alert",
            )
            sequence = (tmp / "call-sequence.log").read_text()
            self.assertNotIn(
                "git push",
                sequence,
                f"push must not run after commit failure; got:\n{sequence}",
            )

    def test_push_failure_exits_4_alerts_and_skips_remaining_steps(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            env = self._setup_scenario(tmp, git_exit=1)
            result = self._run(env)

            self.assertEqual(
                result.returncode,
                4,
                f"push failure should exit 4; stderr={result.stderr!r}",
            )
            self.assertTrue(
                (tmp / "sdlc-notify.sh-argv.log").exists(),
                "push failure should send an alert",
            )
            sequence = (tmp / "call-sequence.log").read_text()
            self.assertNotIn(
                "bd --rig elder update",
                sequence,
                f"bd update must not run after push failure; got:\n{sequence}",
            )

    def test_bd_update_failure_exits_5_alerts_and_skips_remaining_steps(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            env = self._setup_scenario(tmp, update_exit=1)
            result = self._run(env)

            self.assertEqual(
                result.returncode,
                5,
                f"bd update failure should exit 5; stderr={result.stderr!r}",
            )
            self.assertTrue(
                (tmp / "sdlc-notify.sh-argv.log").exists(),
                "bd update failure should send an alert",
            )
            sequence = (tmp / "call-sequence.log").read_text()
            self.assertNotIn(
                "session kill",
                sequence,
                f"session kill must not run after bd update failure; got:\n{sequence}",
            )

    def test_session_kill_failure_exits_6_alerts_and_skips_reload(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            env = self._setup_scenario(tmp, kill_exit=1)
            result = self._run(env)

            self.assertEqual(
                result.returncode,
                6,
                f"session kill failure should exit 6; stderr={result.stderr!r}",
            )
            self.assertTrue(
                (tmp / "sdlc-notify.sh-argv.log").exists(),
                "session kill failure should send an alert",
            )
            sequence = (tmp / "call-sequence.log").read_text()
            self.assertNotIn(
                "supervisor reload",
                sequence,
                f"supervisor reload must not run after session kill failure; got:\n{sequence}",
            )

    def test_supervisor_reload_failure_exits_7_and_alerts(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            env = self._setup_scenario(tmp, reload_exit=1)
            result = self._run(env)

            self.assertEqual(
                result.returncode,
                7,
                f"supervisor reload failure should exit 7; stderr={result.stderr!r}",
            )
            self.assertTrue(
                (tmp / "sdlc-notify.sh-argv.log").exists(),
                "supervisor reload failure should send an alert",
            )


class CommitIdempotencyTests(unittest.TestCase):
    """Cycle 6 — repeated emissions converge on the same final state.

    sdlc-stall-recover.sh exits 3 when there is nothing to commit (worktree
    clean after exclusions). For a doubled drain-ack emission, the second
    run would find the worktree already committed and pushed by the first;
    exit 3 must NOT be treated as a step-1 failure. The reset-to-pristine
    invariant says: every step is safe to repeat; convergence holds.
    """

    def test_stall_recover_exit_3_is_not_a_failure(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            state_dir = tmp / "state"
            work_dir = tmp / "rig" / ".gc" / "worktrees" / "ws-1"
            work_dir.mkdir(parents=True)

            bead_json = _make_bead_json(work_dir=str(work_dir), branch="feat/el-xyz")
            _fake_gc(tmp, bead_json=bead_json)
            # exit 3 = "nothing to commit after exclusions" — idempotent
            _fake_recorder(tmp, "sdlc-stall-recover.sh", exit_code=3)
            _fake_recorder(tmp, "git")
            _fake_recorder(tmp, "sdlc-notify.sh")

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "SDLC_DRAIN_ACK_RECOVERY_ENABLED": "true",
                "GC_EVENT_PAYLOAD": _payload(
                    session_id="s-abc",
                    bead_id="el-xyz",
                    template="implementor",
                ),
                "GC_RIG": "elder",
                "SDLC_DRAIN_ACK_STATE_DIR": str(state_dir),
            }

            result = subprocess.run(
                [str(SCRIPT_PATH)],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(
                result.returncode,
                0,
                f"stall-recover exit 3 should not halt recovery; "
                f"stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            sequence = (tmp / "call-sequence.log").read_text()
            self.assertIn(
                "git push -u origin feat/el-xyz",
                sequence,
                f"push must still run when commit is no-op; got:\n{sequence}",
            )
            self.assertIn(
                "supervisor reload",
                sequence,
                "all 5 steps must run when commit is a no-op idempotent case",
            )
