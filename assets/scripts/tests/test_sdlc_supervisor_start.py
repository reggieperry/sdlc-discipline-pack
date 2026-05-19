"""Tests for sdlc-supervisor-start.sh.

The script wraps `gc supervisor start` with PATH-resolution that sources
the user's .profile before exec. Tests run the script with a synthetic
HOME (custom .profile with known PATH entries) and a stub gc binary
substituted via SDLC_SUPERVISOR_GC. The stub writes its received args +
PATH to a sentinel file so the test inspects what would have reached gc.

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

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-supervisor-start.sh"
assert SCRIPT_PATH.exists(), f"sdlc-supervisor-start.sh not found at {SCRIPT_PATH}"


def _run(
    args: list[str],
    *,
    env_overrides: dict[str, str] | None = None,
    minimal_env: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Invoke the script. By default uses a minimal PATH so the test's
    real env doesn't mask whether the script's own resolution works.
    """
    env: dict[str, str] = (
        {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": str(Path.home()),
        }
        if minimal_env
        else dict(os.environ)
    )
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(SCRIPT_PATH), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _make_home_with_profile(tmp: Path, profile_body: str) -> Path:
    """Build a synthetic HOME with a .profile that the script will source."""
    home = tmp / "home"
    home.mkdir()
    (home / ".profile").write_text(profile_body)
    return home


def _make_stub_gc(tmp: Path, sentinel: Path) -> Path:
    """Write a fake gc binary that records its argv + PATH to a sentinel file
    and exits 0. The wrapper exec's this; the sentinel survives the exec.
    """
    stub = tmp / "fake-gc"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "argv=%s\\n" "$*" > {sentinel}\n'
        f'printf "PATH=%s\\n" "$PATH" >> {sentinel}\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    return stub


class CheckModeTests(unittest.TestCase):
    """--check prints resolution without invoking gc."""

    def test_check_prints_path_and_tools(self) -> None:
        result = _run(["--check"], minimal_env=False)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("PATH=", result.stdout)
        self.assertIn("gc:", result.stdout)
        self.assertIn("uv:", result.stdout)
        self.assertIn("bd:", result.stdout)

    def test_check_does_not_exec_gc(self) -> None:
        """--check exits cleanly even if gc resolution would have failed."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = _make_home_with_profile(tmp_path, "# minimal\n")
            result = _run(
                ["--check"],
                env_overrides={"HOME": str(home), "SDLC_SUPERVISOR_GC": "/no/such/gc"},
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("<not found>", result.stdout)


class ProfileSourcingTests(unittest.TestCase):
    def test_profile_path_entry_appears_in_resolved_path(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = _make_home_with_profile(tmp_path, 'export PATH="/custom/from/profile:$PATH"\n')
            result = _run(
                ["--check"],
                env_overrides={"HOME": str(home)},
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("/custom/from/profile", result.stdout)

    def test_missing_profile_does_not_crash(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            # No .profile written.
            result = _run(
                ["--check"],
                env_overrides={"HOME": str(home)},
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            # Belt-and-suspenders still adds ~/.local/bin.
            self.assertIn(f"{home}/.local/bin", result.stdout)

    def test_command_failure_in_profile_is_swallowed(self) -> None:
        """A .profile whose commands return non-zero must not kill supervisor startup.

        Explicit `exit N` in the sourced file is NOT swallowed (sourcing
        propagates exit) — that's an operator-intentional terminator, not
        a bug. This test covers the realistic case: a command in .profile
        fails (missing file, unknown command), and the script continues.
        """
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = _make_home_with_profile(
                tmp_path,
                "false\nthis_command_does_not_exist_anywhere\n",
            )
            result = _run(
                ["--check"],
                env_overrides={"HOME": str(home)},
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)


class LocalBinBeltAndSuspendersTests(unittest.TestCase):
    def test_local_bin_prepended_when_profile_omits_it(self) -> None:
        """Even if .profile doesn't add ~/.local/bin, the script does."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = _make_home_with_profile(tmp_path, "# .profile that does not touch PATH\n")
            result = _run(
                ["--check"],
                env_overrides={"HOME": str(home)},
            )
            self.assertIn(f"{home}/.local/bin", result.stdout)

    def test_local_bin_not_duplicated_when_already_present(self) -> None:
        """If .profile already adds ~/.local/bin, the script must not add it again."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = _make_home_with_profile(tmp_path, 'export PATH="$HOME/.local/bin:$PATH"\n')
            result = _run(
                ["--check"],
                env_overrides={"HOME": str(home)},
            )
            # Count exact occurrences of $HOME/.local/bin in the printed PATH line.
            path_line = [
                line for line in result.stdout.splitlines() if line.strip().startswith("PATH=")
            ][0]
            count = path_line.count(f"{home}/.local/bin")
            self.assertEqual(count, 1, f"expected one occurrence, got {count}: {path_line!r}")


class GcNotFoundTests(unittest.TestCase):
    def test_missing_gc_exits_2_with_helpful_error(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = _make_home_with_profile(tmp_path, "# minimal\n")
            result = _run(
                [],
                env_overrides={
                    "HOME": str(home),
                    "SDLC_SUPERVISOR_GC": "/no/such/binary",
                },
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("not found on PATH", result.stderr)
            self.assertIn("hint:", result.stderr)


class ExecHandoffTests(unittest.TestCase):
    """The script exec's gc; tests verify what reaches it via a stub binary."""

    def test_stub_gc_receives_supervisor_start(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = _make_home_with_profile(tmp_path, "# minimal\n")
            sentinel = tmp_path / "sentinel.txt"
            stub = _make_stub_gc(tmp_path, sentinel)
            result = _run(
                [],
                env_overrides={
                    "HOME": str(home),
                    "SDLC_SUPERVISOR_GC": str(stub),
                },
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertTrue(sentinel.exists(), msg="stub did not run")
            content = sentinel.read_text()
            self.assertIn("argv=supervisor start", content)

    def test_extra_args_are_forwarded(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = _make_home_with_profile(tmp_path, "# minimal\n")
            sentinel = tmp_path / "sentinel.txt"
            stub = _make_stub_gc(tmp_path, sentinel)
            result = _run(
                ["--foo", "bar"],
                env_overrides={
                    "HOME": str(home),
                    "SDLC_SUPERVISOR_GC": str(stub),
                },
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            content = sentinel.read_text()
            self.assertIn("argv=supervisor start --foo bar", content)

    def test_resolved_path_reaches_gc(self) -> None:
        """The PATH the stub sees includes profile-added entries."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = _make_home_with_profile(tmp_path, 'export PATH="/from/profile:$PATH"\n')
            sentinel = tmp_path / "sentinel.txt"
            stub = _make_stub_gc(tmp_path, sentinel)
            result = _run(
                [],
                env_overrides={
                    "HOME": str(home),
                    "SDLC_SUPERVISOR_GC": str(stub),
                },
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            content = sentinel.read_text()
            self.assertIn("/from/profile", content)
            self.assertIn(f"{home}/.local/bin", content)


if __name__ == "__main__":
    unittest.main()
