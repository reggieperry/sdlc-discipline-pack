"""Tests for sdlc-order-stall-detector.py (pack #44 sub-story 5)."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

MODULE_PATH = Path(__file__).resolve().parent.parent / "sdlc-order-stall-detector.py"
assert MODULE_PATH.exists(), f"sdlc-order-stall-detector.py not found at {MODULE_PATH}"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("sdlc_order_stall_detector", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


osd = _load_module()


_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


class DurationParseTests(unittest.TestCase):
    """`gc order list` reports intervals in Go-duration strings."""

    def test_seconds_minutes_hours(self) -> None:
        self.assertEqual(osd.parse_duration("30s"), 30)
        self.assertEqual(osd.parse_duration("5m"), 300)
        self.assertEqual(osd.parse_duration("1h"), 3600)
        self.assertEqual(osd.parse_duration("24h"), 24 * 3600)

    def test_unknown_shape_returns_none(self) -> None:
        """A cron-schedule string in the same column slot returns None.

        The parser treats `None` as "skip this row" so cron-trigger
        orders sharing the same column don't accidentally enter the
        cooldown-checking path.
        """
        self.assertIsNone(osd.parse_duration("0 */4 * * *"))
        self.assertIsNone(osd.parse_duration(""))
        self.assertIsNone(osd.parse_duration("never"))


class OrderListParseTests(unittest.TestCase):
    """The tabular `gc order list` output is fragile; pin the parser shape."""

    def test_cooldown_rows_extracted(self) -> None:
        output = (
            "NAME             TYPE     TRIGGER    INTERVAL/SCHED  RIG     TARGET\n"
            "sdlc-cost-rollup exec     event      -               -       -\n"
            "rebase-watcher   exec     cooldown   5m              elder   -\n"
            "dolt-health      exec     cooldown   30s             -       -\n"
            "mol-dog-stale    formula  cron       0 */4 * * *     -       dog\n"
        )
        out = osd.parse_order_list(output)
        names = sorted(o.name for o in out)
        self.assertEqual(
            names,
            ["dolt-health", "rebase-watcher"],
            f"only cooldown rows should parse; got {names}",
        )
        rebase = next(o for o in out if o.name == "rebase-watcher")
        self.assertEqual(rebase.interval_seconds, 300)
        self.assertEqual(rebase.rig, "elder")


class OrderHistoryParseTests(unittest.TestCase):
    """`gc order history` returns rows sorted newest-first; take the first."""

    def test_latest_executed_returned(self) -> None:
        output = (
            "ORDER          RIG     BEAD     EXECUTED\n"
            "rebase-watcher elder   el-h7    2026-05-17T06:05:38Z\n"
            "rebase-watcher elder   el-b3    2026-05-17T05:50:00Z\n"
        )
        latest = osd.parse_order_history_latest(output)
        self.assertEqual(latest, datetime(2026, 5, 17, 6, 5, 38, tzinfo=UTC))

    def test_no_rows_returns_none(self) -> None:
        """Fresh order with no fire history skipped to avoid noisy alerts."""
        output = "ORDER          RIG     BEAD     EXECUTED\n"
        self.assertIsNone(osd.parse_order_history_latest(output))


class FindStallsTests(unittest.TestCase):
    """Core detection — last-fire-elapsed > interval × multiplier."""

    def setUp(self) -> None:
        self.orders = [
            osd.OrderInfo(name="rebase-watcher", interval_seconds=300, rig="elder"),  # 5m
            osd.OrderInfo(name="dolt-health", interval_seconds=30, rig="-"),  # 30s
        ]

    def test_recent_fire_does_not_alert(self) -> None:
        last_fires = {
            "rebase-watcher": _NOW - timedelta(minutes=4),  # under 5m × 2 = 10m
        }
        out = osd.find_stalls(self.orders, last_fires, _NOW, multiplier=2, throttled=set())
        self.assertEqual(out, [])

    def test_overdue_fire_alerts(self) -> None:
        last_fires = {
            "rebase-watcher": _NOW - timedelta(minutes=15),  # past 10m bound
        }
        out = osd.find_stalls(self.orders, last_fires, _NOW, multiplier=2, throttled=set())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].order_name, "rebase-watcher")
        self.assertEqual(out[0].expected_seconds, 600)
        self.assertEqual(out[0].elapsed_seconds, 900)

    def test_order_never_fired_skipped(self) -> None:
        """An order with no history (fresh install) is skipped, not alerted on.

        Otherwise every fresh rig would page the operator at every cron
        tick until the first real fire happened.
        """
        out = osd.find_stalls(self.orders, last_fires={}, now=_NOW, multiplier=2, throttled=set())
        self.assertEqual(out, [])

    def test_throttled_order_skipped(self) -> None:
        last_fires = {"rebase-watcher": _NOW - timedelta(minutes=30)}
        out = osd.find_stalls(
            self.orders, last_fires, _NOW, multiplier=2, throttled={"rebase-watcher"}
        )
        self.assertEqual(out, [], "throttled order should be skipped")


class StateFileTests(unittest.TestCase):
    """Throttle state file round-trips and handles corruption gracefully."""

    def test_load_missing_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "nonexistent.json"
            self.assertEqual(osd.load_state(path), {})

    def test_load_corrupt_returns_empty(self) -> None:
        """A corrupt state file should not crash the order; it's reset on next save.

        Operationally, this happens if a write was interrupted by SIGTERM
        on the supervisor. Failure-mode is "loud retry," not "block the
        whole order from running."
        """
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{not valid json")
            self.assertEqual(osd.load_state(path), {})

    def test_save_creates_parent_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "subdir" / "state.json"
            osd.save_state(path, {"a": "b"})
            self.assertTrue(path.exists())
            self.assertEqual(json.loads(path.read_text()), {"a": "b"})

    def test_throttled_orders_window(self) -> None:
        """An entry older than the window is no longer throttled."""
        state = {
            "fresh": (_NOW - timedelta(hours=1)).isoformat(),
            "stale": (_NOW - timedelta(hours=10)).isoformat(),
        }
        out = osd.throttled_orders(state, _NOW, timedelta(hours=4))
        self.assertEqual(
            out,
            {"fresh"},
            f"`fresh` within window should be throttled; `stale` past window "
            f"should re-alert; got {out}",
        )


class RenderTests(unittest.TestCase):
    """Subject + body pin the operator-facing format."""

    def test_subject_includes_order_stall_warning_tag(self) -> None:
        alert = osd.OrderStallAlert(
            order_name="rebase-watcher",
            rig="elder",
            elapsed_seconds=540 * 60,  # 9 hours — the motivating-case scenario
            expected_seconds=600,
            last_executed_iso="2026-05-17T03:00:00+00:00",
        )
        subject, body = osd.render_email_body(alert)
        self.assertIn(
            "[order-stall-warning]",
            subject,
            "subject should carry the order-stall tag distinct from bead-phase stalls",
        )
        self.assertIn("[elder]", subject, "rig prefix lets operator filter")
        self.assertIn("rebase-watcher", subject)
        self.assertIn("gc order history rebase-watcher", body, "body should suggest diagnostics")


if __name__ == "__main__":
    unittest.main()
