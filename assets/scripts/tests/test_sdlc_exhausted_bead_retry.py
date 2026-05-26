"""Tests for sdlc-exhausted-bead-retry.sh (pack #47 supervisor-side).

Black-box subprocess tests with recording fake binaries for gc, bd, and
sdlc-notify.sh on the test's PATH. Each test stands up a fake rig with
a bd list response carrying contrived <template>.state=exhausted beads,
invokes the script, and inspects the fake bd's argv log to verify the
right reslings + give-ups fired.

stdlib-only (unittest + tempfile + subprocess + textwrap). Matches pack
convention.

Run with::

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _spies import spy_bd_list, spy_gc_rig_list, spy_notify

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-exhausted-bead-retry.sh"
assert SCRIPT_PATH.exists(), f"sdlc-exhausted-bead-retry.sh not found at {SCRIPT_PATH}"


def _setup_rig(tmp: Path, rig_name: str = "test-rig") -> tuple[Path, Path, Path]:
    city_root = tmp / "city"
    rig_root = city_root / rig_name
    fakes_dir = tmp / "fakes"
    rig_root.mkdir(parents=True)
    fakes_dir.mkdir(parents=True)
    return city_root, rig_root, fakes_dir


def _rig_list_json(rig_name: str, rig_root: Path) -> str:
    return json.dumps(
        {"rigs": [{"name": rig_name, "path": str(rig_root), "hq": False, "suspended": False}]}
    )


def _iso_now_offset(minutes: int) -> str:
    """ISO timestamp `minutes` ago (negative = future)."""
    when = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=minutes)
    return when.isoformat().replace("+00:00", "Z")


def _bead(
    bead_id: str,
    template: str,
    exhausted_at: str | None,
    retry_count: int = 0,
    cause: str = "API_529",
    pool_target: str = "test-rig/sdlc-discipline.worker",
) -> dict:
    meta = {
        f"{template}.state": "exhausted",
        f"{template}.last_exit_cause": cause,
        "gc.routed_to": pool_target,
    }
    if exhausted_at is not None:
        meta[f"{template}.exhausted_at"] = exhausted_at
    if retry_count:
        meta[f"{template}.retry_count"] = str(retry_count)
    return {"id": bead_id, "status": "open", "metadata": meta}


def _invoke(
    fakes_dir: Path,
    city_root: Path,
    enabled: bool = True,
    backoff_minutes: int = 30,
    max_retries: int = 3,
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{fakes_dir}:{os.environ['PATH']}",
        "GC_CITY_ROOT": str(city_root),
        "PACK_DIR": str(fakes_dir / "fake-pack"),
        "SDLC_EXHAUSTED_BEAD_RETRY_ENABLED": "true" if enabled else "false",
        "SDLC_EXHAUSTED_BEAD_BACKOFF_MINUTES": str(backoff_minutes),
        "SDLC_EXHAUSTED_BEAD_MAX_RETRIES": str(max_retries),
    }
    return subprocess.run([str(SCRIPT_PATH)], env=env, capture_output=True, text=True, timeout=30)


def _bd_calls(fakes_dir: Path) -> list[str]:
    path = fakes_dir / "bd-argv.log"
    if not path.exists():
        return []
    return [line for line in path.read_text().splitlines() if line.strip()]


class FeatureGateTests(unittest.TestCase):
    def test_disabled_short_circuits(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(
                fakes_dir, list_response=json.dumps([_bead("el-1", "worker", _iso_now_offset(60))])
            )
            spy_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root, enabled=False)

            self.assertEqual(result.returncode, 0)
            self.assertFalse(
                (fakes_dir / "gc-argv.log").exists(),
                "disabled gate must not invoke gc",
            )


class RetryDecisionTests(unittest.TestCase):
    def test_no_exhausted_beads_no_action(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response="[]")
            spy_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            update_calls = [c for c in _bd_calls(fakes_dir) if "update" in c]
            self.assertEqual(len(update_calls), 0, "no exhausted beads → no bd update")

    def test_exhausted_within_backoff_window_no_action(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            # exhausted 5 min ago, backoff is 30 min → too recent.
            beads = json.dumps([_bead("el-1", "worker", _iso_now_offset(5))])
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=beads)
            spy_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root, backoff_minutes=30)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            update_calls = [c for c in _bd_calls(fakes_dir) if "update" in c]
            self.assertEqual(len(update_calls), 0, "within backoff → no resling")

    def test_exhausted_past_backoff_triggers_resling(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            beads = json.dumps([_bead("el-1", "worker", _iso_now_offset(60))])
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=beads)
            spy_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root, backoff_minutes=30)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            update_calls = [c for c in _bd_calls(fakes_dir) if "update el-1" in c]
            # Two updates: the main resling state write + the
            # sdlc_append_exit_history audit-trail write (pack #182).
            self.assertEqual(len(update_calls), 2, "expect main update + exit_history append")
            main = next(c for c in update_calls if "--status=open" in c)
            self.assertIn("worker.state=resuming", main)
            self.assertIn("worker.retry_count=1", main)
            self.assertIn("worker.last_resling_at=", main)
            history = next(c for c in update_calls if "exit_history" in c)
            self.assertIn("worker.exit_history=", history)
            self.assertIn("~watcher_resling~", history)

    def test_retry_count_at_cap_triggers_give_up(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            beads = json.dumps([_bead("el-1", "worker", _iso_now_offset(60), retry_count=3)])
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=beads)
            spy_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root, backoff_minutes=30, max_retries=3)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            update_calls = [c for c in _bd_calls(fakes_dir) if "update el-1" in c]
            # Two updates: the main give-up state write + the
            # sdlc_append_exit_history audit-trail write (pack #182).
            self.assertEqual(len(update_calls), 2, "expect main update + exit_history append")
            main = next(c for c in update_calls if "retry_count_exhausted" in c)
            self.assertIn("worker.gave_up_at=", main)
            self.assertIn("worker.gave_up_cause=", main)
            self.assertNotIn("resuming", main)
            history = next(c for c in update_calls if "exit_history" in c)
            self.assertIn("worker.exit_history=", history)
            self.assertIn("~watcher_gave_up~", history)

    def test_no_exhausted_at_timestamp_skipped(self) -> None:
        """Beads exhausted before the v2.26.0 wrapper change have no timestamp;
        watcher leaves them alone rather than re-slinging without context."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            beads = json.dumps([_bead("el-1", "worker", exhausted_at=None)])
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=beads)
            spy_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            update_calls = [c for c in _bd_calls(fakes_dir) if "update" in c]
            self.assertEqual(len(update_calls), 0, "no exhausted_at → no action (legacy bead)")


