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

from _spies import _write_executable, spy_msmtp

WRAPPER_PATH = Path(__file__).resolve().parent.parent / "sdlc-finalizer-notify.sh"
NOTIFY_PATH = Path(__file__).resolve().parent.parent / "sdlc-notify.sh"


def stub_bd_with_title(tmp: Path, *, title: str) -> Path:
    """Build a Test Stub for `bd` whose `bd show <id> --json` returns the
    canned title and exits 0. No argv recording — Stub, not Spy per Meszaros.

    Stays here (not in `_spies.py`) because no other test file currently
    needs this exact shape. Lift to `_spies.py` when a second consumer
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
            spy_msmtp(tmp)
            stub_bd_with_title(tmp, title="Decision audit trail")

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "SDLC_NOTIFY_RECIPIENT": "user@example.com",
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
            spy_msmtp(tmp)
            stub_bd_with_title(tmp, title="Decision audit trail")

            env = {
                **os.environ,
                "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
                "SDLC_NOTIFY_RECIPIENT": "user@example.com",
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


class FinalizerNotifyTypeTests(unittest.TestCase):
    """Cycles 1-3 (sub-story 3) — `--type` flag varies the subject prefix.

    The wrapper handles two notification kinds: PR parked for human
    review (sub-story 2 default) and PR auto-merged (sub-story 3,
    opt-in via SDLC_NOTIFY_ALL_CLOSES on the finalizer side). The
    wrapper itself doesn't know about the env gate — that's the
    finalizer's concern; the wrapper just takes a `--type` value and
    composes the matching subject.
    """

    def _invoke(self, tmp: Path, *, notify_type: str | None) -> subprocess.CompletedProcess[str]:
        """Run the wrapper with the given --type (or omit it for default).

        Test-builder helper. Test fixture is the same shape as the
        existing FinalizerNotifyBodyTests; this extracts the boilerplate
        so each `--type`-flavored test reads as one assertion's worth
        of intent.
        """
        spy_msmtp(tmp)
        stub_bd_with_title(tmp, title="Decision audit trail")
        env = {
            **os.environ,
            "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}",
            "SDLC_NOTIFY_RECIPIENT": "user@example.com",
        }
        argv = [
            str(WRAPPER_PATH),
            "--rig",
            "elder",
            "--story-id",
            "el-fake",
            "--pr-url",
            "https://github.com/reggieperry/elder_trading_system/pull/230",
            "--recommendation",
            "glance_merge",
            "--signals",
            "",
        ]
        if notify_type is not None:
            argv.extend(["--type", notify_type])
        return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=10)

    def test_type_merged_produces_auto_merged_subject(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            result = self._invoke(tmp, notify_type="merged")
            self.assertEqual(result.returncode, 0, result.stderr)
            stdin_log = (tmp / "msmtp-stdin.log").read_text()
            self.assertIn(
                "auto-merged",
                stdin_log,
                f"--type merged should produce 'auto-merged' subject; got: {stdin_log!r}",
            )

    def test_type_pr_open_for_human_produces_open_for_review_subject(
        self,
    ) -> None:
        """Cycle 2 — explicit pr_open_for_human pins the existing default behavior.

        Backward-compat regression: when the finalizer prompt later
        passes --type pr_open_for_human explicitly (sub-story 3's
        cleanup), the produced subject must remain `open for review`.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            result = self._invoke(tmp, notify_type="pr_open_for_human")
            self.assertEqual(result.returncode, 0, result.stderr)
            stdin_log = (tmp / "msmtp-stdin.log").read_text()
            self.assertIn(
                "open for review",
                stdin_log,
                f"--type pr_open_for_human should produce 'open for review' "
                f"subject; got: {stdin_log!r}",
            )
            self.assertNotIn(
                "auto-merged",
                stdin_log,
                f"--type pr_open_for_human must NOT accidentally include "
                f"'auto-merged'; got: {stdin_log!r}",
            )

    def test_unknown_type_exits_nonzero(self) -> None:
        """Cycle 3 — unknown --type value is a misconfigured caller; fail loud."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            result = self._invoke(tmp, notify_type="not-a-valid-type")
            self.assertNotEqual(
                result.returncode,
                0,
                f"wrapper should exit nonzero on unknown --type; "
                f"stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            self.assertIn(
                "not-a-valid-type",
                result.stderr,
                f"stderr should name the rejected value; got: {result.stderr!r}",
            )


if __name__ == "__main__":
    unittest.main()
