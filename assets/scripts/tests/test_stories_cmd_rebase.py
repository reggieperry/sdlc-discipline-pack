"""Characterization tests for stories.py's cmd_rebase (audit finding #7 refactor backfill).

v2.30's audit #7 Extract Function moves cmd_rebase from 118 lines to ~45 lines via
three extracts (_find_unique_closed_bead, _check_pr_still_open,
_reopen_bead_routed_to_finalizer). The Tier 3 design specified characterization
tests before refactor; the refactor shipped without them in the v2.30 sprint.
These tests backfill the gap so future refactors of cmd_rebase have a behavior pin.

Per Feathers's characterization-test discipline: tests pin current behavior, not
the right answer. Three scenarios:

  - Happy path: closed bead with final_state=pr_open_for_human + open PR
    → bd update fires with --status=open --assignee='' --set-metadata=gc.routed_to=...
  - No-matching-bead: bd list returns empty → exit 1, stderr names story_id
  - PR-closed: gh pr view returns state=CLOSED → exit 1, no bd update fires

Uses the existing spy factories from `_spies.py` (spy_bd_dispatch + spy_gh_pr_view)
rather than re-rolling fakes.

stdlib-only (unittest + tempfile + subprocess + json). Matches pack convention.

Run with::

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _spies import spy_bd_dispatch, spy_gh_pr_view

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


def _setup_rig_root(tmp: Path) -> Path:
    """Create a minimal directory layout that satisfies stories.py's find_rig_root —
    just a `stories/` subdirectory under the rig root."""
    rig_root = tmp / "rig"
    (rig_root / "stories").mkdir(parents=True)
    return rig_root


def _invoke_rebase(rig_root: Path, fakes_dir: Path, story_id: str) -> subprocess.CompletedProcess:
    """Run `python3 stories.py rebase <story_id>` from rig_root with fakes on PATH."""
    env = {
        **os.environ,
        "PATH": f"{fakes_dir}:{os.environ.get('PATH', '')}",
    }
    return subprocess.run(
        ["python3", str(STORIES_PY), "rebase", story_id],
        cwd=rig_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _bead_record(
    bead_id: str,
    story_id: str,
    *,
    final_state: str = "pr_open_for_human",
    pr_url: str = "https://github.com/example/repo/pull/42",
    rig: str = "alpha",
) -> dict:
    return {
        "id": bead_id,
        "status": "closed",
        "metadata": {
            "story_id": story_id,
            "final_state": final_state,
            "pr_url": pr_url,
            "rig": rig,
        },
    }


class CmdRebaseCharacterizationTests(unittest.TestCase):
    def test_happy_path_reopens_bead_routed_to_finalizer(self) -> None:
        """Closed bead with pr_open_for_human + open PR → bd update with the right argv."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            rig_root = _setup_rig_root(tmp)
            fakes_dir = tmp / "fakes"
            fakes_dir.mkdir()

            bead = _bead_record("el-1", "EL-001")
            spy_bd_dispatch(fakes_dir, {"list": json.dumps([bead])})
            spy_gh_pr_view(fakes_dir, {42: json.dumps({"state": "OPEN"})})

            result = _invoke_rebase(rig_root, fakes_dir, "EL-001")

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            bd_log = (fakes_dir / "bd-argv.log").read_text()
            update_calls = [line for line in bd_log.splitlines() if line.startswith("update ")]
            self.assertEqual(
                len(update_calls), 1, f"expected one bd update call; got log:\n{bd_log}"
            )
            update_line = update_calls[0]
            self.assertIn("el-1", update_line)
            self.assertIn("--status=open", update_line)
            self.assertIn("--assignee", update_line)
            self.assertIn("gc.routed_to=alpha/sdlc-discipline.finalizer", update_line)

    def test_no_matching_bead_exits_1(self) -> None:
        """bd list returns empty → exit 1, stderr names the story_id."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            rig_root = _setup_rig_root(tmp)
            fakes_dir = tmp / "fakes"
            fakes_dir.mkdir()
            spy_bd_dispatch(fakes_dir, {"list": "[]"})
            # gh is wired but never reached
            spy_gh_pr_view(fakes_dir, {})

            result = _invoke_rebase(rig_root, fakes_dir, "EL-001")

            self.assertEqual(result.returncode, 1)
            self.assertIn("no closed bead found for story_id 'EL-001'", result.stderr)
            # No bd update should fire when no bead matches.
            bd_log_path = fakes_dir / "bd-argv.log"
            if bd_log_path.exists():
                self.assertNotIn("update", bd_log_path.read_text())

    def test_closed_pr_exits_1_no_bd_update(self) -> None:
        """gh pr view returns state=CLOSED → exit 1, no bd update fires."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            rig_root = _setup_rig_root(tmp)
            fakes_dir = tmp / "fakes"
            fakes_dir.mkdir()

            bead = _bead_record("el-1", "EL-001")
            spy_bd_dispatch(fakes_dir, {"list": json.dumps([bead])})
            spy_gh_pr_view(fakes_dir, {42: json.dumps({"state": "CLOSED"})})

            result = _invoke_rebase(rig_root, fakes_dir, "EL-001")

            self.assertEqual(result.returncode, 1)
            self.assertIn("not OPEN", result.stderr)
            bd_log_path = fakes_dir / "bd-argv.log"
            if bd_log_path.exists():
                self.assertNotIn("update", bd_log_path.read_text())

    def test_multiple_matching_beads_exits_1_with_ambiguous_message(self) -> None:
        """Two closed beads with the same metadata.story_id → ambiguous; exit 1
        without picking. The operator must disambiguate via bd manually.

        Pins the `_find_unique_closed_bead` helper's second error branch:
        `len(matching) > 1`.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            rig_root = _setup_rig_root(tmp)
            fakes_dir = tmp / "fakes"
            fakes_dir.mkdir()

            bead_a = _bead_record("el-1", "EL-001")
            bead_b = _bead_record("el-2", "EL-001")  # Same story_id, different bead ID
            spy_bd_dispatch(fakes_dir, {"list": json.dumps([bead_a, bead_b])})
            spy_gh_pr_view(fakes_dir, {})

            result = _invoke_rebase(rig_root, fakes_dir, "EL-001")

            self.assertEqual(result.returncode, 1)
            self.assertIn("multiple closed beads", result.stderr)
            self.assertIn("el-1", result.stderr)
            self.assertIn("el-2", result.stderr)
            self.assertIn("pick the bead manually", result.stderr)
            # Neither bead should have been updated.
            bd_log_path = fakes_dir / "bd-argv.log"
            if bd_log_path.exists():
                self.assertNotIn("update", bd_log_path.read_text())

    def test_gh_pr_view_failure_forwards_exit_code(self) -> None:
        """gh pr view exits non-zero → cmd_rebase forwards the gh exit and stops.

        Pins the `_check_pr_still_open` helper's subprocess-failure branch.
        We simulate the gh failure by writing an explicit fake gh that exits 1
        rather than spy_gh_pr_view's silent-pass — spy_gh_pr_view always exits 0.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            rig_root = _setup_rig_root(tmp)
            fakes_dir = tmp / "fakes"
            fakes_dir.mkdir()

            bead = _bead_record("el-1", "EL-001")
            spy_bd_dispatch(fakes_dir, {"list": json.dumps([bead])})

            # Direct-write a failing fake gh (spy factories don't have an
            # exit-N variant for gh today; could be a future _spies.py extension).
            from _spies import write_executable

            gh_path = fakes_dir / "gh"
            gh_body = (
                "#!/bin/bash\n"
                f'echo "$@" >> "{fakes_dir}/gh-argv.log"\n'
                'echo "gh: simulated upstream failure" >&2\n'
                "exit 1\n"
            )
            write_executable(gh_path, gh_body)

            result = _invoke_rebase(rig_root, fakes_dir, "EL-001")

            self.assertNotEqual(result.returncode, 0, "gh failure must forward exit code")
            self.assertIn("gh pr view", result.stderr)
            bd_log_path = fakes_dir / "bd-argv.log"
            if bd_log_path.exists():
                self.assertNotIn("update", bd_log_path.read_text())

    def test_bd_update_failure_forwards_exit_code(self) -> None:
        """bd update exits non-zero → cmd_rebase forwards the bd exit. Pins the
        `_reopen_bead_routed_to_finalizer` helper's subprocess-failure branch.

        The setup uses a custom fake bd that returns a normal `bd list` payload
        but fails on `bd update`. spy_bd_dispatch's update branch silently
        succeeds, so a custom fake is required to exercise the failure shape.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            rig_root = _setup_rig_root(tmp)
            fakes_dir = tmp / "fakes"
            fakes_dir.mkdir()

            bead = _bead_record("el-1", "EL-001")
            bead_json = json.dumps([bead])

            from _spies import write_executable

            bd_path = fakes_dir / "bd"
            bd_body = (
                "#!/bin/bash\n"
                f'echo "$@" >> "{fakes_dir}/bd-argv.log"\n'
                'if [ "$1" = "-C" ]; then shift 2; fi\n'
                'if [ "$1" = "list" ]; then\n'
                "    cat <<'__BD_LIST_EOF__'\n"
                f"{bead_json}\n"
                "__BD_LIST_EOF__\n"
                "    exit 0\n"
                "fi\n"
                'if [ "$1" = "update" ]; then\n'
                '    echo "bd update: simulated upstream failure" >&2\n'
                "    exit 7\n"
                "fi\n"
                "exit 0\n"
            )
            write_executable(bd_path, bd_body)
            spy_gh_pr_view(fakes_dir, {42: json.dumps({"state": "OPEN"})})

            result = _invoke_rebase(rig_root, fakes_dir, "EL-001")

            self.assertEqual(result.returncode, 7, "bd update exit must forward verbatim")
            self.assertIn("bd update", result.stderr)
            # Verify bd update was invoked — the failure forwards from this call,
            # not from an earlier branch.
            bd_log = (fakes_dir / "bd-argv.log").read_text()
            self.assertIn("update el-1", bd_log)


if __name__ == "__main__":
    unittest.main()
