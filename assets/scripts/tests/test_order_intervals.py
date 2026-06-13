"""Guard: every `orders/*.toml` interval must be a Go-parseable duration.

gascity parses an order's `interval` with Go's `time.ParseDuration`, whose units
are ns/us/µs/ms/s/m/h — there is no "d", "w", or "y". An interval like "7d"
parses nowhere: the supervisor logs `invalid interval "7d"` on every start and
reload and silently drops the order (pack #234 — the sdlc-deep-reason-audit
order never scheduled for ~2 weeks because of exactly this). This test fails the
suite the moment any order reintroduces a calendar unit.
"""

import re
import unittest
from pathlib import Path

# A Go duration is one-or-more <number><unit> terms; d/w/y are deliberately absent.
_GO_DURATION = re.compile(r"^[+-]?(\d+(\.\d+)?(ns|us|µs|ms|s|m|h))+$")
_INTERVAL_LINE = re.compile(r'(?m)^\s*interval\s*=\s*"([^"]*)"')


def _orders_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "orders"


class OrderIntervalTests(unittest.TestCase):
    def test_every_order_interval_is_go_parseable(self) -> None:
        orders = sorted(_orders_dir().glob("*.toml"))
        self.assertTrue(orders, "no order .toml files found — wrong orders dir?")
        bad = [
            f"{toml.name}: interval={m.group(1)!r}"
            for toml in orders
            for m in _INTERVAL_LINE.finditer(toml.read_text())
            if not _GO_DURATION.match(m.group(1))
        ]
        self.assertEqual(
            bad, [], f"non-Go-parseable order intervals (use h/m/s, never d/w/y): {bad}"
        )


if __name__ == "__main__":
    unittest.main()
