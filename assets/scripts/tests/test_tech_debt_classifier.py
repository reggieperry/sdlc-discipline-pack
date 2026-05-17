"""Tests for the tech-debt classifier (pack #32 post-finalizer hook).

Pure-Python unit tests for the classifier's rules module, LLM fallback,
and orchestrator. The rules module is deterministic and stdlib-only;
the LLM fallback uses an injected runner so tests can stub responses.

stdlib-only (`unittest` + importlib). Matches the pack's existing test
convention (`test_tech_debt.py`, `test_claude_retry.py`).

Run with:

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Any

MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "overlay"
    / "per-provider"
    / "claude"
    / ".claude"
    / "sdlc-discipline"
    / "tech_debt_classifier.py"
)
assert MODULE_PATH.exists(), f"tech_debt_classifier.py not found at {MODULE_PATH}"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("tech_debt_classifier", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cdc = _load_module()


def _baseline_item(**overrides: Any) -> dict[str, Any]:
    """A trailer item that passes ALL autofix-safe criteria by default.

    Tests override specific fields to flip individual rules; this keeps
    each test focused on one rule rather than rebuilding the whole item
    every time.
    """
    item: dict[str, Any] = {
        "target_path": "core/audit.py",
        "target_lines": "60-62",
        "severity": "low",
        "category": "type-hygiene",
        "summary": "DecisionRecord exposes dict[str, Any]",
        "suggested_fix": "Introduce per-stage typed value objects",
    }
    item.update(overrides)
    return item


class RulesHappyPathTests(unittest.TestCase):
    """Cycle 1 — all criteria met → AUTOFIX_SAFE.

    The baseline trailer item is constructed to satisfy every rule:
    non-sensitive path, severity low, 3-line span (within 10),
    type-hygiene category (in the safe set). The classifier returns
    AUTOFIX_SAFE without escalating to the LLM fallback.
    """

    def test_baseline_item_with_no_sensitive_files_returns_autofix_safe(
        self,
    ) -> None:
        verdict = cdc.classify_by_rules(_baseline_item(), sensitive_files=[])
        self.assertEqual(
            verdict,
            cdc.Verdict.AUTOFIX_SAFE,
            f"baseline item with no sensitive_files list should be autofix-safe; got {verdict!r}",
        )


class RulesCategoryTests(unittest.TestCase):
    """Cycles 5 + 6 — category-based dispatch.

    Three branches:
    - safe set (type-hygiene, docstring-vs-code, missing-test,
      scope-drift, dead-code-removal) → continue evaluating
    - risky set (stale-state, security, concurrency, protocol-change,
      cross-module-coupling) → NEEDS_HUMAN immediately
    - neither → DEFER_TO_LLM (the classifier can't decide; ask the LLM)
    """

    def test_risky_category_returns_needs_human(self) -> None:
        """Cycle 5 — categories in the risky set force needs-human."""
        for risky in (
            "stale-state",
            "security",
            "concurrency",
            "protocol-change",
            "cross-module-coupling",
        ):
            with self.subTest(category=risky):
                verdict = cdc.classify_by_rules(_baseline_item(category=risky), sensitive_files=[])
                self.assertEqual(
                    verdict,
                    cdc.Verdict.NEEDS_HUMAN,
                    f"category={risky!r} should force needs-human; got {verdict!r}",
                )

    def test_unknown_category_returns_defer_to_llm(self) -> None:
        """Cycle 6 — categories outside both sets escalate to LLM."""
        verdict = cdc.classify_by_rules(
            _baseline_item(category="some-novel-category"), sensitive_files=[]
        )
        self.assertEqual(
            verdict,
            cdc.Verdict.DEFER_TO_LLM,
            f"unknown category should defer to LLM; got {verdict!r}",
        )

    def test_safe_categories_pass_through(self) -> None:
        """Regression: every safe-set category remains autofix-safe."""
        for safe in (
            "type-hygiene",
            "docstring-vs-code",
            "missing-test",
            "scope-drift",
            "dead-code-removal",
        ):
            with self.subTest(category=safe):
                verdict = cdc.classify_by_rules(_baseline_item(category=safe), sensitive_files=[])
                self.assertEqual(
                    verdict,
                    cdc.Verdict.AUTOFIX_SAFE,
                    f"category={safe!r} (in safe set) with all other "
                    f"criteria met should be autofix-safe; got {verdict!r}",
                )


class RulesLineSpanTests(unittest.TestCase):
    """Cycle 4 — target_lines span > 10 → NEEDS_HUMAN.

    Big changes — even mechanical refactors — deserve human eyes. The
    10-line threshold was chosen empirically: Extract Method moves
    typically span 5-15 lines; 10 captures most without inviting
    risk on larger restructurings.
    """

    def test_span_exactly_10_lines_remains_autofix_safe(self) -> None:
        """Boundary case — span = 10 should pass (inclusive)."""
        verdict = cdc.classify_by_rules(_baseline_item(target_lines="1-10"), sensitive_files=[])
        self.assertEqual(
            verdict,
            cdc.Verdict.AUTOFIX_SAFE,
            f"span of exactly 10 lines should be autofix-safe (boundary); got {verdict!r}",
        )

    def test_span_11_lines_returns_needs_human(self) -> None:
        """Just over the threshold."""
        verdict = cdc.classify_by_rules(_baseline_item(target_lines="1-11"), sensitive_files=[])
        self.assertEqual(
            verdict,
            cdc.Verdict.NEEDS_HUMAN,
            f"span of 11 lines should force needs-human; got {verdict!r}",
        )

    def test_large_span_returns_needs_human(self) -> None:
        verdict = cdc.classify_by_rules(_baseline_item(target_lines="100-500"), sensitive_files=[])
        self.assertEqual(
            verdict,
            cdc.Verdict.NEEDS_HUMAN,
            f"span of 401 lines should force needs-human; got {verdict!r}",
        )

    def test_single_line_target_remains_autofix_safe(self) -> None:
        verdict = cdc.classify_by_rules(_baseline_item(target_lines="135"), sensitive_files=[])
        self.assertEqual(
            verdict,
            cdc.Verdict.AUTOFIX_SAFE,
            f"single-line target (1 line) should be autofix-safe; got {verdict!r}",
        )

    def test_malformed_lines_returns_needs_human(self) -> None:
        verdict = cdc.classify_by_rules(_baseline_item(target_lines="invalid"), sensitive_files=[])
        self.assertEqual(
            verdict,
            cdc.Verdict.NEEDS_HUMAN,
            f"malformed target_lines should force needs-human; got {verdict!r}",
        )


class RulesSeverityTests(unittest.TestCase):
    """Cycle 3 — severity=high → NEEDS_HUMAN.

    Per the design conversation: severity in {low, medium} is acceptable
    for autofix; high requires human triage. Unknown/missing severity is
    conservatively treated as needs-human — the auto-filer always sets
    severity, so absence indicates a malformed item.
    """

    def test_severity_high_returns_needs_human(self) -> None:
        verdict = cdc.classify_by_rules(_baseline_item(severity="high"), sensitive_files=[])
        self.assertEqual(
            verdict,
            cdc.Verdict.NEEDS_HUMAN,
            f"severity=high should force needs-human; got {verdict!r}",
        )

    def test_severity_medium_remains_autofix_safe(self) -> None:
        """Regression: medium is explicitly allowed per the design."""
        verdict = cdc.classify_by_rules(_baseline_item(severity="medium"), sensitive_files=[])
        self.assertEqual(
            verdict,
            cdc.Verdict.AUTOFIX_SAFE,
            f"severity=medium should remain autofix-safe (other criteria all met); got {verdict!r}",
        )

    def test_severity_unknown_returns_needs_human(self) -> None:
        """Conservative default for malformed input."""
        verdict = cdc.classify_by_rules(_baseline_item(severity="critical"), sensitive_files=[])
        self.assertEqual(
            verdict,
            cdc.Verdict.NEEDS_HUMAN,
            f"unknown severity value should force needs-human; got {verdict!r}",
        )


class RulesSensitiveFilesTests(unittest.TestCase):
    """Cycle 2 — target_path matching sensitive_files entry → NEEDS_HUMAN.

    The sensitive_files list contains exact paths and glob patterns
    (e.g., `db/migrations/*.sql`). The classifier matches via fnmatch
    so both literal paths and patterns work without special-casing.
    """

    def test_exact_path_match_returns_needs_human(self) -> None:
        verdict = cdc.classify_by_rules(
            _baseline_item(target_path="agents/risk_agent.py"),
            sensitive_files=["agents/risk_agent.py", "core/trade.py"],
        )
        self.assertEqual(
            verdict,
            cdc.Verdict.NEEDS_HUMAN,
            f"target_path matching a sensitive_files entry exactly should "
            f"force needs-human; got {verdict!r}",
        )

    def test_glob_pattern_match_returns_needs_human(self) -> None:
        """A `db/migrations/*.sql` pattern matches any SQL file in that dir.

        Conservative posture: if the rig declared a glob, anything matching
        it routes to needs-human. The architectural-signals script uses the
        same fnmatch convention.
        """
        verdict = cdc.classify_by_rules(
            _baseline_item(target_path="db/migrations/0042_user.sql"),
            sensitive_files=["db/migrations/*.sql"],
        )
        self.assertEqual(
            verdict,
            cdc.Verdict.NEEDS_HUMAN,
            f"target_path matching a sensitive_files glob should force "
            f"needs-human; got {verdict!r}",
        )


if __name__ == "__main__":
    unittest.main()
