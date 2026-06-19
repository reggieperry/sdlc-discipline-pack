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
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _spies import (
    spy_bd_dispatch,
    spy_bd_list,
    spy_gc_rig_list,
    spy_gh_pr_list,
    spy_notify,
    spy_python3_stories_archive,
)

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-zombie-reconciler.sh"
assert SCRIPT_PATH.exists(), f"sdlc-zombie-reconciler.sh not found at {SCRIPT_PATH}"
PACK_ROOT = Path(__file__).resolve().parent.parent.parent.parent
STORIES_PY = PACK_ROOT / "overlay/per-provider/claude/.claude/sdlc-discipline/stories.py"
assert STORIES_PY.exists(), f"stories.py not found at {STORIES_PY}"


def _setup_fake_pack_with_notify(fakes_dir: Path) -> None:
    """Build the fake-pack bridge layout the reconciler walks at runtime.

    Calls ``spy_notify`` to drop the notify shim, then symlinks the real
    ``stories.py`` into ``<fakes_dir>/fake-pack/overlay/.../sdlc-discipline/``
    so the reconciler's ``python3 <bridge>/stories.py archive`` invocation
    resolves to a real file. The symlink is test-fixture setup beyond
    pure-spy logic, which is why it stays local rather than living in
    ``_spies.py``.
    """
    spy_notify(fakes_dir)
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
    (city_root / "city.toml").write_text("[city]\n")
    return city_root, rig_root, fakes_dir


def _rig_list_json(rig_name: str, rig_root: Path) -> str:
    return json.dumps(
        {"rigs": [{"name": rig_name, "path": str(rig_root), "hq": False, "suspended": False}]}
    )


def _invoke(
    fakes_dir: Path,
    city_root: Path,
    enabled: bool = True,
    *,
    city_var: str = "GC_CITY_ROOT",
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{fakes_dir}:{os.environ['PATH']}",
        "PACK_DIR": str(fakes_dir / "fake-pack"),
        "SDLC_ZOMBIE_RECONCILER_ENABLED": "true" if enabled else "false",
    }
    # Point the script at the fake city via the requested var, clearing the
    # others so the resolver must use city_var. city_var="GC_CITY" mirrors
    # gascity's post-GC_CITY_ROOT-retirement order-exec env (issue #204).
    for _var in ("GC_CITY_ROOT", "GC_CITY", "GC_CITY_PATH"):
        env.pop(_var, None)
    env[city_var] = str(city_root)
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
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir)
            spy_gh_pr_list(fakes_dir)
            spy_python3_stories_archive(fakes_dir)
            _setup_fake_pack_with_notify(fakes_dir)

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
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir)
            spy_gh_pr_list(fakes_dir)
            spy_python3_stories_archive(fakes_dir)
            _setup_fake_pack_with_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root, enabled=True)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertTrue((fakes_dir / "gc-argv.log").exists(), "gc must be invoked when enabled")


class PostMigrationEnvTests(unittest.TestCase):
    """The reconciler resolves its city root from GC_CITY when GC_CITY_ROOT is
    unset — the env shape gascity hands the order since GC_CITY_ROOT was retired
    from order-exec (issue #204). Proves the script gets past the city-root guard
    to enumerate rigs instead of silently no-op'ing on the live box."""

    def test_resolves_via_gc_city_and_enumerates(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir)
            spy_gh_pr_list(fakes_dir)
            spy_python3_stories_archive(fakes_dir)
            _setup_fake_pack_with_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root, enabled=True, city_var="GC_CITY")

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertTrue(
                (fakes_dir / "gc-argv.log").exists(),
                "reconciler must resolve the city via GC_CITY and invoke gc rig list",
            )


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
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=beads_json)
            spy_gh_pr_list(fakes_dir)
            spy_python3_stories_archive(fakes_dir)
            _setup_fake_pack_with_notify(fakes_dir)

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
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir)  # no bead match
            spy_gh_pr_list(fakes_dir, pr_list_response=prs_json)
            spy_python3_stories_archive(fakes_dir)
            _setup_fake_pack_with_notify(fakes_dir)

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
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir)
            spy_gh_pr_list(fakes_dir, pr_list_response=prs_json)
            spy_python3_stories_archive(fakes_dir)
            _setup_fake_pack_with_notify(fakes_dir)

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
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=beads_json)
            spy_gh_pr_list(fakes_dir)
            spy_python3_stories_archive(fakes_dir)
            _setup_fake_pack_with_notify(fakes_dir)

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
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response="[]")
            spy_gh_pr_list(fakes_dir, pr_list_response="[]")
            spy_python3_stories_archive(fakes_dir)
            _setup_fake_pack_with_notify(fakes_dir)

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
            spy_gc_rig_list(fakes_dir, '{"rigs": []}')
            spy_bd_list(fakes_dir)
            spy_gh_pr_list(fakes_dir)
            spy_python3_stories_archive(fakes_dir)
            _setup_fake_pack_with_notify(fakes_dir)

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
            self.assertIn("cannot resolve city root", result.stderr)


