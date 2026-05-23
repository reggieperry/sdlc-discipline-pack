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
    enabled: str | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the watcher with the requested gate setting."""
    env = {
        **os.environ,
        "PATH": f"{fakes_dir}:{os.environ['PATH']}",
        # Pretend a bead.closed event fired so the script reaches the gate.
        "GC_EVENT_SUBJECT": "el-test",
    }
    # Strip whatever the parent shell might have set so the test env is hermetic.
    env.pop("SDLC_REBASE_WATCHER_ENABLED", None)
    if enabled is not None:
        env["SDLC_REBASE_WATCHER_ENABLED"] = enabled
    return subprocess.run(
        [str(SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


class FeatureGateTests(unittest.TestCase):
    """v2.30 removed the SDLC_WATCHER_ENABLED legacy fallback that v2.29.4 had
    shipped as a one-release deprecation. Only the new name remains."""

    def test_false_disables(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            spy_bd_list(tmp)

            result = _invoke(tmp, enabled="false")

            self.assertEqual(result.returncode, 0)
            bd_log = tmp / "bd-argv.log"
            self.assertFalse(
                bd_log.exists(),
                f"bd should not be called when gate disabled; bd-argv.log present: {bd_log}",
            )

    def test_default_true_enables(self) -> None:
        """Default behavior: when neither the gate var nor the legacy var is
        set, the watcher's gate-default is true and the script reaches `bd show`."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            spy_bd_list(tmp)

            result = _invoke(tmp)

            self.assertEqual(result.returncode, 0)
            bd_log = tmp / "bd-argv.log"
            self.assertTrue(bd_log.exists(), "bd should be reached when gate defaults true")
            self.assertIn("show el-test", bd_log.read_text())


if __name__ == "__main__":
    unittest.main()
