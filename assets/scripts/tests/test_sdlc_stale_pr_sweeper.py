"""Tests for ``sdlc-stale-pr-sweeper.sh`` (pack #38 + #39).

Black-box subprocess tests with recording fake binaries for ``gc``,
``bd``, ``gh``, and ``python3 stories.py`` on the test's PATH. Each
test stands up a contrived rig environment (rig dir + fixture beads +
fixture PR responses), invokes the sweeper, and inspects the fakes'
recorded argv to verify the sweeper called the right downstream
command (or didn't).

Six scenarios in this initial harness:

1. Sweeper sees CONFLICTING PR -> triggers ``stories.py rebase``
2. Sweeper sees CLEAN PR -> no rebase, no reconciliation
3. Sweeper sees CLOSED PR -> no action (bead stays in pr_open_for_human)
4. Sweeper sees MERGED PR (#39) -> reconciles bead via ``bd update final_state=merged``
5. Sweeper sees MERGED PR with empty mergedAt/mergeCommit -> still reconciles
6. ``gc rig list`` returns zero rigs -> sweeper exits cleanly

The remaining six scenarios from #38 (BEHIND/DIRTY rebase regressions,
dedup check, suspended-rig skip, HQ-rig skip, watcher cases) are
follow-on work — separate test classes can be added without changing
this file's infrastructure.

stdlib-only (unittest + tempfile + subprocess + textwrap). Matches pack
convention.

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

from _spies import (
    spy_bd_dispatch,
    spy_gc_rig_list,
    spy_gh_pr_view,
    spy_python3_stories_passthrough,
)

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-stale-pr-sweeper.sh"
assert SCRIPT_PATH.exists(), f"sdlc-stale-pr-sweeper.sh not found at {SCRIPT_PATH}"


def _bead_json(
    bead_id: str,
    status: str = "closed",
    final_state: str = "pr_open_for_human",
    pr_url: str = "https://github.com/example/repo/pull/100",
    rig: str = "elder",
    story_id: str = "EL-9001",
) -> str:
    # story_id is DISTINCT from bead_id by default: a spec's story_id (EL-9001)
    # is not its bead id (el-test). #210 — the reconcile path must `bd update`
    # the bead id, not the story id; an equal-id fake hid that bug.
    return json.dumps(
        [
            {
                "id": bead_id,
                "status": status,
                "metadata": {
                    "rig": rig,
                    "final_state": final_state,
                    "pr_url": pr_url,
                    "story_id": story_id,
                },
            }
        ]
    )


def _bead_list(beads: list[dict]) -> str:
    return json.dumps(beads)


def _rig_list_one(rig_root: str) -> str:
    return json.dumps(
        {
            "rigs": [
                {
                    "name": "elder",
                    "path": rig_root,
                    "hq": False,
                    "suspended": False,
                }
            ]
        }
    )


def _setup_env(tmp: Path, gc_path: Path, bd_path: Path, gh_path: Path, py_path: Path) -> dict:
    """Build a test env that pins fakes on PATH and skips the SDLC enabled
    gate (sweeper requires SDLC_STALE_PR_SWEEPER_ENABLED!=false; default is enabled)."""
    return {
        **os.environ,
        "PATH": f"{tmp}:{os.environ.get('PATH', '')}",
        "PACK_DIR": str(tmp / "pack"),  # not actually used in non-rebase paths
        "GC_CITY_ROOT": str(tmp / "city"),
    }


class SweeperReconciliationTests(unittest.TestCase):
    """Cycle: sweeper observes PR state and acts (or doesn't) on the bead."""

    def setUp(self) -> None:
        self._tmpdir_ctx = TemporaryDirectory()
        self._tmp = Path(self._tmpdir_ctx.name)
        (self._tmp / "pack").mkdir()
        (self._tmp / "city").mkdir()
        (self._tmp / "city" / "city.toml").write_text("[city]\n")
        (self._tmp / "rig").mkdir()
        # Touch a fake pack overlay path the sweeper references (only matters
        # for the rebase path, but harmless to create).
        bridge_dir = (
            self._tmp
            / "pack"
            / "overlay"
            / "per-provider"
            / "claude"
            / ".claude"
            / "sdlc-discipline"
        )
        bridge_dir.mkdir(parents=True)
        (bridge_dir / "stories.py").write_text("# stub\n")

    def tearDown(self) -> None:
        self._tmpdir_ctx.cleanup()

    def _run_sweeper(
        self,
        *,
        bead_id: str = "el-test",
        bead_status: str = "closed",
        pr_state: str = "OPEN",
        merge_state: str = "CLEAN",
        pr_number: int = 100,
        merged_at: str | None = None,
        merge_sha: str | None = None,
        rigs_json: str | None = None,
    ) -> subprocess.CompletedProcess:
        rig_root = str(self._tmp / "rig")
        gc = spy_gc_rig_list(
            self._tmp, rigs_json if rigs_json is not None else _rig_list_one(rig_root)
        )
        bd = spy_bd_dispatch(
            self._tmp,
            {
                "list": _bead_list(
                    [
                        json.loads(_bead_json(bead_id, status=bead_status))[0],
                    ]
                ),
                bead_id: _bead_json(bead_id, status=bead_status),
            },
        )
        pr_payload: dict = {"mergeStateStatus": merge_state, "state": pr_state}
        if merged_at is not None or merge_sha is not None:
            pr_payload["mergedAt"] = merged_at or ""
            pr_payload["mergeCommit"] = {"oid": merge_sha or ""}
        gh = spy_gh_pr_view(self._tmp, {pr_number: json.dumps(pr_payload)})
        py = spy_python3_stories_passthrough(self._tmp)

        env = _setup_env(self._tmp, gc, bd, gh, py)
        return subprocess.run(
            ["bash", str(SCRIPT_PATH)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            cwd=self._tmp,
        )

    def _bd_calls(self) -> list[str]:
        log = self._tmp / "bd-argv.log"
        return log.read_text().strip().splitlines() if log.exists() else []

    def _python_calls(self) -> list[str]:
        log = self._tmp / "python3-argv.log"
        return log.read_text().strip().splitlines() if log.exists() else []

    def test_conflicting_pr_triggers_rebase(self) -> None:
        """Scenario 1: PR is OPEN + CONFLICTING -> sweeper calls stories.py rebase."""
        result = self._run_sweeper(merge_state="CONFLICTING", pr_state="OPEN")
        self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
        py_calls = self._python_calls()
        self.assertTrue(
            any("rebase" in c and "EL-9001" in c for c in py_calls),
            f"expected stories.py rebase call on the story id; got {py_calls}",
        )

    def test_clean_pr_no_action(self) -> None:
        """Scenario 2: PR is OPEN + CLEAN -> no rebase, no bd update."""
        result = self._run_sweeper(merge_state="CLEAN", pr_state="OPEN")
        self.assertEqual(result.returncode, 0)
        py_calls = self._python_calls()
        self.assertFalse(
            any("rebase" in c for c in py_calls),
            f"CLEAN PR should not trigger rebase; got {py_calls}",
        )
        bd_calls = self._bd_calls()
        self.assertFalse(
            any("update" in c and "final_state=merged" in c for c in bd_calls),
            f"CLEAN PR should not trigger reconciliation; got {bd_calls}",
        )

    def test_closed_pr_no_action(self) -> None:
        """Scenario 3: PR is CLOSED (not merged) -> sweeper takes no action."""
        result = self._run_sweeper(pr_state="CLOSED", merge_state="UNKNOWN")
        self.assertEqual(result.returncode, 0)
        py_calls = self._python_calls()
        self.assertFalse(any("rebase" in c for c in py_calls))
        bd_calls = self._bd_calls()
        self.assertFalse(
            any("update" in c and "final_state=merged" in c for c in bd_calls),
            "CLOSED PR should not trigger merge reconciliation",
        )

    def test_merged_pr_reconciles_bead_metadata(self) -> None:
        """Scenario 4 (#39): PR is MERGED -> sweeper updates bead final_state=merged."""
        result = self._run_sweeper(
            pr_state="MERGED",
            merge_state="UNKNOWN",
            merged_at="2026-05-22T20:00:00Z",
            merge_sha="abc123def456abc123def456abc123def456abc1",
        )
        self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
        bd_calls = self._bd_calls()
        update_calls = [c for c in bd_calls if c.startswith("update ")]
        self.assertTrue(update_calls, f"expected bd update call; got bd_calls={bd_calls}")
        joined = " | ".join(update_calls)
        self.assertIn("el-test", joined)  # #210: the BEAD id
        self.assertNotIn("EL-9001", joined)  # #210: NOT the story id
        self.assertIn("final_state=merged", joined)
        self.assertIn("final_merged_at=2026-05-22T20:00:00Z", joined)
        self.assertIn("final_merged_sha=abc123def456abc123def456abc123def456abc1", joined)
        # And no rebase
        py_calls = self._python_calls()
        self.assertFalse(any("rebase" in c for c in py_calls))

    def test_merged_pr_without_timestamp_still_reconciles(self) -> None:
        """Scenario 5: gh returns MERGED but mergedAt + mergeCommit empty -> still reconcile."""
        result = self._run_sweeper(
            pr_state="MERGED",
            merge_state="UNKNOWN",
            merged_at="",
            merge_sha="",
        )
        self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
        bd_calls = self._bd_calls()
        update_calls = [c for c in bd_calls if c.startswith("update ")]
        self.assertTrue(update_calls, f"expected bd update call; got {bd_calls}")
        joined = " | ".join(update_calls)
        self.assertIn("final_state=merged", joined)
        # final_merged_at / final_merged_sha are conditional — when empty, the
        # bd update call should omit them entirely.
        self.assertNotIn("final_merged_at=", joined)
        self.assertNotIn("final_merged_sha=", joined)

    def test_zero_rigs_exits_clean(self) -> None:
        """Scenario 6: gc rig list returns zero rigs -> sweeper exits 0 quietly."""
        result = self._run_sweeper(rigs_json='{"rigs": []}')
        self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
        # No bead-level operations should have fired.
        self.assertEqual(self._bd_calls(), [])
        self.assertEqual(self._python_calls(), [])


class FeatureGateTests(unittest.TestCase):
    """v2.30 removed the SDLC_SWEEPER_ENABLED legacy fallback that v2.29.9 had
    shipped as a one-release deprecation. Only the new name remains."""

    def _invoke(self, tmp: Path, env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
        """Invoke the sweeper inside the caller's tmpdir so post-conditions
        can be asserted on the recording-fake's log files. Wires a
        recording-fake `gc` so the gate-disable assertions can prove no
        downstream binary was invoked.

        Returncode 0 alone does NOT distinguish gate-disabled from
        gate-passed-with-zero-rigs (both produce rc=0). The `gc-argv.log`
        absence pins the gate-disabled branch unambiguously.
        """
        spy_gc_rig_list(tmp, '{"rigs": []}')
        env = {
            **os.environ,
            "PATH": f"{tmp}:{os.environ.get('PATH', '')}",
            "PACK_DIR": str(tmp / "pack"),
            "GC_CITY_ROOT": str(tmp / "city"),
        }
        # Strip whatever the parent shell might have set so the test env is hermetic.
        env.pop("SDLC_STALE_PR_SWEEPER_ENABLED", None)
        env.update(env_overrides)
        (tmp / "city").mkdir(parents=True, exist_ok=True)
        (tmp / "city" / "city.toml").write_text("[city]\n")
        return subprocess.run(
            [str(SCRIPT_PATH)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_false_disables(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            result = self._invoke(tmp, {"SDLC_STALE_PR_SWEEPER_ENABLED": "false"})

            self.assertEqual(result.returncode, 0)
            # Pin: the gate fired before any peer binary was invoked.
            self.assertFalse(
                (tmp / "gc-argv.log").exists(),
                f"gc should not be called when gate disabled; gc-argv.log present: {tmp / 'gc-argv.log'}",
            )

    def test_default_true_enables(self) -> None:
        """Default behavior: when the gate var is unset, the sweeper's
        gate-default is true and the script reaches gc rig list."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            result = self._invoke(tmp, {})

            self.assertEqual(result.returncode, 0)
            self.assertTrue(
                (tmp / "gc-argv.log").exists(),
                "gate-passed path should reach gc; gc-argv.log missing",
            )


if __name__ == "__main__":
    unittest.main()