class PostMergeWritebackTests(unittest.TestCase):
    """Pack #170 — extend the zombie-reconciler to handle the post-merge
    writeback failure pattern.

    Pre-#170: the reconciler skipped specs with status in {filed, in-flight,
    closed} as "terminal-correct." When a chain's finalizer failed to write
    back merged_pr after a human-merged PR, the spec stayed at status=filed
    and the bead's final_state stayed at pr_open_for_human — both stale.
    The cross-batch dep watcher (v2.32.0) reads the bead's final_state to
    decide whether to clear downstream defers, so a stale final_state means
    downstream chains stay parked even after the predecessor merges.

    Post-#170: the reconciler processes status=filed specs through the
    same HIGH-confidence detection paths it uses for status=ready zombies,
    and on a successful archive, additionally advances the predecessor
    bead's final_state to "merged" so the cross-batch dep watcher fires
    on the next tick.
    """

    def test_filed_spec_with_merged_pr_signal_archives_and_advances_bead(self) -> None:
        """A status=filed spec whose `feature/<filed_as_bead>` branch matches
        a merged PR triggers (a) the archive call and (b) a bd update that
        advances the bead's final_state to "merged".
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            _write_spec(
                rig_root / "stories",
                "EL-170",
                status="filed",
                filed_as_bead="el-pr508",
            )
            beads_json = json.dumps(
                [
                    {
                        "id": "el-pr508",
                        "status": "closed",
                        "metadata": {
                            "story_id": "EL-170",
                            "final_state": "pr_open_for_human",
                        },
                    }
                ]
            )
            prs_json = json.dumps(
                [
                    {
                        "number": 508,
                        "title": "EL-170: stage 1",
                        "headRefName": "feature/el-pr508",
                        "url": "https://github.com/x/y/pull/508",
                        "mergeCommit": {"oid": "352b563"},
                        "mergedAt": "2026-05-25T23:52:32Z",
                    }
                ]
            )
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=beads_json)
            spy_gh_pr_list(fakes_dir, pr_list_response=prs_json)
            spy_python3_stories_archive(fakes_dir)
            _setup_fake_pack_with_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root, enabled=True)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            archive_log = fakes_dir / "stories-archive-argv.log"
            self.assertTrue(
                archive_log.exists(),
                "stories.py archive should have been invoked for the filed-but-merged spec",
            )
            archive_text = archive_log.read_text()
            self.assertIn("archive", archive_text)
            self.assertIn("EL-170", archive_text)
            self.assertIn("https://github.com/x/y/pull/508", archive_text)
            bd_log = fakes_dir / "bd-argv.log"
            self.assertTrue(bd_log.exists(), "bd should have been invoked")
            bd_calls = bd_log.read_text()
            self.assertIn("update el-pr508", bd_calls)
            self.assertIn("final_state=merged", bd_calls)

    def test_filed_spec_with_no_signal_is_not_archived(self) -> None:
        """A status=filed spec whose PR has NOT yet merged (no gh signal,
        no bead-metadata signal) is left alone — same conservative posture
        the reconciler applies to status=ready zombies with no match.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            _write_spec(
                rig_root / "stories",
                "EL-171",
                status="filed",
                filed_as_bead="el-inflight",
            )
            beads_json = json.dumps(
                [
                    {
                        "id": "el-inflight",
                        "status": "open",
                        "metadata": {"story_id": "EL-171"},
                    }
                ]
            )
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=beads_json)
            spy_gh_pr_list(fakes_dir, pr_list_response="[]")
            spy_python3_stories_archive(fakes_dir)
            _setup_fake_pack_with_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root, enabled=True)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            archive_log = fakes_dir / "stories-archive-argv.log"
            self.assertFalse(
                archive_log.exists(),
                "stories.py archive should NOT have fired for an in-flight spec",
            )
            bd_log = fakes_dir / "bd-argv.log"
            if bd_log.exists():
                bd_calls = bd_log.read_text()
                self.assertNotIn(
                    "final_state=merged",
                    bd_calls,
                    "bd update should NOT have set final_state=merged for an in-flight bead",
                )

    def test_filed_spec_with_bead_metadata_signal_advances_bead_idempotently(self) -> None:
        """Signal 1 path: a status=filed spec whose bead is already at
        final_state=merged (e.g., advanced by a prior reconciler run or
        manual housekeeping) still archives the spec. The bd update is
        idempotent on this side — calling it again with the same value
        is a no-op at the bd layer.
        """
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            _write_spec(
                rig_root / "stories",
                "EL-172",
                status="filed",
                filed_as_bead="el-alreadymerged",
            )
            beads_json = json.dumps(
                [
                    {
                        "id": "el-alreadymerged",
                        "status": "closed",
                        "metadata": {
                            "story_id": "EL-172",
                            "final_state": "merged",
                            "pr_url": "https://github.com/x/y/pull/600",
                            "final_merged_sha": "abcd1234",
                        },
                    }
                ]
            )
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=beads_json)
            spy_gh_pr_list(fakes_dir, pr_list_response="[]")
            spy_python3_stories_archive(fakes_dir)
            _setup_fake_pack_with_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root, enabled=True)

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            archive_log = fakes_dir / "stories-archive-argv.log"
            self.assertTrue(archive_log.exists(), "archive should fire on Signal 1")
            archive_text = archive_log.read_text()
            self.assertIn("EL-172", archive_text)
            self.assertIn("https://github.com/x/y/pull/600", archive_text)
            self.assertIn("abcd1234", archive_text)
            bd_log = fakes_dir / "bd-argv.log"
            self.assertTrue(bd_log.exists())
            self.assertIn(
                "update el-alreadymerged",
                bd_log.read_text(),
                "bd update should fire even when the bead's final_state is already merged",
            )


