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
import stat
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-stale-pr-sweeper.sh"
assert SCRIPT_PATH.exists(), f"sdlc-stale-pr-sweeper.sh not found at {SCRIPT_PATH}"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_gc(tmp: Path, rig_list_json: str) -> Path:
    """Build a fake ``gc`` that returns rig_list_json for ``gc rig list --json``."""
    path = tmp / "gc"
    body = textwrap.dedent(
        f"""\
        #!/bin/bash
        echo "$@" >> "{tmp}/gc-argv.log"
        if [ "$1" = "rig" ] && [ "$2" = "list" ]; then
            cat <<'__GC_EOF__'
{rig_list_json}
__GC_EOF__
            exit 0
        fi
        exit 0
        """
    )
    _write_executable(path, body)
    return path


def _fake_bd(tmp: Path, bead_responses: dict[str, str]) -> Path:
    """Fake ``bd`` that returns canned responses for ``bd list`` and ``bd show``.

    ``bead_responses`` maps:
      "list"      -> JSON string for ``bd list --json``
      "<bead_id>" -> JSON string for ``bd show <bead_id> --json``
    """
    path = tmp / "bd"
    list_response = bead_responses.get("list", "[]")
    # Build the bd show dispatch as a case statement.
    show_cases: list[str] = []
    for bead_id, json_body in bead_responses.items():
        if bead_id == "list":
            continue
        show_cases.append(
            f"""    {bead_id})
        cat <<'__BD_SHOW_EOF__'
{json_body}
__BD_SHOW_EOF__
        ;;"""
        )
    show_block = "\n".join(show_cases) if show_cases else "    *) echo '[]' ;;"

    body = textwrap.dedent(
        f"""\
        #!/bin/bash
        echo "$@" >> "{tmp}/bd-argv.log"
        # Strip -C <dir> prefix if present.
        if [ "$1" = "-C" ]; then shift 2; fi
        if [ "$1" = "list" ]; then
            cat <<'__BD_LIST_EOF__'
{list_response}
__BD_LIST_EOF__
            exit 0
        fi
        if [ "$1" = "show" ]; then
            case "$2" in
{show_block}
                *) echo '[]' ;;
            esac
            exit 0
        fi
        if [ "$1" = "update" ]; then
            # Record the update call; exit 0.
            exit 0
        fi
        exit 0
        """
    )
    _write_executable(path, body)
    return path


def _fake_gh(tmp: Path, pr_responses: dict[int, str]) -> Path:
    """Fake ``gh`` that returns canned responses for ``gh pr view <N>``."""
    path = tmp / "gh"
    cases: list[str] = []
    for pr_num, json_body in pr_responses.items():
        cases.append(
            f"""    {pr_num})
        cat <<'__GH_PR_EOF__'
{json_body}
__GH_PR_EOF__
        ;;"""
        )
    case_block = "\n".join(cases) if cases else "    *) echo '{}' ;;"
    body = textwrap.dedent(
        f"""\
        #!/bin/bash
        echo "$@" >> "{tmp}/gh-argv.log"
        if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
            case "$3" in
{case_block}
                *) echo '{{}}' ;;
            esac
            exit 0
        fi
        exit 0
        """
    )
    _write_executable(path, body)
    return path


def _fake_python3_stories(tmp: Path) -> Path:
    """Fake the ``python3 <bridge>/stories.py rebase <id>`` invocation by
    recording argv via a python3 shim. The sweeper calls python3 directly
    so we replace python3 on PATH; legitimate python3 needs delegated to
    /usr/bin/python3 for unrelated invocations (none in the sweeper path
    today, but keep the fallback)."""
    path = tmp / "python3"
    body = textwrap.dedent(
        f"""\
        #!/bin/bash
        echo "$@" >> "{tmp}/python3-argv.log"
        # If the first non-flag arg looks like a stories.py path, fake-succeed.
        for arg in "$@"; do
            case "$arg" in
                *stories.py) exit 0 ;;
            esac
        done
        # Fallback: dispatch to the real python3 for anything else.
        exec /usr/bin/python3 "$@"
        """
    )
    _write_executable(path, body)
    return path


def _bead_json(
    bead_id: str,
    status: str = "closed",
    final_state: str = "pr_open_for_human",
    pr_url: str = "https://github.com/example/repo/pull/100",
    rig: str = "elder",
) -> str:
    return json.dumps(
        [
            {
                "id": bead_id,
                "status": status,
                "metadata": {
                    "rig": rig,
                    "final_state": final_state,
                    "pr_url": pr_url,
                    "story_id": bead_id,
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
    gate (sweeper requires SDLC_WATCHER_ENABLED!=false; default is enabled)."""
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
        gc = _fake_gc(self._tmp, rigs_json if rigs_json is not None else _rig_list_one(rig_root))
        bd = _fake_bd(
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
        gh = _fake_gh(self._tmp, {pr_number: json.dumps(pr_payload)})
        py = _fake_python3_stories(self._tmp)

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
            any("rebase" in c and "el-test" in c for c in py_calls),
            f"expected stories.py rebase call; got {py_calls}",
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
        self.assertIn("el-test", joined)
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


if __name__ == "__main__":
    unittest.main()
