"""Tests for stories.py build_graph_plan — focused on metadata propagation.

v2.30 added `metadata.source_audit_doc` propagation from spec frontmatter to
bead metadata at `stories.py:445`. The v2.30 pre-tag deep-reasoning evaluation
flagged that the propagation was an unverified claim (no unit test); issue #138
backfills the test.

The function under test:

    build_graph_plan(selected, all_stories, prefix) -> dict

builds the bd-graph-apply JSON plan. Each selected story produces one node;
node.metadata carries the per-story fields the chain phases read at runtime
(story_id, build_item, phase, story_file, self_audit_rules, source_audit_doc).

Tests pin three propagations + the dep-resolution behavior that's adjacent:

  - source_audit_doc set in spec → present in node.metadata
  - source_audit_doc absent in spec → empty string in node.metadata
  - self_audit_rules list in spec → comma-joined string in node.metadata

stdlib-only. Matches pack convention.

Run with::

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STORIES_PY = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "overlay"
    / "per-provider"
    / "claude"
    / ".claude"
    / "sdlc-discipline"
    / "stories.py"
)
assert STORIES_PY.exists(), f"stories.py not found at {STORIES_PY}"


def _import_stories() -> object:
    """Import stories.py as a module — needed because the filename has no .py
    relationship to a normal package path. Cache by sys.modules so repeat
    test invocations share the import."""
    if "_stories_module" in sys.modules:
        return sys.modules["_stories_module"]
    spec = importlib.util.spec_from_file_location("_stories_module", STORIES_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_stories_module"] = module
    spec.loader.exec_module(module)
    return module


def _make_spec_file(
    tmp: Path,
    story_id: str,
    *,
    source_audit_doc: str | None = None,
    self_audit_rules: list[str] | None = None,
) -> dict:
    """Write a minimal story spec to tmp + return the loaded story dict shape
    that build_graph_plan consumes."""
    path = tmp / f"{story_id}-test.md"
    fm_lines = [
        "---",
        f"story_id: {story_id}",
        f"title: Test spec for {story_id}",
        "phase: 2",
        "status: ready",
    ]
    if source_audit_doc is not None:
        fm_lines.append(f"source_audit_doc: {source_audit_doc}")
    if self_audit_rules is not None:
        fm_lines.append("self_audit_rules:")
        for rule in self_audit_rules:
            fm_lines.append(f"  - {rule}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(f"# {story_id} test spec")
    fm_lines.append("")
    path.write_text("\n".join(fm_lines) + "\n")

    # Construct the story dict shape that load_all_stories produces.
    story: dict = {
        "story_id": story_id,
        "title": f"Test spec for {story_id}",
        "phase": 2,
        "status": "ready",
        "__path": path,
    }
    if source_audit_doc is not None:
        story["source_audit_doc"] = source_audit_doc
    if self_audit_rules is not None:
        story["self_audit_rules"] = self_audit_rules
    return story


class BuildGraphPlanMetadataTests(unittest.TestCase):
    def test_source_audit_doc_present_propagates_to_metadata(self) -> None:
        """When the spec sets `source_audit_doc`, the resulting bead-node's
        metadata.source_audit_doc carries the value verbatim."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            story = _make_spec_file(tmp, "TEST-001", source_audit_doc="reviews/audit-001.md")
            stories_mod = _import_stories()
            plan = stories_mod.build_graph_plan([story], [story], "el")

            self.assertEqual(len(plan["nodes"]), 1)
            node_meta = plan["nodes"][0]["metadata"]
            self.assertEqual(node_meta["source_audit_doc"], "reviews/audit-001.md")

    def test_source_audit_doc_absent_propagates_as_empty_string(self) -> None:
        """When the spec does not set `source_audit_doc`, the resulting bead-node's
        metadata.source_audit_doc is the empty string (not None, not absent).

        Empty-string-not-None is the contract because bd metadata is string-valued;
        downstream readers do `metadata.source_audit_doc != ""` not `is not None`.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            story = _make_spec_file(tmp, "TEST-002", source_audit_doc=None)
            stories_mod = _import_stories()
            plan = stories_mod.build_graph_plan([story], [story], "el")

            node_meta = plan["nodes"][0]["metadata"]
            self.assertEqual(node_meta["source_audit_doc"], "")
            self.assertIn("source_audit_doc", node_meta, "key must be present even when empty")

    def test_self_audit_rules_list_propagates_as_comma_joined_string(self) -> None:
        """Adjacent claim — propagation contract for self_audit_rules (the field
        v2.27.0 #52 added). The dict-list goes in; a comma-joined string comes
        out. Verifies the propagation shape that the new deferred_work_prose
        rule (v2.30 #123) opts into via spec frontmatter."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            story = _make_spec_file(
                tmp,
                "TEST-003",
                self_audit_rules=["deferred_work_prose", "function_body_length"],
            )
            stories_mod = _import_stories()
            plan = stories_mod.build_graph_plan([story], [story], "el")

            node_meta = plan["nodes"][0]["metadata"]
            self.assertEqual(
                node_meta["self_audit_rules"],
                "deferred_work_prose,function_body_length",
            )

    def test_self_audit_rules_empty_propagates_as_empty_string(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            story = _make_spec_file(tmp, "TEST-004")
            stories_mod = _import_stories()
            plan = stories_mod.build_graph_plan([story], [story], "el")

            node_meta = plan["nodes"][0]["metadata"]
            self.assertEqual(node_meta["self_audit_rules"], "")


if __name__ == "__main__":
    unittest.main()
