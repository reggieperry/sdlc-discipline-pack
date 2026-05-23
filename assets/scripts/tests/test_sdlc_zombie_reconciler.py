"""Tests for sdlc-zombie-reconciler.sh (pack #92).

Black-box subprocess tests with recording fake binaries for gc, bd, gh,
sdlc-notify.sh, and python3 stories.py on the test's PATH. Each test
stands up a contrived rig with stories/EL-*.md fixtures, invokes the
reconciler, and inspects fake-argv logs to verify HIGH-confidence
zombies are archived and weak-signal specs are left alone.

stdlib-only (unittest + tempfile + subprocess + textwrap). Matches pack
convention.

Run with::

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-zombie-reconciler.sh"
assert SCRIPT_PATH.exists(), f"sdlc-zombie-reconciler.sh not found at {SCRIPT_PATH}"
PACK_ROOT = Path(__file__).resolve().parent.parent.parent.parent
STORIES_PY = PACK_ROOT / "overlay/per-provider/claude/.claude/sdlc-discipline/stories.py"
assert STORIES_PY.exists(), f"stories.py not found at {STORIES_PY}"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_gc(fakes_dir: Path, rig_list_json: str) -> None:
    """Fake gc that returns rig_list_json for `gc rig list --json`."""
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{fakes_dir}/gc-argv.log"\n'
        'if [ "$1" = "rig" ] && [ "$2" = "list" ]; then\n'
        "    cat <<'__GC_EOF__'\n"
        f"{rig_list_json}\n"
        "__GC_EOF__\n"
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_executable(fakes_dir / "gc", body)


def _fake_bd(fakes_dir: Path, list_response: str = "[]") -> None:
    """Fake bd that returns list_response for `bd list ... --json`."""
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{fakes_dir}/bd-argv.log"\n'
        'if [ "$1" = "-C" ]; then shift 2; fi\n'
        'if [ "$1" = "list" ]; then\n'
        "    cat <<'__BD_LIST_EOF__'\n"
        f"{list_response}\n"
        "__BD_LIST_EOF__\n"
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_executable(fakes_dir / "bd", body)


def _fake_gh(fakes_dir: Path, pr_list_response: str = "[]") -> None:
    """Fake gh that returns pr_list_response for `gh pr list ... --json`."""
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{fakes_dir}/gh-argv.log"\n'
        'if [ "$1" = "pr" ] && [ "$2" = "list" ]; then\n'
        "    cat <<'__GH_EOF__'\n"
        f"{pr_list_response}\n"
        "__GH_EOF__\n"
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_executable(fakes_dir / "gh", body)


def _fake_python3_stories(fakes_dir: Path, archive_exit: int = 0) -> None:
    """Wrapper invoked when the reconciler calls `python3 <stories.py> archive ...`.

    Substitutes the python3 binary on PATH with a wrapper that detects
    `archive` in argv and short-circuits, logging the call; any other
    invocation falls through to the real python3.
    """
    real_python = subprocess.run(
        ["which", "python3"], capture_output=True, text=True
    ).stdout.strip()
    body = (
        "#!/bin/bash\n"
        "# If invoked with a stories.py path + 'archive' subcommand, record + exit.\n"
        'for arg in "$@"; do\n'
        '    if [ "$arg" = "archive" ]; then\n'
        f'        echo "$@" >> "{fakes_dir}/stories-archive-argv.log"\n'
        f"        exit {archive_exit}\n"
        "    fi\n"
        "done\n"
        f'exec "{real_python}" "$@"\n'
    )
    _write_executable(fakes_dir / "python3", body)


def _fake_notify(fakes_dir: Path) -> None:
    body = f'#!/bin/bash\necho "$@" >> "{fakes_dir}/notify-argv.log"\nexit 0\n'
    pack_assets_scripts = fakes_dir / "fake-pack" / "assets" / "scripts"
    pack_assets_scripts.mkdir(parents=True, exist_ok=True)
    _write_executable(pack_assets_scripts / "sdlc-notify.sh", body)
    # Also symlink the real stories.py into the fake-pack layout.
    fake_bridge = (
        fakes_dir
        / "fake-pack"
        / "overlay"
        / "per-provider"
        / "claude"
        / ".claude"
        / "sdlc-discipline"
    )
    fake_bridge.mkdir(parents=True, exist_ok=True)
    (fake_bridge / "stories.py").symlink_to(STORIES_PY)


def _write_spec(stories_dir: Path, story_id: str, status: str, filed_as_bead: str = "") -> Path:
    """Write a minimal spec to stories/<story_id>-...md with the given frontmatter."""
    spec_path = stories_dir / f"{story_id}-test.md"
    body = textwrap.dedent(
        f"""\
        ---
        story_id: {story_id}
        title: Test spec for {story_id}
        status: {status}
        filed_as_bead: {filed_as_bead}
        ---

        # {story_id} test spec
        """
    )
    spec_path.write_text(body)
    return spec_path


def _setup_rig(tmp: Path, rig_name: str = "test-rig") -> tuple[Path, Path, Path]:
    """Create city + rig + stories/ structure. Returns (city_root, rig_root, fakes_dir)."""
    city_root = tmp / "city"
    rig_root = city_root / rig_name
    stories_dir = rig_root / "stories"
    fakes_dir = tmp / "fakes"
    stories_dir.mkdir(parents=True)
    fakes_dir.mkdir(parents=True)
    return city_root, rig_root, fakes_dir


def _rig_list_json(rig_name: str, rig_root: Path) -> str:
    return json.dumps(
        {"rigs": [{"name": rig_name, "path": str(rig_root), "hq": False, "suspended": False}]}
    )


def _invoke(fakes_dir: Path, city_root: Path, enabled: bool = True) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{fakes_dir}:{os.environ['PATH']}",
        "GC_CITY_ROOT": str(city_root),
        "PACK_DIR": str(fakes_dir / "fake-pack"),
        "SDLC_ZOMBIE_RECONCILER_ENABLED": "true" if enabled else "false",
    }
    return subprocess.run(
        [str(SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class FeatureGateTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            _fake_gc(fakes_dir, _rig_list_json("test-rig", rig_root))
            _fake_bd(fakes_dir)
            _fake_gh(fakes_dir)
            _fake_python3_stories(fakes_dir)
            _fake_notify(fakes_dir)

            # No env var set → default OFF → script exits early; gc never invoked.
            env = {
                **os.environ,
                "PATH": f"{fakes_dir}:{os.environ['PATH']}",
                "GC_CITY_ROOT": str(city_root),
            }
            env.pop("SDLC_ZOMBIE_RECONCILER_ENABLED", None)
            result = subprocess.run(
                [str(SCRIPT_PATH)], env=env, capture_output=True, text=True, timeout=10
            )

            self.assertEqual(result.returncode, 0)
            self.assertFalse(
                (fakes_dir / "gc-argv.log").exists(), "gc must not be invoked when disabled"
            )

    def test_enabled_invokes_gc_rig_list(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            _fake_gc(fakes_dir, _rig_list_json("test-rig", rig_root))
            _fake_bd(fakes_dir)
            _fake_gh(fakes_dir)
            _fake_python3_stories(fakes_dir)
            _fake_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root, enabled=True)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertTrue((fakes_dir / "gc-argv.log").exists(), "gc must be invoked when enabled")


class HighConfidenceArchiveTests(unittest.TestCase):
    def test_bead_metadata_signal_triggers_archive(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            _write_spec(rig_root / "stories", "EL-134", status="ready")
            beads_json = json.dumps(
                [
                    {
                        "id": "el-abc",
                        "status": "closed",
                        "metadata": {
                            "story_id": "EL-134",
                            "final_state": "merged",
                            "pr_url": "https://github.com/x/y/pull/417",
                            "final_merged_sha": "6b0727a",
                        },
                    }
                ]
            )
            _fake_gc(fakes_dir, _rig_list_json("test-rig", rig_root))
            _fake_bd(fakes_dir, list_response=beads_json)
            _fake_gh(fakes_dir)
            _fake_python3_stories(fakes_dir)
            _fake_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            archive_log = fakes_dir / "stories-archive-argv.log"
            self.assertTrue(archive_log.exists(), "stories.py archive must be invoked")
            archive_call = archive_log.read_text()
            self.assertIn("archive", archive_call)
            self.assertIn("EL-134", archive_call)
            self.assertIn("--pr", archive_call)
            self.assertIn("https://github.com/x/y/pull/417", archive_call)
            self.assertIn("6b0727a", archive_call)

    def test_pr_title_prefix_signal_triggers_archive(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            _write_spec(rig_root / "stories", "EL-200", status="ready")
            prs_json = json.dumps(
                [
                    {
                        "number": 100,
                        "title": "EL-200: Triple Screen long entry",
                        "headRefName": "feature/some-other-bead",
                        "mergeCommit": {"oid": "abc1234"},
                        "url": "https://github.com/x/y/pull/100",
                    }
                ]
            )
            _fake_gc(fakes_dir, _rig_list_json("test-rig", rig_root))
            _fake_bd(fakes_dir)  # no bead match
            _fake_gh(fakes_dir, pr_list_response=prs_json)
            _fake_python3_stories(fakes_dir)
            _fake_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            archive_log = fakes_dir / "stories-archive-argv.log"
            self.assertTrue(archive_log.exists(), "stories.py archive must be invoked")
            archive_call = archive_log.read_text()
            self.assertIn("EL-200", archive_call)
            self.assertIn("abc1234", archive_call)

    def test_branch_name_signal_triggers_archive(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            _write_spec(rig_root / "stories", "EL-201", status="ready", filed_as_bead="el-xyz")
            prs_json = json.dumps(
                [
                    {
                        "number": 200,
                        "title": "Unrelated title without story id",
                        "headRefName": "feature/el-xyz",
                        "mergeCommit": {"oid": "deadbeef"},
                        "url": "https://github.com/x/y/pull/200",
                    }
                ]
            )
            _fake_gc(fakes_dir, _rig_list_json("test-rig", rig_root))
            _fake_bd(fakes_dir)
            _fake_gh(fakes_dir, pr_list_response=prs_json)
            _fake_python3_stories(fakes_dir)
            _fake_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            archive_log = fakes_dir / "stories-archive-argv.log"
            self.assertTrue(archive_log.exists(), "branch-name match must trigger archive")
            self.assertIn("deadbeef", archive_log.read_text())


class SkipTests(unittest.TestCase):
    """Specs that should NOT be archived."""

    def test_status_closed_is_skipped(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            _write_spec(rig_root / "stories", "EL-300", status="closed")
            beads_json = json.dumps(
                [
                    {
                        "id": "el-abc",
                        "status": "closed",
                        "metadata": {"story_id": "EL-300", "final_state": "merged"},
                    },
                ]
            )
            _fake_gc(fakes_dir, _rig_list_json("test-rig", rig_root))
            _fake_bd(fakes_dir, list_response=beads_json)
            _fake_gh(fakes_dir)
            _fake_python3_stories(fakes_dir)
            _fake_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0)
            self.assertFalse(
                (fakes_dir / "stories-archive-argv.log").exists(),
                "status: closed must not trigger archive",
            )

    def test_no_signal_match_no_archive(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            _write_spec(rig_root / "stories", "EL-400", status="ready")
            # bd returns no matching bead; gh returns no matching PR.
            _fake_gc(fakes_dir, _rig_list_json("test-rig", rig_root))
            _fake_bd(fakes_dir, list_response="[]")
            _fake_gh(fakes_dir, pr_list_response="[]")
            _fake_python3_stories(fakes_dir)
            _fake_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertFalse(
                (fakes_dir / "stories-archive-argv.log").exists(),
                "no signal match → no archive",
            )


class NoCityRootTests(unittest.TestCase):
    def test_missing_city_root_exits_clean(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            fakes_dir = tmp / "fakes"
            fakes_dir.mkdir()
            _fake_gc(fakes_dir, '{"rigs": []}')
            _fake_bd(fakes_dir)
            _fake_gh(fakes_dir)
            _fake_python3_stories(fakes_dir)
            _fake_notify(fakes_dir)

            env = {
                **os.environ,
                "PATH": f"{fakes_dir}:{os.environ['PATH']}",
                "SDLC_ZOMBIE_RECONCILER_ENABLED": "true",
                "PACK_DIR": str(fakes_dir / "fake-pack"),
            }
            env.pop("GC_CITY_ROOT", None)
            result = subprocess.run(
                [str(SCRIPT_PATH)], env=env, capture_output=True, text=True, timeout=10
            )

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertIn("GC_CITY_ROOT not set", result.stderr)


if __name__ == "__main__":
    unittest.main()
