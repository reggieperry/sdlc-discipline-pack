"""Guard: the plan format mandates a ## Anchors code-map section, and the
worker is told to start from it (issue #249).

The worker phase dominates chain wall-clock and ~95-98% of worker input is
re-exploring code the planner already navigated, because the plan format had
no slot to persist edit-site anchors (sampled Elder plans carried 6/0/0/2
file:line anchors). This test pins the fix: the `mol-sdlc-plan` plan step's
mandated structure includes a `## Anchors` section with a path:line | symbol
format, ordered after `## Sensitive files` and before `## Steps` (so the
self-audit's Sensitive-files awk, which stops at the next `## ` heading, is
unaffected), and `mol-sdlc-work` tells the worker to use it as its jump map.

stdlib-only (unittest + pathlib). Matches pack convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_plan_anchors_section -v
"""

from __future__ import annotations

import unittest
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[3]
PLAN = PACK_ROOT / "formulas" / "mol-sdlc-plan.toml"
WORK = PACK_ROOT / "formulas" / "mol-sdlc-work.toml"


class TestPlanAnchorsSection(unittest.TestCase):
    def test_plan_format_mandates_anchors_section(self) -> None:
        text = PLAN.read_text()
        self.assertIn(
            "## Anchors",
            text,
            "the plan format must mandate a ## Anchors code-map section (#249)",
        )

    def test_anchors_format_names_path_line_and_symbol(self) -> None:
        text = PLAN.read_text()
        self.assertIn(
            "<path>:<line>",
            text,
            "## Anchors must specify the <path>:<line> | <symbol> anchor format (#249)",
        )

    def test_anchors_ordered_after_sensitive_before_steps(self) -> None:
        text = PLAN.read_text()
        i_sens = text.find("## Sensitive files")
        i_anch = text.find("## Anchors")
        i_steps = text.find("## Steps")
        self.assertNotEqual(i_sens, -1, "## Sensitive files heading present")
        self.assertNotEqual(i_anch, -1, "## Anchors heading present")
        self.assertNotEqual(i_steps, -1, "## Steps heading present")
        self.assertLess(
            i_sens, i_anch, "## Anchors must follow ## Sensitive files (gate-awk safety)"
        )
        self.assertLess(i_anch, i_steps, "## Anchors must precede ## Steps")

    def test_worker_starts_from_anchors(self) -> None:
        text = WORK.read_text()
        self.assertIn(
            "Anchors",
            text,
            "the worker must be told to use the plan's ## Anchors as its jump map (#249)",
        )


if __name__ == "__main__":
    unittest.main()
