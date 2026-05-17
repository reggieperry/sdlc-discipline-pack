"""Tech-debt classifier (pack #32 post-finalizer hook).

Classifies an auto-filed tech-debt issue as `autofix-safe`, `needs-human`,
or `defer-to-llm`. Used by the planned post-finalizer order
`sdlc-classify-tech-debt` to label issues so the downstream auto-process
build can pick up the safe ones and route the rest to operator triage.

Scope of this module: deterministic rules only. The LLM fallback for
`defer-to-llm` verdicts and the orchestrator that combines them ship in
follow-up sub-stories (cycles 8-12 in the planned cycle list).

Rules — ALL must hold for `autofix-safe`:

- `target_path` does NOT match any pattern in `sensitive_files` (fnmatch)
- `severity` is `low` or `medium` (not `high`, not unset)
- `target_lines` span (M-N+1 for `"N-M"`, 1 for `"N"`) is ≤ 10
- `category` is in the safe set (type-hygiene, docstring-vs-code,
  missing-test, scope-drift, dead-code-removal)
- `category` is NOT in the risky set (stale-state, security, concurrency,
  protocol-change, cross-module-coupling) — risky categories force
  `needs-human` regardless of the safe-set check

When `category` is neither safe nor risky, the rule defers to LLM
classification (`defer-to-llm`). Any other criterion failing returns
`needs-human` (the conservative default).
"""

from __future__ import annotations

import fnmatch
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    """Three possible classification outcomes for a tech-debt issue."""

    AUTOFIX_SAFE = "autofix-safe"
    NEEDS_HUMAN = "needs-human"
    DEFER_TO_LLM = "defer-to-llm"


_AUTOFIX_SAFE_SEVERITIES: frozenset[str] = frozenset({"low", "medium"})
_MAX_AUTOFIX_LINE_SPAN: int = 10
_SAFE_CATEGORIES: frozenset[str] = frozenset(
    {
        "type-hygiene",
        "docstring-vs-code",
        "missing-test",
        "scope-drift",
        "dead-code-removal",
    }
)
_RISKY_CATEGORIES: frozenset[str] = frozenset(
    {
        "stale-state",
        "security",
        "concurrency",
        "protocol-change",
        "cross-module-coupling",
    }
)


def _line_span(target_lines: str) -> int:
    """Compute the inclusive line count from a `target_lines` field value.

    Accepts both `"N"` (single line → 1) and `"N-M"` (range → M-N+1).
    Returns a sentinel value larger than any reasonable threshold for
    malformed inputs so the calling rule's bounded check rejects them.
    """
    try:
        if "-" in target_lines:
            start_str, end_str = target_lines.split("-", 1)
            start, end = int(start_str.strip()), int(end_str.strip())
            return max(end - start + 1, 1)
        return 1 if int(target_lines.strip()) else 1
    except (ValueError, AttributeError):
        return _MAX_AUTOFIX_LINE_SPAN + 1


def classify_by_rules(item: dict[str, Any], sensitive_files: list[str]) -> Verdict:
    """Deterministic rules-only classifier.

    Returns one of the three verdicts. Tests inject the sensitive_files
    list so the module stays rig-agnostic.
    """
    target_path = item.get("target_path", "")
    if any(fnmatch.fnmatch(target_path, pattern) for pattern in sensitive_files):
        return Verdict.NEEDS_HUMAN

    severity = item.get("severity", "")
    if severity not in _AUTOFIX_SAFE_SEVERITIES:
        return Verdict.NEEDS_HUMAN

    if _line_span(item.get("target_lines", "")) > _MAX_AUTOFIX_LINE_SPAN:
        return Verdict.NEEDS_HUMAN

    category = item.get("category", "")
    if category in _RISKY_CATEGORIES:
        return Verdict.NEEDS_HUMAN
    if category not in _SAFE_CATEGORIES:
        return Verdict.DEFER_TO_LLM

    return Verdict.AUTOFIX_SAFE
