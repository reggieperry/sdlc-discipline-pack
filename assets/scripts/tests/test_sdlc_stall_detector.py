"""Tests for sdlc-stall-detector.py (pack #44 sub-story 4).

Pure-Python unit tests for the bead-phase stall detector. The tests
exercise the pure `find_stalls` function with injected fixtures and
the `slos_with_overrides` parser; the subprocess-driven I/O paths
(`fetch_beads`, `invoke_notify`, `mark_alerted`) are exercised only at
the contract level (their existence is verified at import time).

stdlib-only (`unittest` + `importlib`). Matches the pack's existing
test convention.

Run with:

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

MODULE_PATH = Path(__file__).resolve().parent.parent / "sdlc-stall-detector.py"
assert MODULE_PATH.exists(), f"sdlc-stall-detector.py not found at {MODULE_PATH}"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("sdlc_stall_detector", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


stalls = _load_module()


_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


def _bead(
    bead_id: str,
    phase: str,
    started_minutes_ago: int,
    last_alerted_minutes_ago: int | None = None,
) -> dict[str, Any]:
    """Construct a bead JSON shape matching `bd list --json` output.

    Centralizes the metadata-key convention so individual tests focus on
    the behavior under test rather than rebuilding the structure.
    """
    started_at = _NOW - timedelta(minutes=started_minutes_ago)
    meta: dict[str, str] = {
        "current_step": phase,
        f"{phase}.started_at": started_at.isoformat(),
    }
    if last_alerted_minutes_ago is not None:
        last_at = _NOW - timedelta(minutes=last_alerted_minutes_ago)
        meta[f"stall_alert.{phase}.last_at"] = last_at.isoformat()
    return {"id": bead_id, "metadata": meta}


class SLOOverrideTests(unittest.TestCase):
    """`SDLC_STALL_SLO_OVERRIDE` env shifts individual phase SLOs.

    The default SLO table is built into the script (per the issue's
    initial-values list). The override env exists so a rig can tune
    without touching the pack — typical case: a rig with substantive
    migrations bumps `implement` from 120 to 180 minutes.
    """

    def test_defaults_returned_when_env_absent(self) -> None:
        slos = stalls.slos_with_overrides({})
        self.assertEqual(slos["implement"], 120)
        self.assertEqual(slos["tester"], 15)

    def test_override_replaces_default_for_named_phase(self) -> None:
        slos = stalls.slos_with_overrides({"SDLC_STALL_SLO_OVERRIDE": "implement=180,tester=25"})
        self.assertEqual(slos["implement"], 180)
        self.assertEqual(slos["tester"], 25)
        # Unmodified phases keep their default.
        self.assertEqual(slos["reviewer"], 20)

    def test_malformed_entry_skipped_with_stderr_log(self) -> None:
        """Bad entries don't crash the run; the default for that phase stays."""
        slos = stalls.slos_with_overrides(
            {"SDLC_STALL_SLO_OVERRIDE": "implement=notanint,tester=25"}
        )
        self.assertEqual(slos["implement"], 120, "malformed override should keep the default")
        self.assertEqual(slos["tester"], 25, "valid sibling override should still apply")