class MultiBeadTests(unittest.TestCase):
    def test_mix_of_resling_and_give_up(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            beads = json.dumps(
                [
                    _bead("el-resling", "worker", _iso_now_offset(60), retry_count=1),
                    _bead("el-giveup", "tester", _iso_now_offset(60), retry_count=3),
                    _bead("el-too-recent", "reviewer", _iso_now_offset(5)),
                ]
            )
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=beads)
            spy_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root, backoff_minutes=30, max_retries=3)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            all_updates = [c for c in _bd_calls(fakes_dir) if "update" in c]
            resling = [c for c in all_updates if "el-resling" in c]
            giveup = [c for c in all_updates if "el-giveup" in c]
            recent = [c for c in all_updates if "el-too-recent" in c]
            # Two updates per acted-on bead post-pack-#182: main state write
            # + exit_history audit-trail append.
            self.assertEqual(len(resling), 2, "el-resling: main + history")
            self.assertEqual(len(giveup), 2, "el-giveup: main + history")
            self.assertEqual(len(recent), 0, "el-too-recent should be left alone")
            resling_main = next(c for c in resling if "--status=open" in c)
            giveup_main = next(c for c in giveup if "retry_count_exhausted" in c)
            self.assertIn("worker.retry_count=2", resling_main)
            self.assertIn("tester.state=retry_count_exhausted", giveup_main)
            self.assertIn("tester.gave_up_cause=", giveup_main)


if __name__ == "__main__":
    unittest.main()
