"""Tests for snapshot_operator_memory.py (pack #45).

Pure-Python unit tests against the operator-memory snapshot module.
stdlib-only (`unittest` + `importlib` + `tempfile`). Matches the pack's
existing test convention (`test_tech_debt.py`, `test_claude_retry.py`,
`test_tech_debt_classifier.py`).

Run with:

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "overlay"
    / "per-provider"
    / "claude"
    / ".claude"
    / "sdlc-discipline"
    / "snapshot_operator_memory.py"
)
assert MODULE_PATH.exists(), f"snapshot_operator_memory.py not found at {MODULE_PATH}"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("snapshot_operator_memory", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


snapshot = _load_module()


def _make_memory_dir(tmp: Path, cwd_path: str, files: dict[str, str]) -> Path:
    """Create a Claude-Code-style memory directory at tmp/.claude/projects/<key>/memory/.

    `cwd_path` is the would-be working directory the operator was in when
    Claude Code initialized the memory. Its absolute form, with `/` → `-`,
    is the project-key. Returns the path to the created memory dir so
    individual tests can poke at it.
    """
    project_key = cwd_path.replace("/", "-")
    memory_dir = tmp / ".claude" / "projects" / project_key / "memory"
    memory_dir.mkdir(parents=True)
    for name, content in files.items():
        (memory_dir / name).write_text(content)
    return memory_dir


def _entry(name: str, type_: str, description: str = "", body: str = "Body text.") -> str:
    """Render a memory-file body with frontmatter in the shape Claude Code writes.

    Centralizing this in a helper keeps individual tests focused on the
    behavior under test rather than the YAML serialization.
    """
    return textwrap.dedent(
        f"""\
        ---
        name: {name}
        description: {description or "test entry"}
        metadata:
          type: {type_}
        ---

        {body}
        """
    )


class ProjectKeyTests(unittest.TestCase):
    """Project-key derivation matches Claude Code's auto-memory convention.

    A rig at `/home/user/path/to/rig` keys under `-home-user-path-to-rig`.
    The convention is documented at the module docstring; this test pins
    it so a refactor does not silently shift the key shape and disconnect
    the snapshot from the operator's memory.
    """

    def test_absolute_path_becomes_dash_prefixed_key(self) -> None:
        with TemporaryDirectory() as tmp:
            cwd = Path(tmp) / "rig-root"
            cwd.mkdir()
            key = snapshot.project_key(cwd)
            self.assertTrue(
                key.startswith("-"),
                f"project_key should start with `-` (absolute path replacement); got {key!r}",
            )
            self.assertIn("rig-root", key, "project_key should include the rig dir name")


class FilterTypeTests(unittest.TestCase):
    """Selection filters by `metadata.type` ∈ {project, reference}.

    The whole point of the snapshot is to give chain agents the operator's
    *project* + *reference* context — not `user` or `feedback` entries,
    which encode the operator's collaboration preferences with the human-
    facing Claude session and don't apply to chain-spawned agents.
    """

    def test_only_project_and_reference_types_included(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            cwd = tmp / "rig"
            cwd.mkdir()
            _make_memory_dir(
                tmp,
                str(cwd.resolve()),
                {
                    "user_role.md": _entry("user-role", "user", body="USER_BODY"),
                    "feedback_perms.md": _entry("feedback-perms", "feedback", body="FB_BODY"),
                    "project_state.md": _entry("project-state", "project", body="PROJ_BODY"),
                    "reference_host.md": _entry("reference-host", "reference", body="REF_BODY"),
                },
            )
            entries = snapshot.select_entries(snapshot.memory_dir(cwd, tmp))
            types = [fm["type"] for fm, _ in entries]
            self.assertEqual(
                sorted(types),
                ["project", "reference"],
                f"selection should include only project/reference; got types={types}",
            )

    def test_entries_sorted_by_filename_for_determinism(self) -> None:
        """Two consecutive runs against an unchanged dir produce byte-identical output.

        Stable filename ordering is the property the snapshot relies on
        downstream — the chain agents shouldn't see the snapshot reorder
        between runs against the same memory state.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            cwd = tmp / "rig"
            cwd.mkdir()
            _make_memory_dir(
                tmp,
                str(cwd.resolve()),
                {
                    "project_zeta.md": _entry("project-zeta", "project"),
                    "project_alpha.md": _entry("project-alpha", "project"),
                    "project_mu.md": _entry("project-mu", "project"),
                },
            )
            entries = snapshot.select_entries(snapshot.memory_dir(cwd, tmp))
            names = [fm["name"] for fm, _ in entries]
            self.assertEqual(
                names,
                ["project-alpha", "project-mu", "project-zeta"],
                f"entries should sort by filename; got {names}",
            )


