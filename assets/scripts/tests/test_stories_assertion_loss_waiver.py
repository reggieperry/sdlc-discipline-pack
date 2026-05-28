"""Tests for assertion_loss_waiver propagation in stories.py (pack #199).

The migration waiver is declared in a spec's frontmatter as a single-line
JSON string (the YAML subset has no nested-map support, so it is a scalar)
and must flow into the bead's metadata via build_graph_plan, so the chain
phase can later read it and pass it to sdlc-gate.py. This pins that the
field propagates verbatim and stays valid JSON, and that an absent waiver
yields an empty string (the common case, unchanged behavior).

stdlib-only (unittest + importlib + tempfile). Matches pack convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_stories_assertion_loss_waiver -v
"""

from __future__ import annotations

import importlib.util
import json
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


def _load_stories():
    spec = importlib.util.spec_from_file_location("stories_under_test", STORIES_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


stories = _load_stories()

WAIVER_JSON = (
    '{"file": "tests/test_x.py", "expected_delta": -3, '
    '"migrated_to_test": "tests/test_y.py", "migrated_in": "EL-998"}'
)


def _spec(tmp: Path, *, with_waiver: bool) -> dict:
    waiver_line = f"assertion_loss_waiver: {WAIVER_JSON}\n" if with_waiver else ""
    path = tmp / "EL-999-test.md"
    path.write_text(
        "---\n"
        "story_id: EL-999\n"
        "title: Waiver propagation\n"
        "status: ready\n"
        f"{waiver_line}"
        "---\n\n# body\n"
    )
    fm, _ = stories.parse_frontmatter(path)
    fm["__path"] = path
    return fm


def _node_metadata(spec: dict) -> dict:
    plan = stories.build_graph_plan([spec], [spec], "bd")
    return plan["nodes"][0]["metadata"]


class AssertionLossWaiverPropagation(unittest.TestCase):
    def test_waiver_string_propagates_verbatim_and_is_valid_json(self) -> None:
        with TemporaryDirectory() as t:
            md = _node_metadata(_spec(Path(t), with_waiver=True))
            self.assertEqual(md["assertion_loss_waiver"], WAIVER_JSON)
            parsed = json.loads(md["assertion_loss_waiver"])
            self.assertEqual(parsed["file"], "tests/test_x.py")
            self.assertEqual(parsed["expected_delta"], -3)
            self.assertEqual(parsed["migrated_to_test"], "tests/test_y.py")

    def test_absent_waiver_is_empty_string(self) -> None:
        with TemporaryDirectory() as t:
            md = _node_metadata(_spec(Path(t), with_waiver=False))
            self.assertEqual(md["assertion_loss_waiver"], "")


if __name__ == "__main__":
    unittest.main()
