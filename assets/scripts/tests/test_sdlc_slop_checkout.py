"""Tests for sdlc-slop-checkout.sh (G0 Arm-C extraction of slop step 3).

Checks out the story's branch: reads ``metadata.branch`` and
``metadata.target`` from the bead via ``bd show --json | jq``, fetches
origin, and checks out the branch. Echoes ``BRANCH=`` / ``TARGET=`` on
stdout so the formula can reuse them.

Black-box subprocess with a ``bd`` dispatch spy (canned bead JSON) and a
``git`` recorder spy on PATH. ``jq`` is real. stdlib-only. Matches pack
convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_sdlc_slop_checkout -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spies import spy_bd_dispatch, spy_recorder  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "sdlc-slop-checkout.sh"


def _bead_json(*, branch: str | None, target: str | None) -> str:
    """A one-element bead array as ``bd show --json`` emits, with the
    metadata keys the script reads. ``None`` omits the key entirely."""
    metadata: dict[str, str] = {}
    if branch is not None:
        metadata["branch"] = branch
    if target is not None:
        metadata["target"] = target
    import json

    return json.dumps([{"metadata": metadata}])


def _invoke(tmp: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{tmp}:{env['PATH']}"
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=tmp,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


class CheckoutHappyPathTests(unittest.TestCase):
    def test_fetches_and_checks_out_branch(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            spy_bd_dispatch(
                tmp, {"EL-1": _bead_json(branch="feat/EL-1", target="develop")}
            )
            spy_recorder(tmp, "git")
            result = _invoke(tmp, "EL-1")
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            git_log = (tmp / "git-argv.log").read_text()
            self.assertIn("fetch origin", git_log, f"git fetch origin not called; log={git_log!r}")
            self.assertIn(
                "checkout --track -B feat/EL-1 origin/feat/EL-1",
                git_log,
                f"branch checkout not called; log={git_log!r}",
            )

    def test_echoes_branch_and_target_on_stdout(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            spy_bd_dispatch(
                tmp, {"EL-1": _bead_json(branch="feat/EL-1", target="develop")}
            )
            spy_recorder(tmp, "git")
            result = _invoke(tmp, "EL-1")
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertIn("BRANCH=feat/EL-1", result.stdout)
            self.assertIn("TARGET=develop", result.stdout)

    def test_target_defaults_to_main_when_unset(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            # No target key in the bead metadata → default "main".
            spy_bd_dispatch(tmp, {"EL-2": _bead_json(branch="feat/EL-2", target=None)})
            spy_recorder(tmp, "git")
            result = _invoke(tmp, "EL-2")
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertIn("TARGET=main", result.stdout)


class CheckoutFailClosedTests(unittest.TestCase):
    def test_missing_story_id_fails(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            spy_bd_dispatch(tmp, {})
            spy_recorder(tmp, "git")
            result = _invoke(tmp)  # no STORY_ID
            self.assertNotEqual(result.returncode, 0, "missing STORY_ID must fail")
            self.assertFalse(
                (tmp / "git-argv.log").exists(),
                "must not touch git without a story id",
            )

    def test_unresolvable_branch_fails(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            # Bead has no branch key → jq yields "null" → must fail closed.
            spy_bd_dispatch(tmp, {"EL-3": _bead_json(branch=None, target="main")})
            spy_recorder(tmp, "git")
            result = _invoke(tmp, "EL-3")
            self.assertNotEqual(
                result.returncode, 0, "unresolvable branch must fail, not silently no-op"
            )
            self.assertFalse(
                (tmp / "git-argv.log").exists(),
                "must not checkout when the branch can't be resolved",
            )


if __name__ == "__main__":
    unittest.main()
