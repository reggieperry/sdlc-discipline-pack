"""Tests for the claude-retry decision helper (pack #47).

Covers cause classification, retry-delay schedule, handoff detection,
bd integration via injected runner, the top-level `decide()` orchestrator,
and the CLI dispatch.

stdlib-only (`unittest` + tempfile + importlib). Mocks `bd` via a fake
runner passed into the public functions.

Run with:

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "overlay"
    / "per-provider"
    / "claude"
    / ".claude"
    / "sdlc-discipline"
    / "claude_retry.py"
)
assert MODULE_PATH.exists(), f"claude_retry.py not found at {MODULE_PATH}"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("claude_retry", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules before exec_module so Python 3.14's stricter
    # @dataclass introspection can resolve the module's __dict__ for field-
    # type lookups (PEP 604 `T | None` unions).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cr = _load_module()


def _fake_bd_factory(
    responses: list[subprocess.CompletedProcess[str]],
) -> Any:
    """Build a fake bd runner returning canned responses in order."""
    calls: list[list[str]] = []
    iterator = iter(responses)

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        try:
            return next(iterator)
        except StopIteration as exc:
            raise AssertionError(f"unexpected extra bd call: {args}") from exc

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def _bd_ok(metadata: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    """Build a successful bd-show response carrying `metadata`."""
    payload = json.dumps([{"id": "el-fake", "metadata": metadata}])
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=payload, stderr="")


def _bd_fail(rc: int = 1, stderr: str = "bead not found") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout="", stderr=stderr)


def _write_jsonl(tmp: Path, events: list[dict[str, Any]]) -> Path:
    """Write a session log fixture and return its path."""
    path = tmp / "session.jsonl"
    with path.open("w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")
    return path


# ---------------------------------------------------------------------------
# classify_exit — the five trigger-detection cases from the acceptance crit
# ---------------------------------------------------------------------------


class ClassifyExitTests(unittest.TestCase):
    def test_turn_cap_via_system_event(self) -> None:
        """Mode B: final system event subtype=turn_duration → TURN_CAP."""
        with TemporaryDirectory() as tmp:
            path = _write_jsonl(
                Path(tmp),
                [
                    {"type": "user", "content": "start"},
                    {"type": "assistant", "content": "working"},
                    {
                        "type": "system",
                        "subtype": "turn_duration",
                        "durationMs": 626006,
                        "messageCount": 47,
                        "preventedContinuation": False,
                    },
                ],
            )
            self.assertEqual(cr.classify_exit(path, 0), cr.Cause.TURN_CAP)

    def test_turn_cap_takes_precedence_over_text_markers(self) -> None:
        """A turn_duration event wins even if earlier text mentions 429/529."""
        with TemporaryDirectory() as tmp:
            path = _write_jsonl(
                Path(tmp),
                [
                    {"type": "assistant", "content": "saw 429 earlier but recovered"},
                    {"type": "system", "subtype": "turn_duration", "durationMs": 1},
                ],
            )
            self.assertEqual(cr.classify_exit(path, 0), cr.Cause.TURN_CAP)

    def test_api_529_via_overloaded_text(self) -> None:
        """Mode A: tail contains 'Overloaded' or '529' → API_529 (heuristic until OQ3)."""
        with TemporaryDirectory() as tmp:
            path = _write_jsonl(
                Path(tmp),
                [
                    {"type": "user", "content": "go"},
                    {"type": "system", "subtype": "error", "message": "API Overloaded (529)"},
                ],
            )
            self.assertEqual(cr.classify_exit(path, 1), cr.Cause.API_529)

    def test_api_429_via_rate_limit_text(self) -> None:
        """Tail contains '429' or 'rate_limit' → API_429."""
        with TemporaryDirectory() as tmp:
            path = _write_jsonl(
                Path(tmp),
                [
                    {"type": "system", "subtype": "error", "message": "rate_limit hit"},
                ],
            )
            self.assertEqual(cr.classify_exit(path, 1), cr.Cause.API_429)

    def test_crash_when_log_missing_and_nonzero_rc(self) -> None:
        """No session log + nonzero rc → CRASH (process died early)."""
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist.jsonl"
            self.assertEqual(cr.classify_exit(missing, 137), cr.Cause.CRASH)

    def test_crash_when_clean_log_but_nonzero_rc(self) -> None:
        """Clean log + nonzero rc + no recognized markers → CRASH."""
        with TemporaryDirectory() as tmp:
            path = _write_jsonl(
                Path(tmp),
                [
                    {"type": "user", "content": "go"},
                    {"type": "assistant", "content": "thinking"},
                ],
            )
            self.assertEqual(cr.classify_exit(path, 1), cr.Cause.CRASH)

    def test_unknown_when_log_missing_and_zero_rc(self) -> None:
        """No log + zero rc → UNKNOWN (clean exit but no signal)."""
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no.jsonl"
            self.assertEqual(cr.classify_exit(missing, 0), cr.Cause.UNKNOWN)

    def test_unknown_when_clean_log_zero_rc_no_markers(self) -> None:
        """Clean log + zero rc + no markers → UNKNOWN."""
        with TemporaryDirectory() as tmp:
            path = _write_jsonl(
                Path(tmp),
                [
                    {"type": "user", "content": "go"},
                    {"type": "assistant", "content": "done"},
                ],
            )
            self.assertEqual(cr.classify_exit(path, 0), cr.Cause.UNKNOWN)

    def test_malformed_jsonl_lines_are_skipped(self) -> None:
        """Bad lines in the log don't crash the classifier — they're skipped."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text(
                "not-json\n" + json.dumps({"type": "system", "subtype": "turn_duration"}) + "\n"
                "also-not-json\n"
            )
            self.assertEqual(cr.classify_exit(path, 0), cr.Cause.TURN_CAP)