class Issue210Tests(unittest.TestCase):
    """#210 — the reconciler ran but timed out: it queried the closed-bead
    list once per spec (~9s each on ~20k closed beads), so ~57 specs blew the
    5m order deadline. Cache the query once per run; keep the PR link when a
    bead carries merged_pr but no pr_url."""

    def test_closed_bead_query_is_cached_once_per_run(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            _write_spec(rig_root / "stories", "EL-134", status="ready")
            _write_spec(rig_root / "stories", "EL-135", status="ready")
            beads_json = json.dumps(
                [
                    {
                        "id": "el-a",
                        "status": "closed",
                        "metadata": {
                            "story_id": "EL-134",
                            "final_state": "merged",
                            "pr_url": "https://github.com/x/y/pull/1",
                        },
                    },
                    {
                        "id": "el-b",
                        "status": "closed",
                        "metadata": {
                            "story_id": "EL-135",
                            "final_state": "merged",
                            "pr_url": "https://github.com/x/y/pull/2",
                        },
                    },
                ]
            )
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=beads_json)
            spy_gh_pr_list(fakes_dir)
            spy_python3_stories_archive(fakes_dir)
            _setup_fake_pack_with_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root)
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            archive_text = (fakes_dir / "stories-archive-argv.log").read_text()
            self.assertIn("EL-134", archive_text)
            self.assertIn("EL-135", archive_text)
            bd_calls = (fakes_dir / "bd-argv.log").read_text().strip().splitlines()
            list_calls = [c for c in bd_calls if "list" in c and "--status=closed" in c]
            self.assertEqual(
                len(list_calls),
                1,
                f"closed-bead list must be queried ONCE per run, not per spec; got {list_calls}",
            )

    def test_archive_uses_merged_pr_when_pr_url_absent(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            _write_spec(rig_root / "stories", "EL-179", status="ready")
            beads_json = json.dumps(
                [
                    {
                        "id": "el-r5",
                        "status": "closed",
                        "metadata": {
                            "story_id": "EL-179",
                            "final_state": "merged",
                            "merged_pr": 553,
                        },
                    },
                ]
            )
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=beads_json)
            spy_gh_pr_list(fakes_dir)
            spy_python3_stories_archive(fakes_dir)
            _setup_fake_pack_with_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root)
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            archive_text = (fakes_dir / "stories-archive-argv.log").read_text()
            self.assertIn("EL-179", archive_text)
            self.assertIn("553", archive_text)  # the PR ref, not an empty --pr


