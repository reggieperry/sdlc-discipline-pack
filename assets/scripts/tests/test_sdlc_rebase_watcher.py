"""Tests for sdlc-rebase-watcher.sh — focused on the v2.29.4 env-var rename.

Pre-v2.29.4 the watcher's feature gate read ``SDLC_WATCHER_ENABLED``, which
broke the ``SDLC_<SHORTNAME>_ENABLED`` convention used by the other detector
scripts. v2.29.4 renames it to ``SDLC_REBASE_WATCHER_ENABLED`` and keeps
the legacy name honored-with-warning for one release.

These tests pin the gate's three branches: new name disables, legacy name
still disables (with a stderr warning), and new name wins when both are
set. They use minimal fakes — only enough to reach (or skip) the first
substantive call (`bd show`); the watcher's downstream rebase logic is
not exercised here.

stdlib-only (unittest + tempfile + subprocess + textwrap). Matches pack
convention.

Run with::

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _spies import spy_bd_list

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-rebase-watcher.sh"
assert SCRIPT_PATH.exists(), f"sdlc-rebase-watcher.sh not found at {SCRIPT_PATH}"

# The watcher's gate-passed path reaches `bd show "$BEAD_ID"` and exits at the
# `[ -z "$BEAD_JSON" ] && exit 0` guard when bd returns nothing. `spy_bd_list(tmp)`
# (default empty `[]` list-response, silent exit-0 on every other subcommand) gives
# exactly that shape — argv is logged to bd-argv.log so the gate tests can assert
# "bd was never called" via the log's presence/absence.


def _invoke(
    fakes_dir: Path,
    *,
    new_var: str | None = None,
    legacy_var: str | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the watcher with the requested env-var permutation."""
    env = {
        **os.environ,
        "PATH": f"{fakes_dir}:{os.environ['PATH']}",
        # Pretend a bead.closed event fired so the script reaches the gate.
        "GC_EVENT_SUBJECT": "el-test",
    }
    # Strip whatever the parent shell might have set so the test env is hermetic.
    env.pop("SDLC_REBASE_WATCHER_ENABLED", None)
    env.pop("SDLC_WATCHER_ENABLED", None)
    if new_var is not None:
        env["SDLC_REBASE_WATCHER_ENABLED"] = new_var
    if legacy_var is not None:
        env["SDLC_WATCHER_ENABLED"] = legacy_var
    return subprocess.run(
        [str(SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


class FeatureGateTests(unittest.TestCase):
    def test_new_name_false_disables(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            spy_bd_list(tmp)

            result = _invoke(tmp, new_var="false")

            self.assertEqual(result.returncode, 0)
            self.assertNotIn("deprecated", result.stderr)
            bd_log = tmp / "bd-argv.log"
            self.assertFalse(
                bd_log.exists(),
                f"bd should not be called when gate disabled; bd-argv.log present: {bd_log}",
            )

    def test_legacy_name_false_still_disables_with_warning(self) -> None:
        """v2.29.4 backward-compat: SDLC_WATCHER_ENABLED=false still gates the
        script off, AND a stderr warning fires telling the operator the
        legacy name is deprecated."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            spy_bd_list(tmp)

            result = _invoke(tmp, legacy_var="false")

            self.assertEqual(result.returncode, 0)
            self.assertIn("SDLC_WATCHER_ENABLED is deprecated", result.stderr)
            self.assertIn("SDLC_REBASE_WATCHER_ENABLED", result.stderr)
            self.assertFalse(
                (tmp / "bd-argv.log").exists(),
                "legacy var should still disable; bd-argv.log unexpectedly present",
            )

    def test_new_name_wins_when_both_set(self) -> None:
        """Precedence pin: if the operator sets BOTH the new and the legacy
        var, the new one wins. Specifically: legacy=false but new=true →
        gate passes (and no warning fires, since the new var is non-empty,
        so the deprecation branch isn't entered)."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            spy_bd_list(tmp)

            result = _invoke(tmp, new_var="true", legacy_var="false")

            self.assertEqual(result.returncode, 0)
            # Warning should NOT fire — new var is set, so the legacy fallback
            # branch is skipped entirely.
            self.assertNotIn("deprecated", result.stderr)
            # bd should have been called once (the gate let the script through).
            bd_log = tmp / "bd-argv.log"
            self.assertTrue(bd_log.exists(), "bd should be reached when gate passes")
            self.assertIn("show el-test", bd_log.read_text())


if __name__ == "__main__":
    unittest.main()
