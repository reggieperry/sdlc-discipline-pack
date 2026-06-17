"""No loaded-discipline surface may reference a rule the craft/go/python migration removed or renamed.

The 2026-06-17 taxonomy migration (docs/rule-taxonomy-craft-go-python.md) renamed
the flat rule set to craft-*/go-*/python-*. Loading is glob-by-frontmatter so a
dangling reference does not crash — it silently sends a reader (the reviewer
agent, a formula, a sibling rule) to a file that no longer exists. This test is
the rail that keeps those references in lockstep with the rename.

Scope is the LOADED surface only — the rules, the long-form guides, the agent
prompts, and the formulas. docs/ is intentionally excluded: the migration ADR
and the README version history name the old rules on purpose, as the record.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_PACK_ROOT = Path(__file__).resolve().parents[3]

# Old rule basenames the migration removed or renamed. decoupling / writing-style /
# architecture-config / stories survive and are deliberately absent here.
_REMOVED = [
    "ddd",
    "modularity",
    "code-structure",
    "refactoring",
    "tdd",
    "testing",
    "python",
    "concurrency",
    "llm-app-patterns",
    "security",
    "xunit-patterns",
]

# Match `<old>.md` only when NOT part of a longer rule name — the negative
# lookbehind on [-\w] excludes the new prefixed names (craft-tdd.md, go-security.md,
# python-concurrency.md) and the rig overlay (security-elder.md never ends "security.md").
_DANGLING = re.compile(r"(?<![-\w])(" + "|".join(re.escape(n) for n in _REMOVED) + r")\.md")

_SCANNED_DIRS = [
    "overlay/per-provider/claude/.claude/rules",
    "overlay/per-provider/claude/.claude/sdlc-discipline/guides",
    "agents",
    "formulas",
]


def _scan_files() -> list[Path]:
    out: list[Path] = []
    for rel in _SCANNED_DIRS:
        base = _PACK_ROOT / rel
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in (".md", ".toml"):
                out.append(path)
    return out


class RuleTaxonomyReferenceTest(unittest.TestCase):
    def test_no_dangling_old_rule_reference(self) -> None:
        offenders: list[str] = []
        for path in _scan_files():
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                for m in _DANGLING.finditer(line):
                    # A rig overlay reference (.claude/rules/project/<name>.md) is not a
                    # pack-rule dangle — those rig files are not renamed by this migration.
                    if line[max(0, m.start() - 8) : m.start()] == "project/":
                        continue
                    rel = path.relative_to(_PACK_ROOT)
                    offenders.append(f"{rel}:{lineno}: {m.group(0)} -> {line.strip()[:100]}")
        self.assertEqual(
            offenders,
            [],
            "loaded-discipline files reference renamed/removed rules; remap to the "
            "craft/go/python names:\n" + "\n".join(offenders),
        )

    def test_scan_actually_covers_files(self) -> None:
        # guard against a silent empty scan (wrong root, moved dirs)
        self.assertGreater(len(_scan_files()), 20)


if __name__ == "__main__":
    unittest.main()
