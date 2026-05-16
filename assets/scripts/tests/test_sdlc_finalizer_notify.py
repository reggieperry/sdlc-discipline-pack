"""Tests for the SDLC finalizer notification wrapper (pack #44 sub-story 2).

The wrapper composes a human-readable subject + body for the
human_required-PR notification path, then invokes `sdlc-notify.sh`. Tests
provide fake `bd` (returns canned story title) and fake `msmtp` (records
what would have been sent) shims via PATH.

stdlib-only (`unittest` + tempfile + subprocess + textwrap). Matches the
pack's existing test convention.

Run with:

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import os
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _helpers import _fake_msmtp, _write_executable

WRAPPER_PATH = Path(__file__).resolve().parent.parent / "sdlc-finalizer-notify.sh"
NOTIFY_PATH = Path(__file__).resolve().parent.parent / "sdlc-notify.sh"


def _fake_bd_with_title(tmp: Path, *, title: str) -> Path:
    """Build a fake `bd` whose `bd show <id> --json` returns the canned title.

    Stays here (not in `_helpers.py`) because no other test file currently
    needs this exact shape. Lift to `_helpers.py` when a second consumer
    appears.
    """
    path = tmp / "bd"
    body = textwrap.dedent(
        f"""\
        #!/bin/bash
        case "$1" in
            show)
                echo '[{{"id":"el-fake","title":"{title}"}}]'
                ;;
        esac
        exit 0
        """
    )
    _write_executable(path, body)
    return path


class FinalizerNotifySubjectTests(unittest.TestCase):
    """Cycle 6 — subject template composition.

    The finalizer hook fires when a PR is parked at `final_state=
    pr_open_for_human`. The operator's email inbox is the surface; the
    subject is the only thing they see before deciding to open the
    message. Format pins the rig name, PR number, and story title so the
    operator can triage at a glance.

    Expected shape: `[<rig>] PR <#> open for review: <story-title>`
    """

    def test_subject_contains_rig_pr_number_and_title(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _fake_msmtp(tmp)
            _fake_bd_with_title(tmp, title="Decision audit trail")

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "SDLC_NOTIFY_RECIPIENT": "ghostdogsamurai@fastmail.fm",
            }
            result = subprocess.run(
                [
                    str(WRAPPER_PATH),
                    "--rig",
                    "elder",
                    "--story-id",
                    "el-fake",
                    "--pr-url",
                    "https://github.com/reggieperry/elder_trading_system/pull/230",
                    "--recommendation",
                    "human_required",
                    "--signals",
                    "A",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"wrapper should exit 0 on happy path; stderr={result.stderr!r}",
            )
            stdin_log = (tmp / "msmtp-stdin.log").read_text()
            self.assertIn(
                "Subject: ",
                stdin_log,
                f"piped email should carry a Subject header; got: {stdin_log!r}",
            )
            self.assertIn(
                "[elder]",
                stdin_log,
                f"subject should bracket the rig name; got: {stdin_log!r}",
            )
            self.assertIn(
                "PR 230",
                stdin_log,
                f"subject should name the PR number; got: {stdin_log!r}",
            )
            self.assertIn(
                "Decision audit trail",
                stdin_log,
                f"subject should include the story title; got: {stdin_log!r}",
            )


class FinalizerNotifyBodyTests(unittest.TestCase):
    """Cycle 7 — body composition.

    The body is what the operator sees AFTER opening the message. The
    subject answered "should I look?"; the body answers "what do I need
    to know to decide?" — PR link, the reviewer's merge-readiness tier,
    and which architectural signals fired. Three pieces are load-bearing.
    """

    def test_body_contains_pr_url_recommendation_and_signals(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _fake_msmtp(tmp)
            _fake_bd_with_title(tmp, title="Decision audit trail")

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "SDLC_NOTIFY_RECIPIENT": "ghostdogsamurai@fastmail.fm",
            }
            result = subprocess.run(
                [
                    str(WRAPPER_PATH),
                    "--rig",
                    "elder",
                    "--story-id",
                    "el-fake",
                    "--pr-url",
                    "https://github.com/reggieperry/elder_trading_system/pull/230",
                    "--recommendation",
                    "human_required",
                    "--signals",
                    "A",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            stdin_log = (tmp / "msmtp-stdin.log").read_text()
            self.assertIn(
                "https://github.com/reggieperry/elder_trading_system/pull/230",
                stdin_log,
                f"body should carry the PR URL; got: {stdin_log!r}",
            )
            self.assertIn(
                "human_required",
                stdin_log,
                f"body should name the reviewer recommendation tier; got: {stdin_log!r}",
            )
            self.assertIn(
                "Signal",
                stdin_log,
                f"body should label architectural signals; got: {stdin_log!r}",
            )
            self.assertIn(
                "A",
                stdin_log,
                f"body should list the signals fired; got: {stdin_log!r}",
            )


if __name__ == "__main__":
    unittest.main()
