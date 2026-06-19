"""Pricing-table coverage for sdlc-cost-helper.py.

A chain phase whose model id is absent from PRICING_PER_MTOK_USD computes
cost_usd = 0 (PRICING_PER_MTOK_USD.get(model) -> None -> skipped). That is the
2026-06-19 bug: v2.42.0 pinned every Opus phase to ``claude-opus-4-8``, which
was missing from the table, so planner/worker/reviewer recorded $0 while the
Sonnet phases (tester/documenter/finalizer) recorded real cost. This test pins
the currently-pinned models so a future model bump that forgets the table fails
loud instead of silently zeroing the expensive phases.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "sdlc-cost-helper.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("sdlc_cost_helper", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PricingTableTests(unittest.TestCase):
    # Models the SDLC chain currently pins via agents/*/agent.toml option_defaults.
    PINNED_MODELS = ("claude-opus-4-8", "claude-sonnet-4-6")

    def test_currently_pinned_models_are_priced(self) -> None:
        pricing = _load_helper().PRICING_PER_MTOK_USD
        for model in self.PINNED_MODELS:
            self.assertIn(
                model,
                pricing,
                msg=f"{model} missing from PRICING_PER_MTOK_USD -> its phases record $0",
            )
            entry = pricing[model]
            self.assertGreater(entry["input"], 0, msg=f"{model} input price must be > 0")
            self.assertGreater(entry["output"], 0, msg=f"{model} output price must be > 0")

    def test_opus_4_8_priced_like_the_opus_line(self) -> None:
        pricing = _load_helper().PRICING_PER_MTOK_USD
        # Opus 4.x pricing has been stable; the new entry should match its siblings.
        self.assertEqual(pricing["claude-opus-4-8"], pricing["claude-opus-4-7"])


if __name__ == "__main__":
    unittest.main()
