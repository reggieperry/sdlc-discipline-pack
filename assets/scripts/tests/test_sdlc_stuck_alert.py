"""Tests for sdlc-stuck-alert.sh (pack #212).

The stuck-alert watcher emails the operator when the pipeline is stranded
awaiting a human — two triggers:

  1. bounce-exhausted PR: a bead at status=blocked + refresh_status=conflict
     (the finalizer's at-cap branch — rebase-bounce loop gave up). Re-keyed
     from status=escalated in issue #243 (bd rejected escalated atomically;
     the park lands as blocked).
  2. blocked-for-decision bead: status=blocked + a human_decision_reason
     (a worker escalated a spec/architecture call only the operator can make).

Each fires at most once per bead — the watcher stamps a dedup marker
(stuck_alerted_at / blocked_alerted_at) after including a bead in the
digest, so a later tick skips it. One email per rig per tick names every
freshly-stranded bead; the digest body carries the actionable detail
(bead id, story, the human_decision_reason or the collision files).

Black-box subprocess tests with recording fake binaries for gc, bd, and
sdlc-notify.sh on the test's PATH (the pack convention; see
test_sdlc_exhausted_bead_retry.py). The local notify spy here records
both argv (subject) and stdin (body) so the digest content is assertable.

stdlib-only (unittest + tempfile + subprocess). Run with::

    python3 -m unittest assets.scripts.tests.test_sdlc_stuck_alert -v
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _spies import spy_bd_list, spy_gc_rig_list, write_executable

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-stuck-alert.sh"
assert SCRIPT_PATH.exists(), f"sdlc-stuck-alert.sh not found at {SCRIPT_PATH}"


def _setup_rig(tmp: Path, rig_name: str = "test-rig") -> tuple[Path, Path, Path]:
    city_root = tmp / "city"
    rig_root = city_root / rig_name
    fakes_dir = tmp / "fakes"
    rig_root.mkdir(parents=True)
    fakes_dir.mkdir(parents=True)
    (city_root / "city.toml").write_text("[city]\n")
    return city_root, rig_root, fakes_dir


def _rig_list_json(rig_name: str, rig_root: Path) -> str:
    return json.dumps(
        {"rigs": [{"name": rig_name, "path": str(rig_root), "hq": False, "suspended": False}]}
    )


def _spy_notify_recording(fakes_dir: Path) -> Path:
    """Fake sdlc-notify.sh under the fake-pack layout that records BOTH
    argv (to notify-argv.log) and stdin/body (to notify-body.log).

    The shared spy_notify records only argv; the stuck-alert digest puts
    the bead detail in the body (stdin), so this test needs the body too.
    """
    pack_assets = fakes_dir / "fake-pack" / "assets" / "scripts"
    pack_assets.mkdir(parents=True, exist_ok=True)
    path = pack_assets / "sdlc-notify.sh"
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{fakes_dir}/notify-argv.log"\n'
        f'cat >> "{fakes_dir}/notify-body.log"\n'
        "exit 0\n"
    )
    write_executable(path, body)
    return path


def _bounce_conflict_bead(
    bead_id: str,
    files: str = "tests/unit/test_slop_analysis.py",
    alerted: bool = False,
) -> dict:
    """Trigger 1: bounce-exhausted (finalizer at-cap branch).

    Issue #243: the finalizer's at-cap park now lands as `status=blocked`
    (bd rejected the old `status=escalated` atomically), carrying
    `refresh_status=conflict` AND the human-decision park markers. The
    detector keys the bounce trigger on `status=blocked + refresh_status=
    conflict` and must check it BEFORE the generic blocked-for-decision
    trigger, so a bounce bead is categorized as bounce-exhausted (remediation:
    rebase + merge the collision files) rather than blocked-for-decision.
    """
    meta = {
        "refresh_status": "conflict",
        "merge_failure_count": "3",
        "merge_failure_files": files,
        "merge_failure_at": "2026-05-31T12:00:00Z",
        "requires_human_decision": "true",
        "human_decision_reason": "exhausted 3 rebase attempts; conflicts in: " + files,
        "gc.routed_to": "",
    }
    if alerted:
        meta["stuck_alerted_at"] = "2026-05-31T12:05:00Z"
    return {"id": bead_id, "status": "blocked", "metadata": meta}


def _blocked_bead(
    bead_id: str,
    reason: str = "re-root surfaces a real scanner_agent->indicators.elder violation; architecture call",
    alerted: bool = False,
) -> dict:
    """Trigger 2: blocked-for-decision (worker escalation)."""
    meta = {
        "human_decision_reason": reason,
        "worker.blocked_at": "2026-05-31T06:30:00Z",
        "story_file": "stories/EL-192-import-linter-non-vacuous.md",
    }
    if alerted:
        meta["blocked_alerted_at"] = "2026-05-31T06:35:00Z"
    return {"id": bead_id, "status": "blocked", "metadata": meta}


def _running_bead(bead_id: str) -> dict:
    """A healthy in-flight bead — must NOT trip either trigger."""
    return {
        "id": bead_id,
        "status": "in_progress",
        "metadata": {"gc.routed_to": "test-rig/sdlc-discipline.reviewer"},
    }


def _invoke(fakes_dir: Path, city_root: Path, enabled: bool = True) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{fakes_dir}:{os.environ['PATH']}",
        "GC_CITY_ROOT": str(city_root),
        "PACK_DIR": str(fakes_dir / "fake-pack"),
        "SDLC_STUCK_ALERT_ENABLED": "true" if enabled else "false",
        "SDLC_NOTIFY_RECIPIENT": "ops@example.com",
    }
    return subprocess.run([str(SCRIPT_PATH)], env=env, capture_output=True, text=True, timeout=30)


def _bd_calls(fakes_dir: Path) -> list[str]:
    path = fakes_dir / "bd-argv.log"
    return [l for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def _notify_subjects(fakes_dir: Path) -> list[str]:
    path = fakes_dir / "notify-argv.log"
    return [l for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def _notify_body(fakes_dir: Path) -> str:
    path = fakes_dir / "notify-body.log"
    return path.read_text() if path.exists() else ""


class FeatureGateTests(unittest.TestCase):
    def test_disabled_short_circuits(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=json.dumps([_blocked_bead("el-1")]))
            _spy_notify_recording(fakes_dir)

            result = _invoke(fakes_dir, city_root, enabled=False)

            self.assertEqual(result.returncode, 0)
            self.assertFalse(
                (fakes_dir / "notify-argv.log").exists(),
                "disabled gate must not send any notification",
            )


class NoStrandedBeadsTests(unittest.TestCase):
    def test_only_healthy_beads_no_notify(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=json.dumps([_running_bead("el-ok")]))
            _spy_notify_recording(fakes_dir)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertEqual(_notify_subjects(fakes_dir), [], "healthy bead → no alert")


class BlockedForDecisionTests(unittest.TestCase):
    def test_blocked_bead_alerts_and_stamps(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=json.dumps([_blocked_bead("el-blk")]))
            _spy_notify_recording(fakes_dir)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertEqual(len(_notify_subjects(fakes_dir)), 1, "exactly one digest email")
            body = _notify_body(fakes_dir)
            self.assertIn("el-blk", body, "digest body must name the blocked bead")
            self.assertIn("architecture call", body, "digest must carry the human_decision_reason")
            # Dedup stamp written so a later tick skips it.
            stamps = [
                c for c in _bd_calls(fakes_dir) if "el-blk" in c and "blocked_alerted_at" in c
            ]
            self.assertEqual(len(stamps), 1, "must stamp blocked_alerted_at exactly once")

    def test_already_alerted_blocked_is_deduped(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(
                fakes_dir, list_response=json.dumps([_blocked_bead("el-blk", alerted=True)])
            )
            _spy_notify_recording(fakes_dir)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertEqual(
                _notify_subjects(fakes_dir), [], "already-stamped blocked bead → no re-alert"
            )


class BounceExhaustedTests(unittest.TestCase):
    def test_blocked_conflict_alerts_and_stamps(self) -> None:
        """Issue #243: the re-keyed bounce trigger fires on `status=blocked +
        refresh_status=conflict` (the shape bd actually accepts) and reports it
        as bounce-exhausted, not blocked-for-decision."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(
                fakes_dir,
                list_response=json.dumps([_bounce_conflict_bead("el-cf", files="a/b.py")]),
            )
            _spy_notify_recording(fakes_dir)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertEqual(len(_notify_subjects(fakes_dir)), 1, "exactly one digest email")
            body = _notify_body(fakes_dir)
            self.assertIn("el-cf", body, "digest body must name the bounce-exhausted bead")
            self.assertIn("a/b.py", body, "digest must carry the collision file(s)")
            self.assertIn(
                "bounce-exhausted",
                body,
                "a conflict bead must be categorized as bounce-exhausted, not blocked-for-decision",
            )
            # Deduped under the bounce stamp, not the blocked-for-decision stamp.
            stamps = [c for c in _bd_calls(fakes_dir) if "el-cf" in c and "stuck_alerted_at" in c]
            self.assertEqual(len(stamps), 1, "must stamp stuck_alerted_at exactly once")

    def test_blocked_without_conflict_is_not_bounce(self) -> None:
        """A blocked bead WITHOUT refresh_status=conflict and WITHOUT a
        human_decision_reason is neither trigger — it is bare/crash-residue
        blocked, which the alerter deliberately does not chase."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            bead = {"id": "el-bare", "status": "blocked", "metadata": {"gc.routed_to": "x"}}
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=json.dumps([bead]))
            _spy_notify_recording(fakes_dir)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertEqual(
                _notify_subjects(fakes_dir), [], "bare blocked (no markers) → no alert"
            )

    def test_conflict_bead_categorized_as_bounce_not_blocked(self) -> None:
        """Ordering guard: the finalizer at-cap park carries BOTH
        refresh_status=conflict AND human_decision_reason. The bounce trigger
        (more specific) must be evaluated first so the bead is reported as
        bounce-exhausted with the collision-file remediation, never folded into
        the generic blocked-for-decision category."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(
                fakes_dir,
                list_response=json.dumps([_bounce_conflict_bead("el-cf", files="x/y.py")]),
            )
            _spy_notify_recording(fakes_dir)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            body = _notify_body(fakes_dir)
            # The bead must be listed under the bounce-exhausted category line,
            # never under a [blocked-for-decision] entry. (The digest footer
            # always names "blocked-for-decision" in its remediation guidance,
            # so assert on the bead's category MARKER, not the raw body.)
            self.assertIn("[bounce-exhausted] el-cf", body)
            self.assertIn("x/y.py", body, "bounce remediation must name the collision files")
            self.assertNotIn(
                "[blocked-for-decision] el-cf",
                body,
                "a conflict bead must not also surface as blocked-for-decision",
            )
            # Deduped via the bounce stamp, not the blocked stamp.
            self.assertTrue(
                any("stuck_alerted_at" in c for c in _bd_calls(fakes_dir)),
                "bounce bead must dedup via stuck_alerted_at",
            )
            self.assertFalse(
                any("blocked_alerted_at" in c for c in _bd_calls(fakes_dir)),
                "a bounce bead must NOT be stamped with blocked_alerted_at",
            )

    def test_already_alerted_bounce_is_deduped(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(
                fakes_dir,
                list_response=json.dumps([_bounce_conflict_bead("el-cf", alerted=True)]),
            )
            _spy_notify_recording(fakes_dir)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertEqual(
                _notify_subjects(fakes_dir), [], "already-stamped bounce bead → no re-alert"
            )


class CombinedDigestTests(unittest.TestCase):
    def test_both_triggers_one_digest_names_both(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            beads = [
                _blocked_bead("el-blk"),
                _bounce_conflict_bead("el-cf"),
                _running_bead("el-ok"),
            ]
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=json.dumps(beads))
            _spy_notify_recording(fakes_dir)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertEqual(
                len(_notify_subjects(fakes_dir)), 1, "one digest email covering both triggers"
            )
            body = _notify_body(fakes_dir)
            self.assertIn("el-blk", body)
            self.assertIn("el-cf", body)
            self.assertNotIn("el-ok", body, "healthy bead must not appear in the digest")


class SelfTestCanaryTests(unittest.TestCase):
    """The --self-test canary runs the REAL detector against synthetic
    stranded beads, so a blind detector fails loud rather than silently
    reporting all-clear (pack #212's core requirement)."""

    def _invoke_selftest(
        self, fakes_dir: Path, enabled: bool = True
    ) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PATH": f"{fakes_dir}:{os.environ['PATH']}",
            "PACK_DIR": str(fakes_dir / "fake-pack"),
            "SDLC_STUCK_ALERT_ENABLED": "true" if enabled else "false",
            "SDLC_NOTIFY_RECIPIENT": "ops@example.com",
        }
        return subprocess.run(
            [str(SCRIPT_PATH), "--self-test"], env=env, capture_output=True, text=True, timeout=30
        )

    def test_self_test_passes_when_detector_healthy(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _, _, fakes_dir = _setup_rig(tmp)
            _spy_notify_recording(fakes_dir)

            result = self._invoke_selftest(fakes_dir)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertIn("OK", result.stdout)
            self.assertFalse(
                (fakes_dir / "notify-argv.log").exists(),
                "a healthy self-test must not notify",
            )

    def test_self_test_respects_disabled_gate(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _, _, fakes_dir = _setup_rig(tmp)
            _spy_notify_recording(fakes_dir)

            result = self._invoke_selftest(fakes_dir, enabled=False)

            self.assertEqual(result.returncode, 0)
            self.assertNotIn("OK", result.stdout, "disabled gate exits before the canary runs")


if __name__ == "__main__":
    unittest.main()