# ---------------------------------------------------------------------------
# retry_delay — per-cause schedule plus boundary behavior
# ---------------------------------------------------------------------------


class RetryDelayTests(unittest.TestCase):
    def test_turn_cap_uses_short_delay(self) -> None:
        self.assertEqual(cr.retry_delay(cr.Cause.TURN_CAP, 1), 5)
        self.assertEqual(cr.retry_delay(cr.Cause.TURN_CAP, 5), 5)

    def test_api_529_exponential_schedule(self) -> None:
        """First retry 30s, then 60, 120, 300, 600."""
        self.assertEqual(cr.retry_delay(cr.Cause.API_529, 1), 30)
        self.assertEqual(cr.retry_delay(cr.Cause.API_529, 2), 60)
        self.assertEqual(cr.retry_delay(cr.Cause.API_529, 3), 120)
        self.assertEqual(cr.retry_delay(cr.Cause.API_529, 4), 300)
        self.assertEqual(cr.retry_delay(cr.Cause.API_529, 5), 600)

    def test_attempt_beyond_schedule_clamps_to_last(self) -> None:
        """Attempt > schedule length reuses the final entry instead of erroring."""
        self.assertEqual(cr.retry_delay(cr.Cause.API_529, 99), 600)
        self.assertEqual(cr.retry_delay(cr.Cause.TURN_CAP, 99), 5)

    def test_crash_and_unknown_share_conservative_delay(self) -> None:
        self.assertEqual(cr.retry_delay(cr.Cause.CRASH, 1), 60)
        self.assertEqual(cr.retry_delay(cr.Cause.UNKNOWN, 1), 60)

    def test_attempt_below_one_raises(self) -> None:
        with self.assertRaises(ValueError):
            cr.retry_delay(cr.Cause.TURN_CAP, 0)


# ---------------------------------------------------------------------------
# handoff_complete + expected_terminal_step — phase-progression logic
# ---------------------------------------------------------------------------


class HandoffTests(unittest.TestCase):
    def test_step_inside_template_phase_is_not_complete(self) -> None:
        """A step still in the template's own list means mid-work."""
        self.assertFalse(cr.handoff_complete("implement", "worker"))
        self.assertFalse(cr.handoff_complete("plan", "worker"))

    def test_step_outside_template_phase_is_complete(self) -> None:
        """A step belonging to the next pool means handoff advanced."""
        self.assertTrue(cr.handoff_complete("read-diff", "worker"))  # reviewer's first step
        self.assertTrue(cr.handoff_complete("run-tests", "worker"))  # tester step

    def test_empty_step_is_not_complete(self) -> None:
        """An unset step (worker never started) is NOT a handoff."""
        self.assertFalse(cr.handoff_complete("", "worker"))

    def test_unknown_template_raises(self) -> None:
        with self.assertRaises(ValueError):
            cr.handoff_complete("anything", "no-such-template")

    def test_expected_terminal_step_returns_last(self) -> None:
        self.assertEqual(cr.expected_terminal_step("worker"), "submit-and-exit")
        self.assertEqual(cr.expected_terminal_step("tester"), "submit-and-exit")

    def test_expected_terminal_step_unknown_template_raises(self) -> None:
        with self.assertRaises(ValueError):
            cr.expected_terminal_step("ghost")


# ---------------------------------------------------------------------------
# build_continuation_prompt — the OQ1-validated text
# ---------------------------------------------------------------------------


class ContinuationPromptTests(unittest.TestCase):
    def test_contains_the_validated_phrases(self) -> None:
        """OQ1's exact prompt requires three load-bearing phrases."""
        prompt = cr.build_continuation_prompt()
        self.assertIn("You were interrupted", prompt)
        self.assertIn("Check git status", prompt)
        self.assertIn("Continue your plan", prompt)


# ---------------------------------------------------------------------------
# read_current_step — bd integration with injected fake runner
# ---------------------------------------------------------------------------


