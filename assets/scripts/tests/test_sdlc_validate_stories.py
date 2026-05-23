"""Tests for sdlc-validate-stories.sh (pack#90).

Black-box subprocess tests. Each test stands up a tempdir mocked as a rig
with stories/, .claude/sdlc-discipline/stories.py (a fake bridge), and a
git repo for the staged-diff gate. Asserts the script's exit code and
invocation pattern against the bridge.

stdlib-only (unittest + tempfile + subprocess + textwrap). Matches pack
convention.

Run with:
    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import os
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _spies import write_executable

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-validate-stories.sh"
assert SCRIPT_PATH.exists(), f"sdlc-validate-stories.sh not found at {SCRIPT_PATH}"


def _make_rig(tmp: Path, bridge_exit: int = 0, bridge_stdout: str = "") -> None:
    """Stand up a fake rig: stories/, .claude/sdlc-discipline/stories.py, git init.

    The fake stories.py records its invocation argv to <tmp>/bridge-argv.log
    and exits with bridge_exit. The script under test should locate this
    bridge via the walk-up and invoke it with `validate` as argv.
    """
    (tmp / "stories").mkdir()
    (tmp / ".claude" / "sdlc-discipline").mkdir(parents=True)

    bridge = tmp / ".claude" / "sdlc-discipline" / "stories.py"
    bridge_body = textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import sys
        with open("{tmp}/bridge-argv.log", "a") as f:
            f.write(" ".join(sys.argv) + "\\n")
        sys.stdout.write({bridge_stdout!r})
        sys.exit({bridge_exit})
        """
    )
    write_executable(bridge, bridge_body)

    # Initialize a git repo so the staged-diff gate has something to query.
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "config", "commit.gpgsign", "false"],
        cwd=tmp,
        check=True,
    )


def _stage_file(tmp: Path, relpath: str, content: str = "x") -> None:
    p = tmp / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    subprocess.run(["git", "add", relpath], cwd=tmp, check=True)


class PreCommitGateTests(unittest.TestCase):
    """When invoked as a pre-commit hook, run only on stories/*.md staged changes."""

    def test_no_stories_diff_exits_clean_without_invoking_bridge(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_rig(tmp, bridge_exit=1)  # bridge would fail if invoked

            _stage_file(tmp, "src/foo.py", "print('hi')\n")

            result = subprocess.run(
                [str(SCRIPT_PATH)],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(
                result.returncode,
                0,
                f"no stories diff → exit 0; stderr={result.stderr!r}",
            )
            self.assertFalse(
                (tmp / "bridge-argv.log").exists(),
                "no stories diff → bridge must not be invoked",
            )

    def test_stories_diff_invokes_bridge_validate(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_rig(tmp, bridge_exit=0)

            _stage_file(tmp, "stories/EL-999-test.md", "---\nstory_id: EL-999\n---\n")

            result = subprocess.run(
                [str(SCRIPT_PATH)],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertTrue((tmp / "bridge-argv.log").exists(), "stories diff → bridge invoked")
            log = (tmp / "bridge-argv.log").read_text().strip()
            self.assertIn("validate", log, f"bridge should be called with 'validate'; got {log!r}")

    def test_stories_diff_propagates_bridge_failure(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_rig(tmp, bridge_exit=1, bridge_stdout="FAIL: out-of-schema status\n")

            _stage_file(tmp, "stories/EL-998-bad.md", "---\nstatus: shipped\n---\n")

            result = subprocess.run(
                [str(SCRIPT_PATH)],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertNotEqual(
                result.returncode,
                0,
                "validate failure must propagate as non-zero exit",
            )


class ForceModeTests(unittest.TestCase):
    """SDLC_VALIDATE_STORIES_FORCE=1 bypasses the staged-diff gate (chain self-audit)."""

    def test_force_mode_runs_regardless_of_staged_diff(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_rig(tmp, bridge_exit=0)
            # No stories diff staged; pre-commit mode would skip.
            _stage_file(tmp, "src/foo.py", "x\n")

            env = {**os.environ, "SDLC_VALIDATE_STORIES_FORCE": "1"}
            result = subprocess.run(
                [str(SCRIPT_PATH)],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertTrue(
                (tmp / "bridge-argv.log").exists(),
                "force mode → bridge invoked regardless of staged diff",
            )


class BridgeDiscoveryTests(unittest.TestCase):
    """The walk-up resolution finds the bridge via .claude/sdlc-discipline/stories.py."""

    def test_missing_bridge_exits_clean(self) -> None:
        """No .claude/sdlc-discipline/ → not our hook to enforce. Exit 0."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
            (tmp / "stories").mkdir()
            _stage_file(tmp, "stories/EL-1-x.md", "x")

            result = subprocess.run(
                [str(SCRIPT_PATH)],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(
                result.returncode,
                0,
                f"missing bridge → exit clean; stderr={result.stderr!r}",
            )

    def test_finds_bridge_from_subdirectory(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _make_rig(tmp, bridge_exit=0)
            (tmp / "deep" / "nested" / "dir").mkdir(parents=True)
            _stage_file(tmp, "stories/EL-7-x.md", "x")

            result = subprocess.run(
                [str(SCRIPT_PATH)],
                cwd=tmp / "deep" / "nested" / "dir",
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertTrue(
                (tmp / "bridge-argv.log").exists(),
                "walk-up should find the bridge from a subdirectory",
            )


if __name__ == "__main__":
    unittest.main()
