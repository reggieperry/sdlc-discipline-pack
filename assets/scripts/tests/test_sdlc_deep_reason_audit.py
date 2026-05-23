"""Tests for sdlc-deep-reason-audit.sh (pack #125).

Black-box subprocess tests. Each test stands up a tempdir with a fake gc
(via spy_gc_rig_list) returning a contrived rig list, a fake notify under
the fake-pack layout, and the rig has a real git repo with tags so the
audit can compute the window.

stdlib-only. Matches pack convention.

Run with::

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _spies import spy_gc_rig_list, spy_notify

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-deep-reason-audit.sh"
assert SCRIPT_PATH.exists(), f"sdlc-deep-reason-audit.sh not found at {SCRIPT_PATH}"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _setup_rig_with_tags(tmp: Path, rig_name: str, tags: list[str]) -> Path:
    """Stand up a git-initialized rig with the given tags (oldest first)."""
    city_root = tmp / "city"
    rig_root = city_root / rig_name
    rig_root.mkdir(parents=True)
    _git(rig_root, "init", "-q", "-b", "main")
    for i, tag in enumerate(tags):
        (rig_root / f"file-{i}.txt").write_text(f"content {i}\n")
        _git(rig_root, "add", ".")
        _git(rig_root, "commit", "-q", "-m", f"commit {i}")
        _git(rig_root, "tag", tag)
    return rig_root


def _invoke(
    tmp: Path,
    rig_name: str,
    rig_root: Path,
    *,
    enabled: str | None = None,
    tags_window: str | None = None,
) -> subprocess.CompletedProcess:
    fakes_dir = tmp / "fakes"
    fakes_dir.mkdir(parents=True, exist_ok=True)

    rig_list_json = json.dumps(
        {"rigs": [{"name": rig_name, "path": str(rig_root), "hq": False, "suspended": False}]}
    )
    spy_gc_rig_list(fakes_dir, rig_list_json)
    spy_notify(fakes_dir)

    pack_dir = fakes_dir / "fake-pack"

    env = {
        **os.environ,
        "PATH": f"{fakes_dir}:{os.environ.get('PATH', '')}",
        "PACK_DIR": str(pack_dir),
        "GC_CITY_ROOT": str(tmp / "city"),
    }
    env.pop("SDLC_DEEP_REASON_AUDIT_ENABLED", None)
    if enabled is not None:
        env["SDLC_DEEP_REASON_AUDIT_ENABLED"] = enabled
    if tags_window is not None:
        env["SDLC_DEEP_REASON_AUDIT_TAGS"] = tags_window

    return subprocess.run(
        [str(SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class FeatureGateTests(unittest.TestCase):
    def test_disabled_by_default_no_op(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            rig_root = _setup_rig_with_tags(tmp, "alpha", ["v2.29.1", "v2.29.2"])
            result = _invoke(tmp, "alpha", rig_root)
            self.assertEqual(result.returncode, 0)
            # No prompt file should be written when the gate is off.
            reviews_dir = rig_root / "reviews"
            if reviews_dir.exists():
                self.assertEqual(
                    list(reviews_dir.glob("deep-reason-audit-*.md")),
                    [],
                    "no prompt should be written when gate is disabled",
                )
            self.assertFalse((rig_root / ".gc").exists())


class AuditGenerationTests(unittest.TestCase):
    def test_fresh_window_writes_prompt_state_and_notifies(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            rig_root = _setup_rig_with_tags(tmp, "alpha", ["v2.29.1", "v2.29.2", "v2.29.3"])
            result = _invoke(tmp, "alpha", rig_root, enabled="true", tags_window="3")

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            # Prompt file written
            prompts = list((rig_root / "reviews").glob("deep-reason-audit-*.md"))
            self.assertEqual(len(prompts), 1, f"expected one prompt, got {prompts}")
            prompt_text = prompts[0].read_text()
            self.assertIn("v2.29.3", prompt_text)
            self.assertIn("v2.29.1..HEAD", prompt_text)
            # State bookmark written with the latest tag
            state_file = rig_root / ".gc" / "deep-reason-audit-state.json"
            self.assertTrue(state_file.exists())
            state = json.loads(state_file.read_text())
            self.assertEqual(state["last_audited_tag"], "v2.29.3")
            # Notify called
            notify_log = tmp / "fakes" / "notify-argv.log"
            self.assertTrue(notify_log.exists(), "notify should fire when audit is generated")
            self.assertIn("alpha", notify_log.read_text())

    def test_idempotent_when_latest_tag_already_audited(self) -> None:
        """Re-fire after bookmarking the latest tag should be a no-op."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            rig_root = _setup_rig_with_tags(tmp, "alpha", ["v2.29.1", "v2.29.2"])
            # Pre-bookmark the latest tag.
            (rig_root / ".gc").mkdir()
            (rig_root / ".gc" / "deep-reason-audit-state.json").write_text(
                json.dumps({"last_audited_tag": "v2.29.2", "last_audit_date": "2026-01-01"})
            )

            result = _invoke(tmp, "alpha", rig_root, enabled="true")

            self.assertEqual(result.returncode, 0)
            # No new prompt
            prompts = list((rig_root / "reviews").glob("deep-reason-audit-*.md"))
            self.assertEqual(prompts, [], "re-audit at bookmarked tag should be a no-op")
            # No notify
            notify_log = tmp / "fakes" / "notify-argv.log"
            self.assertFalse(notify_log.exists(), "notify should not fire on idempotent re-audit")

    def test_no_tags_skips_cleanly(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            # No tags — repo has zero tags.
            city_root = tmp / "city"
            rig_root = city_root / "alpha"
            rig_root.mkdir(parents=True)
            _git(rig_root, "init", "-q", "-b", "main")
            (rig_root / "init.txt").write_text("init\n")
            _git(rig_root, "add", ".")
            _git(rig_root, "commit", "-q", "-m", "init")

            result = _invoke(tmp, "alpha", rig_root, enabled="true")

            self.assertEqual(result.returncode, 0)
            self.assertFalse(
                (rig_root / "reviews").exists(),
                "rig with no tags should not produce an audit prompt",
            )


if __name__ == "__main__":
    unittest.main()
