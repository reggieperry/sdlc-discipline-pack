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
            self.assertIn("GC_CITY_ROOT not set", result.stderr)


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


if __name__ == "__main__":
    unittest.main()