class IntervalConfigTests(unittest.TestCase):
    """Pin the order's TOML interval at the operator-calibrated cadence.

    The cadence is the binding constraint on user-visible recovery latency
    when the finalizer's spec-frontmatter writeback fails (the pack #170 +
    #174 path). 24h was the original safety-net assumption; 5m is calibrated
    to the cross-batch-dep-watcher's interval — the downstream consumer of
    the bead state advance — so end-to-end recovery is dominated by the
    watcher cycle rather than the reconciler cycle. See #174 for rationale.
    """

    def test_interval_is_5m(self) -> None:
        import tomllib

        order_path = Path(__file__).resolve().parents[3] / "orders" / "sdlc-zombie-reconciler.toml"
        with order_path.open("rb") as fh:
            config = tomllib.load(fh)

        self.assertEqual(
            config["order"]["interval"],
            "5m",
            "zombie-reconciler interval pinned at 5m (matches cross-batch-dep-watcher cadence; see #174)",
        )


class ArchiveCommitTests(unittest.TestCase):
    """Pack #219 — the reconciler must COMMIT and PUSH its archive moves.

    Pre-fix it ran `stories.py archive` (which moves active -> _archive on
    disk) but never git-committed, leaving the rig dirty and the archive
    unpropagated to origin. This test git-inits the rig + a bare remote and
    runs the REAL `stories.py archive` (no `spy_python3_stories_archive`),
    then asserts a commit landed staging the move, the tree is clean, and
    origin advanced.
    """

    @staticmethod
    def _git(rig: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(rig), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    def test_archive_is_committed_and_pushed(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, rig_root, fakes_dir = _setup_rig(tmp)
            _write_spec(rig_root / "stories", "EL-901", status="filed", filed_as_bead="el-901bd")

            # git rig + bare remote so the reconciler has somewhere to push.
            remote = tmp / "remote.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            self._git(rig_root, "init", "-q", "-b", "main")
            self._git(rig_root, "config", "user.email", "t@t")
            self._git(rig_root, "config", "user.name", "t")
            self._git(rig_root, "config", "commit.gpgsign", "false")
            self._git(rig_root, "add", "-A")
            self._git(rig_root, "commit", "-q", "-m", "initial")
            self._git(rig_root, "remote", "add", "origin", str(remote))
            self._git(rig_root, "push", "-q", "-u", "origin", "main")
            head_before = self._git(rig_root, "rev-parse", "HEAD")

            beads_json = json.dumps(
                [
                    {
                        "id": "el-901bd",
                        "status": "closed",
                        "metadata": {"story_id": "EL-901", "final_state": "pr_open_for_human"},
                    }
                ]
            )
            prs_json = json.dumps(
                [
                    {
                        "number": 901,
                        "title": "EL-901: thing",
                        "headRefName": "feature/el-901bd",
                        "url": "https://github.com/x/y/pull/901",
                        "mergeCommit": {"oid": "abc1234"},
                        "mergedAt": "2026-05-31T00:00:00Z",
                    }
                ]
            )
            spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
            spy_bd_list(fakes_dir, list_response=beads_json)
            spy_gh_pr_list(fakes_dir, pr_list_response=prs_json)
            # No spy_python3_stories_archive — the REAL stories.py archive moves the files.
            _setup_fake_pack_with_notify(fakes_dir)

            result = _invoke(fakes_dir, city_root, enabled=True)
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")

            # the spec actually moved
            self.assertFalse(
                (rig_root / "stories" / "EL-901-test.md").exists(), "active spec should be gone"
            )
            self.assertTrue(
                list((rig_root / "stories" / "_archive").glob("EL-901-*.md")),
                "the _archive copy should exist",
            )
            # a commit landed staging the move, and the tree is clean afterward
            head_after = self._git(rig_root, "rev-parse", "HEAD")
            self.assertNotEqual(head_before, head_after, "reconciler must COMMIT the archive move")
            committed = self._git(rig_root, "show", "--name-status", "--format=", "HEAD")
            self.assertIn("EL-901", committed)
            self.assertIn("_archive/", committed)
            self.assertEqual(
                self._git(rig_root, "status", "--porcelain"), "", "rig must be clean after archive"
            )
            # the archive commit was PUSHED to origin
            self.assertEqual(
                self._git(rig_root, "rev-parse", "HEAD"),
                self._git(rig_root, "rev-parse", "origin/main"),
                "archive commit must be pushed to origin",
            )


class BlockedZombieCloserTests(unittest.TestCase):
    """Issue #243 — the reconciler must CLOSE a merged-but-blocked zombie.

    Root cause: a chain phase parks a bead with the invalid `--status=escalated`
    (bd rejects it atomically), the LLM retries with bare `--status=blocked`, and
    once the PR merges externally nothing flips that `blocked` bead to `closed`.
    The reconciler stamps `final_state=merged` but left status untouched, so the
    bead gated its downstream deps indefinitely (el-az1chd / EL-274 sat blocked
    17h after PR #738 merged).

    Fix: when a HIGH-confidence merge signal hits AND the bead's current status
    is `blocked` (the unambiguous zombie shape — a parked bead whose PR merged),
    additionally `bd update <bead> --status=closed`. A bead at `in_progress`
    (active re-walk) or `open` (possibly queued for re-walk) is NOT closed —
    closing those would interrupt live work.

    These tests drive detection through Signal 2 (PR branch match against
    `filed_as_bead`), the path that actually carries the zombie shape: the bead
    is NOT in the closed-bead list, so its live status is read via `bd show`.
    The fake (`spy_bd_dispatch`) answers `bd show <id> --json` with a
    configurable status so the guard can be exercised.
    """

    @staticmethod
    def _bead_show_json(bead_id: str, status: str) -> str:
        return json.dumps(
            [
                {
                    "id": bead_id,
                    "status": status,
                    "metadata": {"story_id": "EL-243", "final_state": "pr_open_for_human"},
                }
            ]
        )

    def _run(self, bead_status: str) -> tuple[subprocess.CompletedProcess, Path]:
        """Stand up a Signal-2 zombie whose bead reports `bead_status`, run the
        reconciler, return (result, fakes_dir) for argv-log inspection."""
        self._ctx = TemporaryDirectory()
        tmp = Path(self._ctx.name)
        self.addCleanup(self._ctx.cleanup)
        city_root, rig_root, fakes_dir = _setup_rig(tmp)
        bead_id = "el-243bd"
        _write_spec(rig_root / "stories", "EL-243", status="ready", filed_as_bead=bead_id)
        prs_json = json.dumps(
            [
                {
                    "number": 738,
                    "title": "EL-243: merged-but-blocked closer",
                    "headRefName": f"feature/{bead_id}",
                    "url": "https://github.com/x/y/pull/738",
                    "mergeCommit": {"oid": "cafef00d"},
                    "mergedAt": "2026-06-19T00:00:00Z",
                }
            ]
        )
        spy_gc_rig_list(fakes_dir, _rig_list_json("test-rig", rig_root))
        # bd: empty closed-list (forces Signal 2) + a `bd show` that reports the
        # bead's live status so the closer's guard can read it.
        spy_bd_dispatch(
            fakes_dir,
            {
                "list": "[]",
                bead_id: self._bead_show_json(bead_id, bead_status),
            },
        )
        spy_gh_pr_list(fakes_dir, pr_list_response=prs_json)
        spy_python3_stories_archive(fakes_dir)
        _setup_fake_pack_with_notify(fakes_dir)

        result = _invoke(fakes_dir, city_root, enabled=True)
        return result, fakes_dir

    @staticmethod
    def _close_calls(fakes_dir: Path, bead_id: str) -> list[str]:
        log = fakes_dir / "bd-argv.log"
        if not log.exists():
            return []
        return [
            c
            for c in log.read_text().splitlines()
            if "update" in c and bead_id in c and "--status=closed" in c
        ]

    def test_blocked_bead_with_merged_pr_is_closed(self) -> None:
        result, fakes_dir = self._run(bead_status="blocked")
        self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
        # The archive still fired (load-bearing op).
        self.assertTrue(
            (fakes_dir / "stories-archive-argv.log").exists(),
            "archive must still fire for the zombie spec",
        )
        bd_calls = (fakes_dir / "bd-argv.log").read_text()
        self.assertIn("final_state=merged", bd_calls, "final_state=merged stamp must still happen")
        self.assertEqual(
            len(self._close_calls(fakes_dir, "el-243bd")),
            1,
            f"a blocked bead with a merged PR must be closed exactly once; bd calls:\n{bd_calls}",
        )

    def test_in_progress_bead_with_merged_pr_is_not_closed(self) -> None:
        result, fakes_dir = self._run(bead_status="in_progress")
        self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
        bd_calls = (fakes_dir / "bd-argv.log").read_text()
        self.assertEqual(
            self._close_calls(fakes_dir, "el-243bd"),
            [],
            f"an in_progress bead (active re-walk) must NOT be closed; bd calls:\n{bd_calls}",
        )

    def test_open_bead_with_merged_pr_is_not_closed(self) -> None:
        result, fakes_dir = self._run(bead_status="open")
        self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
        bd_calls = (fakes_dir / "bd-argv.log").read_text()
        self.assertEqual(
            self._close_calls(fakes_dir, "el-243bd"),
            [],
            f"an open bead (possibly queued for re-walk) must NOT be closed; bd calls:\n{bd_calls}",
        )


if __name__ == "__main__":
    unittest.main()