class ReadCurrentStepTests(unittest.TestCase):
    def test_happy_path_returns_metadata_field(self) -> None:
        fake_bd = _fake_bd_factory([_bd_ok({"current_step": "implement"})])
        self.assertEqual(cr.read_current_step("el-fake", run_bd=fake_bd), "implement")
        self.assertEqual(fake_bd.calls, [["bd", "show", "el-fake", "--json"]])

    def test_missing_metadata_returns_empty_string(self) -> None:
        fake_bd = _fake_bd_factory([_bd_ok({})])
        self.assertEqual(cr.read_current_step("el-fake", run_bd=fake_bd), "")

    def test_bd_failure_returns_empty_string(self) -> None:
        fake_bd = _fake_bd_factory([_bd_fail()])
        self.assertEqual(cr.read_current_step("el-fake", run_bd=fake_bd), "")

    def test_malformed_json_returns_empty_string(self) -> None:
        fake_bd = _fake_bd_factory(
            [subprocess.CompletedProcess(args=[], returncode=0, stdout="garbage", stderr="")]
        )
        self.assertEqual(cr.read_current_step("el-fake", run_bd=fake_bd), "")

    def test_null_current_step_is_normalized_to_empty(self) -> None:
        """A bd record carrying `current_step: null` should read as empty."""
        fake_bd = _fake_bd_factory([_bd_ok({"current_step": None})])
        self.assertEqual(cr.read_current_step("el-fake", run_bd=fake_bd), "")


# ---------------------------------------------------------------------------
# decide — top-level orchestrator
# ---------------------------------------------------------------------------


class DecideTests(unittest.TestCase):
    def _decide(
        self,
        *,
        bd_metadata: dict[str, Any],
        log_events: list[dict[str, Any]] | None,
        return_code: int,
        attempt: int,
        max_attempts: int = 5,
        template: str = "worker",
    ) -> cr.Decision:
        fake_bd = _fake_bd_factory([_bd_ok(bd_metadata)])
        with TemporaryDirectory() as tmp:
            if log_events is None:
                log_path = Path(tmp) / "missing.jsonl"
            else:
                log_path = _write_jsonl(Path(tmp), log_events)
            return cr.decide(
                bead_id="el-fake",
                template=template,
                session_log_path=log_path,
                return_code=return_code,
                attempt=attempt,
                max_attempts=max_attempts,
                run_bd=fake_bd,
            )

    def test_handoff_complete_short_circuits_to_exit_success(self) -> None:
        """If the step advanced past worker's phase, exit immediately."""
        decision = self._decide(
            bd_metadata={"current_step": "read-diff"},  # reviewer step
            log_events=[{"type": "system", "subtype": "turn_duration"}],  # would-be turn_cap
            return_code=0,
            attempt=1,
        )
        self.assertEqual(decision.action, cr.Action.EXIT_SUCCESS)
        self.assertIsNone(decision.cause)
        self.assertIsNone(decision.delay_seconds)

    def test_no_handoff_turn_cap_under_max_retries(self) -> None:
        decision = self._decide(
            bd_metadata={"current_step": "implement"},
            log_events=[{"type": "system", "subtype": "turn_duration"}],
            return_code=0,
            attempt=2,
        )
        self.assertEqual(decision.action, cr.Action.RETRY)
        self.assertEqual(decision.cause, cr.Cause.TURN_CAP)
        self.assertEqual(decision.delay_seconds, 5)

    def test_no_handoff_at_max_attempts_exhausts(self) -> None:
        decision = self._decide(
            bd_metadata={"current_step": "implement"},
            log_events=[{"type": "system", "subtype": "turn_duration"}],
            return_code=0,
            attempt=5,
            max_attempts=5,
        )
        self.assertEqual(decision.action, cr.Action.EXIT_EXHAUSTED)
        self.assertEqual(decision.cause, cr.Cause.TURN_CAP)

    def test_no_handoff_crash_retry(self) -> None:
        decision = self._decide(
            bd_metadata={"current_step": "implement"},
            log_events=None,  # no log file → CRASH given nonzero rc
            return_code=137,
            attempt=1,
        )
        self.assertEqual(decision.action, cr.Action.RETRY)
        self.assertEqual(decision.cause, cr.Cause.CRASH)
        self.assertEqual(decision.delay_seconds, 60)


# ---------------------------------------------------------------------------
# CLI — argparse dispatch + stdout shape
# ---------------------------------------------------------------------------


class CliTests(unittest.TestCase):
    def test_classify_exit_subcommand_prints_cause(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write_jsonl(Path(tmp), [{"type": "system", "subtype": "turn_duration"}])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cr.main(
                    [
                        "classify-exit",
                        "--session-log",
                        str(path),
                        "--return-code",
                        "0",
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertEqual(buf.getvalue().strip(), "turn_cap")

    def test_retry_delay_subcommand_prints_seconds(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cr.main(["retry-delay", "--cause", "api_529", "--attempt", "3"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), "120")

    def test_retry_delay_unknown_cause_exits_nonzero(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            rc = cr.main(["retry-delay", "--cause", "nope", "--attempt", "1"])
        self.assertEqual(rc, 2)
        self.assertIn("unknown cause", err.getvalue())

    def test_build_prompt_subcommand(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cr.main(["build-prompt"])
        self.assertEqual(rc, 0)
        self.assertIn("You were interrupted", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