class GracefulDegradationTests(unittest.TestCase):
    """The snapshot tool no-ops cleanly when the operator's memory is absent.

    `mostly-unattended` operation requires the kickoff path to succeed even
    on rigs where the operator never set up auto-memory. Empty-but-existing
    files, missing files, and malformed frontmatter all degrade to "produce
    an empty snapshot, exit 0" rather than failing the kickoff.
    """

    def test_no_memory_dir_yields_empty_entries(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            cwd = tmp / "rig-no-memory"
            cwd.mkdir()
            # Deliberately do NOT create the memory dir.
            entries = snapshot.select_entries(snapshot.memory_dir(cwd, tmp))
            self.assertEqual(
                entries, [], f"missing memory dir should yield no entries; got {entries}"
            )

    def test_empty_memory_dir_yields_empty_entries(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            cwd = tmp / "rig"
            cwd.mkdir()
            _make_memory_dir(tmp, str(cwd.resolve()), {})  # dir created but empty
            entries = snapshot.select_entries(snapshot.memory_dir(cwd, tmp))
            self.assertEqual(entries, [], "empty memory dir should yield no entries")

    def test_memory_md_index_file_excluded(self) -> None:
        """MEMORY.md is the index of pointer entries — not itself a memory file.

        Including MEMORY.md's body would duplicate the per-file pointers
        without the actual memory content. Pin that it's filtered out.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            cwd = tmp / "rig"
            cwd.mkdir()
            _make_memory_dir(
                tmp,
                str(cwd.resolve()),
                {
                    "MEMORY.md": "- [some entry](project_x.md) — hook\n",
                    "project_x.md": _entry("project-x", "project"),
                },
            )
            entries = snapshot.select_entries(snapshot.memory_dir(cwd, tmp))
            names = [fm["name"] for fm, _ in entries]
            self.assertEqual(
                names, ["project-x"], f"MEMORY.md should not appear as an entry; got {names}"
            )

    def test_file_with_no_frontmatter_skipped(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            cwd = tmp / "rig"
            cwd.mkdir()
            _make_memory_dir(
                tmp,
                str(cwd.resolve()),
                {
                    "project_x.md": "No frontmatter here, just body text.\n",
                    "project_y.md": _entry("project-y", "project"),
                },
            )
            entries = snapshot.select_entries(snapshot.memory_dir(cwd, tmp))
            names = [fm["name"] for fm, _ in entries]
            self.assertEqual(
                names,
                ["project-y"],
                f"file without frontmatter should be skipped; got {names}",
            )


class RenderTests(unittest.TestCase):
    """Snapshot output format pins the contract the chain-agent prompts read."""

    def test_empty_entries_yield_empty_string(self) -> None:
        """Downstream prompts handle the empty case as a no-op.

        The kickoff writes an empty file rather than no file; the agent
        prompt's context-loading step reads it and short-circuits when
        the content is empty. This keeps the path resolution simple
        (file always exists) without forcing the agent to special-case
        absent content.
        """
        self.assertEqual(snapshot.render_snapshot([]), "")

    def test_entries_render_with_slug_header_and_body(self) -> None:
        rendered = snapshot.render_snapshot(
            [
                ({"name": "project-foo", "description": "foo desc", "type": "project"}, "Foo body"),
                (
                    {"name": "reference-bar", "description": "bar desc", "type": "reference"},
                    "Bar body",
                ),
            ]
        )
        self.assertIn("## project-foo", rendered, "slug header missing for project-foo")
        self.assertIn("## reference-bar", rendered, "slug header missing for reference-bar")
        self.assertIn("Foo body", rendered, "body content missing for project-foo")
        self.assertIn("Bar body", rendered, "body content missing for reference-bar")
        self.assertIn(
            "*foo desc*", rendered, "description should render as italic line under header"
        )


class CLITests(unittest.TestCase):
    """End-to-end through the `main()` entry point."""

    def test_main_writes_snapshot_and_creates_parent_dir(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            cwd = tmp / "rig"
            cwd.mkdir()
            _make_memory_dir(
                tmp,
                str(cwd.resolve()),
                {"project_test.md": _entry("project-test", "project", body="TEST_BODY")},
            )
            output = tmp / "subdir" / "operator-context.md"  # parent doesn't exist yet
            rc = snapshot.main(
                [
                    "--output",
                    str(output),
                    "--cwd",
                    str(cwd),
                    "--home",
                    str(tmp),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(output.exists(), f"output should be created at {output}")
            content = output.read_text()
            self.assertIn("project-test", content, "rendered content should include the slug")
            self.assertIn("TEST_BODY", content, "rendered content should include the body")

    def test_main_writes_empty_file_when_no_matching_entries(self) -> None:
        """The empty-file shape is the no-op signal for downstream prompts.

        Writing an empty file rather than skipping the write means the
        agent prompt's context-loading step doesn't have to special-case
        an absent file vs an empty file — it just reads whatever's there
        and short-circuits on empty content.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            cwd = tmp / "rig"
            cwd.mkdir()
            output = tmp / "operator-context.md"
            rc = snapshot.main(
                [
                    "--output",
                    str(output),
                    "--cwd",
                    str(cwd),
                    "--home",
                    str(tmp),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(output.exists(), "output file should be created even with no entries")
            self.assertEqual(output.read_text(), "", "no matching entries should yield empty file")


if __name__ == "__main__":
    unittest.main()