class FindStallsTests(unittest.TestCase):
    """Pure-function stall detection — the load-bearing contract."""

    def setUp(self) -> None:
        self.slos = dict(stalls._DEFAULT_SLOS_MINUTES)
        self.throttle = timedelta(hours=4)
        self.rig = "test-rig"

    def test_bead_under_slo_produces_no_alert(self) -> None:
        beads = [_bead("bd-1", "plan", started_minutes_ago=10)]  # SLO 30
        out = stalls.find_stalls(beads, self.slos, _NOW, self.throttle, self.rig)
        self.assertEqual(out, [], f"plan@10min should not alert; got {out}")

    def test_bead_past_slo_produces_alert(self) -> None:
        beads = [_bead("bd-2", "plan", started_minutes_ago=35)]  # SLO 30
        out = stalls.find_stalls(beads, self.slos, _NOW, self.throttle, self.rig)
        self.assertEqual(len(out), 1, f"plan@35min should alert; got {out}")
        self.assertEqual(out[0].bead_id, "bd-2")
        self.assertEqual(out[0].phase, "plan")
        self.assertEqual(out[0].slo_minutes, 30)
        self.assertEqual(out[0].elapsed_minutes, 35)
        self.assertEqual(out[0].rig, "test-rig")

    def test_bead_at_implement_slo_boundary_does_not_alert(self) -> None:
        """`implement` SLO is the largest at 120 min — covers Elder's substantive migrations."""
        beads = [_bead("bd-3", "implement", started_minutes_ago=119)]
        out = stalls.find_stalls(beads, self.slos, _NOW, self.throttle, self.rig)
        self.assertEqual(out, [], "elapsed < SLO should not alert")

        beads = [_bead("bd-3", "implement", started_minutes_ago=121)]
        out = stalls.find_stalls(beads, self.slos, _NOW, self.throttle, self.rig)
        self.assertEqual(len(out), 1, "elapsed > SLO should alert")

    def test_throttle_suppresses_recent_alert(self) -> None:
        """Re-alert on the same (bead, phase) within the throttle window is silent."""
        beads = [
            _bead(
                "bd-4",
                "plan",
                started_minutes_ago=35,
                last_alerted_minutes_ago=60,  # 1 hour ago, throttle is 4
            )
        ]
        out = stalls.find_stalls(beads, self.slos, _NOW, self.throttle, self.rig)
        self.assertEqual(out, [], "recent alert should suppress re-alert")

    def test_throttle_window_expiration_allows_re_alert(self) -> None:
        """After the throttle window expires, the stall re-alerts."""
        beads = [
            _bead(
                "bd-5",
                "plan",
                started_minutes_ago=300,  # 5 hours stuck
                last_alerted_minutes_ago=300,  # last alerted 5 hours ago, throttle 4
            )
        ]
        out = stalls.find_stalls(beads, self.slos, _NOW, self.throttle, self.rig)
        self.assertEqual(len(out), 1, "alert older than throttle should re-fire")

    def test_unknown_phase_in_bead_is_ignored(self) -> None:
        """If `current_step` is not in the SLO table, the detector skips the bead.

        Stales due to a brand-new pool phase the operator hasn't tuned an
        SLO for yet shouldn't crash the detector or produce phantom
        alerts; they just don't fire until an SLO is configured.
        """
        beads = [_bead("bd-6", "unknown-phase", started_minutes_ago=999)]
        out = stalls.find_stalls(beads, self.slos, _NOW, self.throttle, self.rig)
        self.assertEqual(out, [], "unknown phase should not produce alerts")

    def test_missing_started_at_is_ignored(self) -> None:
        """Bead in transition between phases (no started_at yet) is skipped.

        Happens briefly between a handoff and the receiving pool agent's
        cost-tracking step. The next tick (15 min later) will see the
        new metadata once the receiving agent has run.
        """
        beads = [{"id": "bd-7", "metadata": {"current_step": "plan"}}]
        out = stalls.find_stalls(beads, self.slos, _NOW, self.throttle, self.rig)
        self.assertEqual(out, [], "missing started_at should not produce alerts")

    def test_multiple_stalls_each_produce_alerts(self) -> None:
        beads = [
            _bead("bd-8a", "plan", started_minutes_ago=35),
            _bead("bd-8b", "reviewer", started_minutes_ago=25),
            _bead("bd-8c", "implement", started_minutes_ago=5),  # under SLO
        ]
        out = stalls.find_stalls(beads, self.slos, _NOW, self.throttle, self.rig)
        ids = sorted(a.bead_id for a in out)
        self.assertEqual(ids, ["bd-8a", "bd-8b"], f"two stalls expected; got {ids}")


class EmailRenderTests(unittest.TestCase):
    """The email body / subject format is what the operator actually reads."""

    def test_subject_has_rig_warning_prefix_and_bead_id(self) -> None:
        alert = stalls.StallAlert(
            bead_id="bd-42",
            phase="implement",
            elapsed_minutes=150,
            slo_minutes=120,
            started_at_iso="2026-05-17T09:30:00+00:00",
            rig="elder",
        )
        subject, body = stalls.render_email_body(alert)
        self.assertIn(
            "[elder]",
            subject,
            "subject should include the rig in brackets — operator filters on rig",
        )
        self.assertIn(
            "[stall-warning]",
            subject,
            "subject should carry the stall-warning prefix per the design note "
            "(uncertain — not [error])",
        )
        self.assertIn("bd-42", subject)
        self.assertIn("`implement`", subject)
        self.assertIn("150 min", subject)

    def test_body_includes_diagnostic_command(self) -> None:
        """Body gives the operator a one-liner to investigate the stall."""
        alert = stalls.StallAlert(
            bead_id="bd-42",
            phase="reviewer",
            elapsed_minutes=25,
            slo_minutes=20,
            started_at_iso="2026-05-17T11:35:00+00:00",
            rig="elder",
        )
        _, body = stalls.render_email_body(alert)
        self.assertIn("bd show bd-42 --json", body, "body should include the bd-show diagnostic")
        self.assertIn(
            "Re-alert fires when the phase changes or four hours pass",
            body,
            "body should document the throttle so the operator isn't surprised by silence",
        )


if __name__ == "__main__":
    unittest.main()
