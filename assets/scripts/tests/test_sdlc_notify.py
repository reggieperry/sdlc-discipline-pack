"""Tests for the SDLC notification helper (pack #44 sub-story 1).

Black-box subprocess tests. Each test stands up a tempdir holding a fake
`msmtp` binary, prepends it to PATH, and invokes `sdlc-notify.sh` with
controlled env. The fake msmtp records its argv to `<tmp>/msmtp-argv.log`
and its stdin to `<tmp>/msmtp-stdin.log` for assertions.

stdlib-only (`unittest` + tempfile + subprocess + textwrap). Matches the
pack's existing test convention (`test_tech_debt.py`,
`test_claude_retry.py`, `test_sdlc_claude_with_retry.py`).

Run with:

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _helpers import _fake_msmtp

NOTIFY_PATH = Path(__file__).resolve().parent.parent / "sdlc-notify.sh"


class NotifyHappyPathTests(unittest.TestCase):
    """Cycle 1 — helper invokes msmtp with subject + body.

    The helper's contract is the standard "minimal email pipe": read a
    subject from --subject, read the body from stdin, send via msmtp.
    Recipient comes from SDLC_NOTIFY_RECIPIENT env (cycle 2 tests that
    specifically; this test pins the happy-path call shape).
    """

    def test_invokes_msmtp_with_recipient_and_pipes_subject_plus_body(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _fake_msmtp(tmp)

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "SDLC_NOTIFY_RECIPIENT": "user@example.com",
            }
            result = subprocess.run(
                [str(NOTIFY_PATH), "--subject", "Test alert"],
                input="Body line 1\nBody line 2\n",
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(
                result.returncode,
                0,
                f"helper should exit 0 on happy path; "
                f"stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            argv_log = (tmp / "msmtp-argv.log").read_text().strip()
            self.assertIn(
                "user@example.com",
                argv_log,
                f"msmtp should be invoked with the recipient address; got argv: {argv_log!r}",
            )
            stdin_log = (tmp / "msmtp-stdin.log").read_text()
            self.assertIn(
                "Subject: Test alert",
                stdin_log,
                f"stdin to msmtp should carry a Subject header; got: {stdin_log!r}",
            )
            self.assertIn(
                "Body line 1",
                stdin_log,
                f"stdin to msmtp should carry the piped body; got: {stdin_log!r}",
            )
            self.assertIn(
                "Body line 2",
                stdin_log,
                f"multi-line body should be preserved; got: {stdin_log!r}",
            )


class NotifyArgValidationTests(unittest.TestCase):
    """Cycles 4 + 5 — fail loud on missing required inputs.

    `--subject` and `SDLC_NOTIFY_RECIPIENT` are required for any meaningful
    notification; their absence is a misconfigured caller, not a runtime
    blip. Helper exits nonzero with a clear message rather than silently
    sending a subjectless email or an email to nowhere.
    """

    def test_missing_subject_exits_nonzero(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _fake_msmtp(tmp)
            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "SDLC_NOTIFY_RECIPIENT": "user@example.com",
            }
            result = subprocess.run(
                [str(NOTIFY_PATH)],  # no --subject
                input="Body\n",
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotEqual(
                result.returncode,
                0,
                f"helper should exit nonzero when --subject is missing; stderr={result.stderr!r}",
            )
            self.assertIn(
                "subject",
                result.stderr.lower(),
                f"stderr should mention the missing argument; got: {result.stderr!r}",
            )

    def test_missing_recipient_env_exits_nonzero(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _fake_msmtp(tmp)
            env = {
                **{k: v for k, v in os.environ.items() if k != "SDLC_NOTIFY_RECIPIENT"},
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
            }
            result = subprocess.run(
                [str(NOTIFY_PATH), "--subject", "Test"],
                input="Body\n",
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotEqual(
                result.returncode,
                0,
                "helper should exit nonzero when SDLC_NOTIFY_RECIPIENT is unset; "
                f"stderr={result.stderr!r}",
            )
            self.assertIn(
                "recipient",
                result.stderr.lower(),
                f"stderr should mention the missing env var; got: {result.stderr!r}",
            )


class NotifyFallbackTests(unittest.TestCase):
    """Cycle 3 — msmtp absence: fall back to stderr-log, exit 0.

    A missing notification substrate must never fail the chain. The chain
    will deliver work just fine without an email getting sent; we just
    want the operator to be informed via stderr (captured by claude's
    session log + the supervisor's logs) that a notification was skipped.

    Tests simulate msmtp absence via the SDLC_NOTIFY_MSMTP env override
    (default: "msmtp", resolved via PATH; tests set it to a nonexistent
    absolute path so `command -v` returns nothing without disturbing the
    host's msmtp install).
    """

    def test_falls_back_silently_when_msmtp_unavailable(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "SDLC_NOTIFY_RECIPIENT": "user@example.com",
                "SDLC_NOTIFY_MSMTP": "/nonexistent/path/to/msmtp",
            }
            result = subprocess.run(
                [str(NOTIFY_PATH), "--subject", "Test alert"],
                input="Body\n",
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"helper must exit 0 even when msmtp is unavailable "
                f"(notification gap must not fail the chain); "
                f"got rc={result.returncode} stderr={result.stderr!r}",
            )
            self.assertIn(
                "msmtp",
                result.stderr.lower(),
                f"helper should mention msmtp in its stderr message "
                f"so the operator can diagnose; got: {result.stderr!r}",
            )
            self.assertIn(
                "test alert",
                result.stderr.lower(),
                f"stderr should include the subject of the skipped "
                f"notification so it's not lost entirely; got: {result.stderr!r}",
            )


if __name__ == "__main__":
    unittest.main()
