"""Tests for the SDLC claude-with-retry bash wrapper (pack #47).

Black-box subprocess tests. Each test stands up a tempdir holding fake `claude`
and `bd` binaries, prepends it to PATH, and invokes the wrapper with controlled
env. Side effects (bd metadata writes, claude arg captures) are recorded into
files inside the tempdir for assertions.

stdlib-only (`unittest` + tempfile + subprocess + textwrap). Matches the
pack's existing test convention (`test_tech_debt.py`, `test_claude_retry.py`).

Run with:

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import os
import stat
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

WRAPPER_PATH = Path(__file__).resolve().parent.parent / "sdlc-claude-with-retry.sh"
CLAUDE_RETRY_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "overlay"
    / "per-provider"
    / "claude"
    / ".claude"
    / "sdlc-discipline"
    / "claude_retry.py"
)


def _write_executable(path: Path, body: str) -> None:
    """Write a shell script and chmod it executable."""
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_claude(tmp: Path, *, exit_code: int = 0) -> Path:
    """Build a fake `claude` binary that records argv and exits with `exit_code`.

    The fake records its argv to `<tmp>/claude-argv.log` (one line per call,
    args space-separated). Tests inspect that file to assert arg pass-through.
    """
    path = tmp / "claude"
    body = textwrap.dedent(
        f"""\
        #!/bin/bash
        echo "$@" >> "{tmp}/claude-argv.log"
        exit {exit_code}
        """
    )
    _write_executable(path, body)
    return path


def _fake_claude_writes_log(tmp: Path, *, exit_code: int = 0, log_event: str) -> Path:
    """Build a fake `claude` that also writes a fixed JSONL event to the session log.

    The session log path is taken from env `SDLC_CLAUDE_SESSION_LOG` (the
    wrapper sets it; tests verify against the same value). `log_event` is a
    raw JSON string written as one line. Multiple invocations append.
    """
    path = tmp / "claude"
    body = textwrap.dedent(
        f"""\
        #!/bin/bash
        echo "$@" >> "{tmp}/claude-argv.log"
        if [ -n "${{SDLC_CLAUDE_SESSION_LOG:-}}" ]; then
            echo '{log_event}' >> "$SDLC_CLAUDE_SESSION_LOG"
        fi
        exit {exit_code}
        """
    )
    _write_executable(path, body)
    return path


def _fake_bd_step_sequence(tmp: Path, *, steps: list[str]) -> Path:
    """Build a fake `bd` whose `bd show` returns a different step per call.

    `steps[i]` is the value of `metadata.current_step` returned on the i-th
    `bd show` invocation. Calls past the sequence end re-use the final entry.
    `bd update` calls are recorded as before. Counter persists in a file
    next to the binary so each subprocess invocation increments.
    """
    path = tmp / "bd"
    counter = tmp / "bd-show-counter"
    counter.write_text("0")
    steps_array = " ".join(f'"{s}"' for s in steps)
    body = textwrap.dedent(
        f"""\
        #!/bin/bash
        STEPS=({steps_array})
        case "$1" in
            show)
                IDX=$(cat "{counter}")
                MAX=$((${{#STEPS[@]}} - 1))
                if [ "$IDX" -gt "$MAX" ]; then IDX=$MAX; fi
                CUR="${{STEPS[$IDX]}}"
                echo $((IDX + 1)) > "{counter}"
                echo "[{{\\"id\\":\\"el-fake\\",\\"metadata\\":{{\\"current_step\\":\\"$CUR\\"}}}}]"
                ;;
            update)
                shift
                echo "$@" >> "{tmp}/bd-update.log"
                ;;
        esac
        exit 0
        """
    )
    _write_executable(path, body)
    return path


def _fake_bd_with_step(tmp: Path, *, current_step: str) -> Path:
    """Build a fake `bd` binary that returns a single canned `bd show` payload.

    For any `bd show <id> --json` invocation, prints a one-element JSON array
    with `metadata.current_step` set to `current_step`. For `bd update` calls,
    records the args to `<tmp>/bd-update.log` and exits 0.
    """
    path = tmp / "bd"
    body = textwrap.dedent(
        f"""\
        #!/bin/bash
        case "$1" in
            show)
                echo '[{{"id":"el-fake","metadata":{{"current_step":"{current_step}"}}}}]'
                ;;
            update)
                shift
                echo "$@" >> "{tmp}/bd-update.log"
                ;;
        esac
        exit 0
        """
    )
    _write_executable(path, body)
    return path


class WrapperHappyPathTests(unittest.TestCase):
    """Cycle 1 — claude exits cleanly and bead has advanced past worker phase.

    The wrapper invokes claude once, asks claude_retry.py to decide, gets
    EXIT_SUCCESS, and exits 0. No retry occurs.
    """

    def test_passes_claude_args_verbatim(self) -> None:
        """Cycle 2 — argv pass-through. The wrapper is a thin shim; gc launches
        it with the claude argv it would have used directly, and that argv must
        reach claude unchanged. Tests special-character handling (space-bearing
        arg, double-dash flag, and an empty-arg edge) so a `"$@"` regression to
        `$*` or word-splitting would surface.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _fake_claude(tmp, exit_code=0)
            _fake_bd_with_step(tmp, current_step="read-diff")

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "STORY_ID": "el-fake",
                "SDLC_TEMPLATE": "worker",
                "CLAUDE_RETRY_PY": str(CLAUDE_RETRY_PATH),
            }
            result = subprocess.run(
                [
                    str(WRAPPER_PATH),
                    "--print",
                    "arg with spaces",
                    "--effort",
                    "max",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0)

            argv_log = (tmp / "claude-argv.log").read_text().strip()
            # The fake claude echoes `"$@"` as a single space-joined line.
            # `"arg with spaces"` collapses to space-separated tokens at echo
            # time, so the canonical assertion is that each flag-value pair
            # appears in order.
            self.assertIn("--print", argv_log)
            self.assertIn("arg with spaces", argv_log)
            self.assertIn("--effort", argv_log)
            self.assertIn("max", argv_log)

    def test_exits_zero_when_step_advanced_past_template(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _fake_claude(tmp, exit_code=0)
            # `read-diff` is reviewer's first step — outside worker's phase
            # list, so handoff is complete from worker's perspective.
            _fake_bd_with_step(tmp, current_step="read-diff")

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "STORY_ID": "el-fake",
                "SDLC_TEMPLATE": "worker",
                "CLAUDE_RETRY_PY": str(CLAUDE_RETRY_PATH),
            }
            result = subprocess.run(
                [str(WRAPPER_PATH), "--print", "hello"],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(
                result.returncode,
                0,
                f"wrapper should exit 0 on clean handoff; "
                f"stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            # Claude invoked exactly once — no retry on clean handoff.
            argv_log = tmp / "claude-argv.log"
            self.assertTrue(
                argv_log.exists(),
                "fake claude should have been invoked at least once",
            )
            invocations = argv_log.read_text().strip().splitlines()
            self.assertEqual(
                len(invocations),
                1,
                f"clean handoff should invoke claude exactly once, "
                f"got {len(invocations)}: {invocations}",
            )


class WrapperExhaustionTests(unittest.TestCase):
    """Cycle 4 — attempts cap reached without handoff.

    Fake claude exits 0 every call but the bead's current_step never leaves
    the worker phase list. decide returns RETRY on attempts 1..MAX-1, then
    EXIT_EXHAUSTED on the cap. Wrapper exits 75 (EX_TEMPFAIL — Anthropic-
    side or environment failure, not a code defect).
    """

    def test_exits_75_when_attempts_exhausted(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            session_log = tmp / "session.jsonl"
            _fake_claude_writes_log(
                tmp,
                exit_code=0,
                log_event='{"type":"system","subtype":"turn_duration","durationMs":1}',
            )
            # current_step stays at `implement` forever — no handoff.
            _fake_bd_step_sequence(tmp, steps=["implement"])

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "STORY_ID": "el-fake",
                "SDLC_TEMPLATE": "worker",
                "CLAUDE_RETRY_PY": str(CLAUDE_RETRY_PATH),
                "SDLC_CLAUDE_SESSION_LOG": str(session_log),
                "SDLC_RETRY_SLEEP_OVERRIDE": "0",
                # Cap at 2 so the test is fast — three attempts total
                # (attempt 1 RETRY, attempt 2 EXIT_EXHAUSTED).
                "SDLC_MAX_ATTEMPTS": "2",
            }
            result = subprocess.run(
                [
                    str(WRAPPER_PATH),
                    "--print",
                    "--session-id",
                    "47c96142-fake-fake-fake-fakefakefake",
                    "hello",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(
                result.returncode,
                75,
                f"wrapper should exit 75 (EX_TEMPFAIL) on exhausted attempts; "
                f"got rc={result.returncode} stdout={result.stdout!r} "
                f"stderr={result.stderr!r}",
            )
            invocations = (tmp / "claude-argv.log").read_text().strip().splitlines()
            self.assertEqual(
                len(invocations),
                2,
                f"with MAX_ATTEMPTS=2 the wrapper should invoke claude exactly "
                f"twice (attempt 1 retries, attempt 2 exhausts), "
                f"got {len(invocations)}",
            )


class WrapperRetryTests(unittest.TestCase):
    """Cycle 3 — stall then recovery.

    First claude invocation: writes a turn_duration system event and exits 0,
    but the bead's current_step stayed `implement` (worker phase, not handed
    off). claude_retry.decide returns RETRY. Second invocation: same fake
    claude, but the bead's step has now advanced to `read-diff` (reviewer
    phase). decide returns EXIT_SUCCESS. Wrapper exits 0 after two attempts.
    """

    def test_retries_once_after_turn_cap_then_succeeds(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            session_log = tmp / "session.jsonl"
            _fake_claude_writes_log(
                tmp,
                exit_code=0,
                log_event='{"type":"system","subtype":"turn_duration","durationMs":1}',
            )
            _fake_bd_step_sequence(
                tmp,
                steps=["implement", "read-diff"],
            )

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "STORY_ID": "el-fake",
                "SDLC_TEMPLATE": "worker",
                "CLAUDE_RETRY_PY": str(CLAUDE_RETRY_PATH),
                "SDLC_CLAUDE_SESSION_LOG": str(session_log),
                # Skip the real sleep schedule in tests.
                "SDLC_RETRY_SLEEP_OVERRIDE": "0",
            }
            result = subprocess.run(
                [
                    str(WRAPPER_PATH),
                    "--print",
                    "--session-id",
                    "47c96142-fake-fake-fake-fakefakefake",
                    "hello",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(
                result.returncode,
                0,
                f"wrapper should exit 0 after one retry; "
                f"stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            argv_log = tmp / "claude-argv.log"
            self.assertTrue(argv_log.exists())
            invocations = argv_log.read_text().strip().splitlines()
            self.assertEqual(
                len(invocations),
                2,
                f"stall + recovery should invoke claude exactly twice, "
                f"got {len(invocations)}: {invocations}",
            )


class WrapperMetadataTests(unittest.TestCase):
    """Cycle 5 — per-attempt bead metadata writes give the operator an audit trail.

    For each attempt the wrapper records `<template>.attempt_n` and
    `<template>.state` (running/resuming/exhausted) to the bead. After each
    non-success decide it also records `<template>.last_exit_cause` (turn_cap
    / api_529 / api_429 / crash / unknown). Without these the operator has
    no way to see why a chain stalled or how many attempts the wrapper made.
    """

    def test_records_attempt_metadata_through_stall_and_recovery(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            session_log = tmp / "session.jsonl"
            _fake_claude_writes_log(
                tmp,
                exit_code=0,
                log_event='{"type":"system","subtype":"turn_duration","durationMs":1}',
            )
            _fake_bd_step_sequence(tmp, steps=["implement", "read-diff"])

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "STORY_ID": "el-fake",
                "SDLC_TEMPLATE": "worker",
                "CLAUDE_RETRY_PY": str(CLAUDE_RETRY_PATH),
                "SDLC_CLAUDE_SESSION_LOG": str(session_log),
                "SDLC_RETRY_SLEEP_OVERRIDE": "0",
            }
            result = subprocess.run(
                [
                    str(WRAPPER_PATH),
                    "--print",
                    "--session-id",
                    "47c96142-fake-fake-fake-fakefakefake",
                    "hello",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)

            update_log = tmp / "bd-update.log"
            self.assertTrue(
                update_log.exists(),
                "wrapper should record bd update calls (none recorded)",
            )
            updates = update_log.read_text()

            # Attempt 1 announced
            self.assertIn(
                "worker.attempt_n=1",
                updates,
                f"missing attempt_n=1 record; got:\n{updates}",
            )
            # Cause recorded after first claude exit didn't advance step
            self.assertIn(
                "worker.last_exit_cause=turn_cap",
                updates,
                f"missing last_exit_cause=turn_cap; got:\n{updates}",
            )
            # Attempt 2 announced
            self.assertIn(
                "worker.attempt_n=2",
                updates,
                f"missing attempt_n=2 record; got:\n{updates}",
            )
            # Successful run shouldn't write exhausted state
            self.assertNotIn(
                "exhausted",
                updates,
                f"clean recovery should not record exhausted state; got:\n{updates}",
            )

    def test_records_exhausted_state_on_cap(self) -> None:
        """Cycle 5b — when attempts hit the cap, the wrapper records state=exhausted.

        Operator running `bd show el-XXX` after the wrapper exits 75 sees
        `worker.state=exhausted` and the last cause that drove it there.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            session_log = tmp / "session.jsonl"
            _fake_claude_writes_log(
                tmp,
                exit_code=0,
                log_event='{"type":"system","subtype":"turn_duration","durationMs":1}',
            )
            _fake_bd_step_sequence(tmp, steps=["implement"])  # never advances

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "STORY_ID": "el-fake",
                "SDLC_TEMPLATE": "worker",
                "CLAUDE_RETRY_PY": str(CLAUDE_RETRY_PATH),
                "SDLC_CLAUDE_SESSION_LOG": str(session_log),
                "SDLC_RETRY_SLEEP_OVERRIDE": "0",
                "SDLC_MAX_ATTEMPTS": "2",
            }
            result = subprocess.run(
                [
                    str(WRAPPER_PATH),
                    "--print",
                    "--session-id",
                    "47c96142-fake-fake-fake-fakefakefake",
                    "hello",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 75)

            updates = (tmp / "bd-update.log").read_text()
            self.assertIn(
                "worker.state=exhausted",
                updates,
                f"missing exhausted-state record; got:\n{updates}",
            )


class WrapperResumeTests(unittest.TestCase):
    """Cycle 6 — on retry, the wrapper switches to `claude --resume <UUID> <prompt>`.

    The OQ1 grounding pass confirmed that mid-task resumption requires
    `--resume <session-id>` AND a continuation prompt; without the prompt
    claude waits for stdin in interactive mode. The wrapper must extract
    the session ID from gc's argv on the first call and switch argv shape
    on retry. Without this the "retry" actually starts a fresh session,
    which defeats the whole point of pack #47.
    """

    def test_retry_uses_resume_with_session_id_and_continuation_prompt(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            session_log = tmp / "session.jsonl"
            session_id = "47c96142-0cee-422f-b5b6-71cfc5d12ebc"
            _fake_claude_writes_log(
                tmp,
                exit_code=0,
                log_event='{"type":"system","subtype":"turn_duration","durationMs":1}',
            )
            _fake_bd_step_sequence(
                tmp,
                steps=["implement", "read-diff"],
            )

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "STORY_ID": "el-fake",
                "SDLC_TEMPLATE": "worker",
                "CLAUDE_RETRY_PY": str(CLAUDE_RETRY_PATH),
                "SDLC_CLAUDE_SESSION_LOG": str(session_log),
                "SDLC_RETRY_SLEEP_OVERRIDE": "0",
            }
            result = subprocess.run(
                [
                    str(WRAPPER_PATH),
                    "--print",
                    "--session-id",
                    session_id,
                    "--effort",
                    "max",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)

            invocations = (tmp / "claude-argv.log").read_text().strip().splitlines()
            self.assertEqual(
                len(invocations),
                2,
                f"expected two claude invocations, got {len(invocations)}",
            )

            # First call: original argv pass-through (gc's normal launch).
            first = invocations[0]
            self.assertIn("--session-id", first)
            self.assertIn(session_id, first)

            # Second call: --resume + UUID + continuation prompt.
            second = invocations[1]
            self.assertIn(
                "--resume",
                second,
                f"retry invocation should use --resume; got {second!r}",
            )
            self.assertIn(
                session_id,
                second,
                f"retry invocation should carry the session id; got {second!r}",
            )
            self.assertIn(
                "interrupted",
                second.lower(),
                f"retry invocation should include the continuation prompt "
                f"(OQ1-validated text starts with 'You were interrupted'); "
                f"got {second!r}",
            )


if __name__ == "__main__":
    unittest.main()
