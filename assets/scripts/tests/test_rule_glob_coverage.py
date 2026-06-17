"""Every layer of the craft/go/python taxonomy must fire on the files it governs.

Rules load by their frontmatter `paths:` glob (a Claude Code native feature — the
harness matches the edited file against each rule's glob and injects the matching
rule). This test locks the glob contract so a future edit cannot silently narrow a
glob and stop a discipline from firing — the failure mode the 2026-06-17 migration
nearly shipped (craft-xunit globbed Python tests only, missing *_test.go).

It also asserts the reviewer prompt's explicit named-rule list resolves: the reviewer
reads a diff (it does not edit), so it loads rules by that named list, not by glob —
the list is the contract there.
"""

from __future__ import annotations

import glob
import re
import unittest
from pathlib import Path

_PACK = Path(__file__).resolve().parents[3]
_RULES = _PACK / "overlay/per-provider/claude/.claude/rules"
_GUIDES = _PACK / "overlay/per-provider/claude/.claude/sdlc-discipline/guides"
_REVIEWER = _PACK / "agents/reviewer/prompt.template.md"

# Language-neutral craft rules that must fire on any source file in any language.
_SOURCE_CRAFT = [
    "craft-abstraction",
    "craft-complexity",
    "craft-documentation",
    "craft-domain-modeling",
    "craft-refactoring",
    "craft-tdd",
]
# Per-language source rules that must fire on their whole language.
_GO_SOURCE = ["go-style", "go-errors", "go-types", "go-concurrency", "go-modules", "go-security"]
_PY_SOURCE = [
    "python-style",
    "python-types",
    "python-errors",
    "python-modules",
    "python-concurrency",
    "python-security",
    "python-llm",
]
# Deliberate, recorded asymmetry: go-llm is narrow (scoped to LLM-call files) while
# python-llm is broad (preserves the old llm-app-patterns.md scope). Not drift — if
# this ever needs revisiting, change it here on purpose.
_LLM_ASYMMETRY = "go-llm: **/*llm*.go,**/*schema*.go (narrow) vs python-llm: **/*.py (broad)"


def _paths(rule: str) -> list[str]:
    txt = (_RULES / f"{rule}.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---", txt, re.S)
    if not m:
        return []
    return re.findall(r'^\s*-\s*"?([^"\n]+?)"?\s*$', m.group(1), re.M)


class GlobCoverageTest(unittest.TestCase):
    def test_source_craft_fires_on_both_languages(self) -> None:
        for r in _SOURCE_CRAFT:
            p = set(_paths(r))
            self.assertIn("**/*.py", p, f"{r} must glob Python source (**/*.py)")
            self.assertIn("**/*.go", p, f"{r} must glob Go source (**/*.go)")

    def test_craft_xunit_fires_on_both_language_tests(self) -> None:
        p = set(_paths("craft-xunit"))
        self.assertIn("**/*_test.py", p, "craft-xunit must fire on Python tests")
        self.assertIn("**/*_test.go", p, "craft-xunit must fire on Go tests")

    def test_go_source_rules_glob_go(self) -> None:
        for r in _GO_SOURCE:
            self.assertIn("**/*.go", set(_paths(r)), f"{r} must glob **/*.go")
        self.assertIn(
            "**/*_test.go", set(_paths("go-testing")), "go-testing must glob **/*_test.go"
        )

    def test_python_source_rules_glob_py(self) -> None:
        for r in _PY_SOURCE:
            self.assertIn("**/*.py", set(_paths(r)), f"{r} must glob **/*.py")
        self.assertTrue(
            {"**/*_test.py", "**/test_*.py"} & set(_paths("python-testing")),
            "python-testing must glob Python test files",
        )

    def test_no_bare_top_level_tests_glob(self) -> None:
        # Nested test dirs (sub/tests/**) must be covered — use **/tests/**, not bare tests/**.
        for f in glob.glob(str(_RULES / "*.md")):
            stem = Path(f).stem
            self.assertNotIn(
                "tests/**",
                _paths(stem),
                f"{stem}: use '**/tests/**' (nested) not bare 'tests/**'",
            )

    def test_each_domain_has_both_language_rules(self) -> None:
        for domain in ["style", "errors", "types", "concurrency", "security", "testing"]:
            self.assertTrue(
                (_RULES / f"python-{domain}.md").exists(), f"missing python-{domain}.md"
            )
            self.assertTrue((_RULES / f"go-{domain}.md").exists(), f"missing go-{domain}.md")

    def test_reviewer_named_rules_resolve(self) -> None:
        # The reviewer loads rules by the names it lists (it reads a diff, not edits).
        # Every `<name>.md` it names must exist as a rule or guide.
        txt = _REVIEWER.read_text(encoding="utf-8")
        named = set(re.findall(r"`([a-z][a-z0-9-]+)\.md`", txt))
        missing = [
            n
            for n in named
            if not (_RULES / f"{n}.md").exists() and not (_GUIDES / f"{n}.md").exists()
        ]
        self.assertEqual(missing, [], f"reviewer prompt names nonexistent rules: {missing}")


if __name__ == "__main__":
    unittest.main()
