"""Tests for sdlc-stall-recover.sh (pack #79).

Tests drive the bash script via subprocess against real git repos in
tmpdirs. No mocking — real `git init`, real commits, real `git log` to
verify the author identity and the file set.

stdlib-only (`unittest` + subprocess + tempfile).

Run with:

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-stall-recover.sh"
assert SCRIPT_PATH.exists(), f"sdlc-stall-recover.sh not found at {SCRIPT_PATH}"


def _run(
    args: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [str(SCRIPT_PATH), *args],
        cwd=str(cwd),
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
    )


def _git_init_with_baseline(tmp: Path) -> None:
    """Init a fresh repo with a committed baseline so HEAD exists for `reset HEAD`."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp, check=True)
    (tmp / "README.md").write_text("baseline\n")
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=tmp, check=True)


def _git_log_show(tmp: Path) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--name-only", "--pretty=format:%an|%ae|%s|%b"],
        cwd=str(tmp),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _git_changed_files(tmp: Path) -> list[str]:
    """The set of files changed by HEAD, without any commit-message text."""
    out = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        cwd=str(tmp),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.split("\n") if line.strip()]


class ArgValidationTests(unittest.TestCase):
    def test_phase_is_required(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _git_init_with_baseline(tmp_path)
            result = _run([], cwd=tmp_path)
            self.assertEqual(result.returncode, 2)
            self.assertIn("--phase", result.stderr)

    def test_unknown_arg_exits_2(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _git_init_with_baseline(tmp_path)
            result = _run(["--phase", "tester", "--bogus"], cwd=tmp_path)
            self.assertEqual(result.returncode, 2)


class HappyPathTests(unittest.TestCase):
    def test_commits_with_recovery_author_and_phase_subject(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _git_init_with_baseline(tmp_path)
            (tmp_path / "core.py").write_text("changed code\n")
            result = _run(["--phase", "tester"], cwd=tmp_path)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            log = _git_log_show(tmp_path)
            self.assertIn("SDLC Recovery|sdlc-recovery@example.com", log)
            self.assertIn("chore(stall-recovery): wip tester checkpoint", log)
            self.assertIn("core.py", log)

    def test_includes_bead_id_and_note_in_body(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _git_init_with_baseline(tmp_path)
            (tmp_path / "core.py").write_text("changed\n")
            result = _run(
                ["--phase", "reviewer", "--bead-id", "bd-42", "--note", "Mode B"],
                cwd=tmp_path,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            log = _git_log_show(tmp_path)
            self.assertIn("Bead: bd-42", log)
            self.assertIn("Mode B", log)


class ExclusionTests(unittest.TestCase):
    def test_default_excludes_claude_settings_json(self) -> None:
        """The canonical pack #79 case: settings.json drift must not land."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _git_init_with_baseline(tmp_path)
            (tmp_path / ".claude").mkdir()
            (tmp_path / ".claude" / "settings.json").write_text('{"drift": "worktree-local"}\n')
            (tmp_path / "core.py").write_text("legit change\n")
            result = _run(["--phase", "tester"], cwd=tmp_path)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            files = _git_changed_files(tmp_path)
            self.assertIn("core.py", files)
            self.assertNotIn(".claude/settings.json", files)

    def test_excluded_files_noted_in_commit_body(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _git_init_with_baseline(tmp_path)
            (tmp_path / ".claude").mkdir()
            (tmp_path / ".claude" / "settings.json").write_text("{}\n")
            (tmp_path / "core.py").write_text("legit\n")
            result = _run(["--phase", "implement"], cwd=tmp_path)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            log = _git_log_show(tmp_path)
            self.assertIn("Worktree-local changes left out", log)
            self.assertIn(".claude/settings.json", log)

    def test_env_extends_exclusion_list(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _git_init_with_baseline(tmp_path)
            (tmp_path / "secrets.env").write_text("KEY=x\n")
            (tmp_path / "core.py").write_text("legit\n")
            result = _run(
                ["--phase", "tester"],
                cwd=tmp_path,
                env={"SDLC_STALL_RECOVERY_EXCLUDES": "secrets.env"},
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            files = _git_changed_files(tmp_path)
            self.assertIn("core.py", files)
            self.assertNotIn("secrets.env", files)


class NothingToCommitTests(unittest.TestCase):
    def test_only_excluded_changes_exits_3(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _git_init_with_baseline(tmp_path)
            (tmp_path / ".claude").mkdir()
            (tmp_path / ".claude" / "settings.json").write_text('{"only drift"}\n')
            result = _run(["--phase", "tester"], cwd=tmp_path)
            self.assertEqual(result.returncode, 3, msg=result.stderr)
            self.assertIn("nothing to commit after exclusions", result.stderr)

    def test_clean_tree_exits_3(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _git_init_with_baseline(tmp_path)
            result = _run(["--phase", "tester"], cwd=tmp_path)
            self.assertEqual(result.returncode, 3)


class DryRunTests(unittest.TestCase):
    def test_dry_run_does_not_commit(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _git_init_with_baseline(tmp_path)
            (tmp_path / "core.py").write_text("change\n")
            result = _run(["--phase", "tester", "--dry-run"], cwd=tmp_path)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("dry-run", result.stdout)
            # No new commit beyond baseline.
            count = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=str(tmp_path),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(count, "1")

    def test_dry_run_lists_staged_and_excluded(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _git_init_with_baseline(tmp_path)
            (tmp_path / ".claude").mkdir()
            (tmp_path / ".claude" / "settings.json").write_text("{}\n")
            (tmp_path / "core.py").write_text("change\n")
            result = _run(["--phase", "tester", "--dry-run"], cwd=tmp_path)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("staged files:", result.stdout)
            self.assertIn("core.py", result.stdout)
            self.assertIn("excluded (had changes):", result.stdout)
            self.assertIn(".claude/settings.json", result.stdout)


if __name__ == "__main__":
    unittest.main()
