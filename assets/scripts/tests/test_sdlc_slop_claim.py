"""Tests for sdlc-slop-claim.sh (G0 Arm-C extraction of slop step 1).

Finds a bead claimable by this slop-reviewer session, claims it, and
echoes the claimed story/bead id to stdout (the formula captures it).
The work-finding query mirrors the prompt's "How you receive work" block:

  1. ``gc bd list --assignee="$GC_SESSION_NAME" --status=in_progress``
     (crash recovery — work already claimed by this session)
  2. routed-to query — ready, unassigned beads routed to this template
     (``gc.routed_to=<rig>/sdlc-discipline.slop-reviewer``)

then ``gc bd update <bead-id> --claim``.

Black-box subprocess with a ``gc`` spy on PATH that returns canned
``gc bd list --json`` output per query and records every argv (so the
``--claim`` call is assertable). jq is real. stdlib-only. Matches pack
convention (``test_sdlc_alive_idle_detector.py`` inline-spy style).

Run with::

    python3 -m unittest assets.scripts.tests.test_sdlc_slop_claim -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spies import write_executable  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "sdlc-slop-claim.sh"

SESSION_NAME = "sdlc-discipline__slop-reviewer-sr-test"
RIG = "elder"


def _spy_gc(tmp: Path, *, assigned_json: str, routed_json: str) -> Path:
    """Fake ``gc`` that dispatches on the two work-finding queries.

    Returns ``routed_json`` for the routed-to query (distinguished by
    the ``gc.routed_to`` token in argv) and ``assigned_json`` for the
    assignee/in_progress query (everything else). Records all argv to
    ``<tmp>/gc-argv.log`` so the ``bd update <id> --claim`` call is
    assertable. ``gc bd update`` exits 0.
    """
    path = tmp / "gc"
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/gc-argv.log"\n'
        'if [ "$1" = "bd" ] && [ "$2" = "list" ]; then\n'
        "    shift 2\n"
        '    case "$*" in\n'
        "        *gc.routed_to=*)\n"
        "            cat <<'__ROUTED_EOF__'\n"
        f"{routed_json}\n"
        "__ROUTED_EOF__\n"
        "            ;;\n"
        "        *)\n"
        "            cat <<'__ASSIGNED_EOF__'\n"
        f"{assigned_json}\n"
        "__ASSIGNED_EOF__\n"
        "            ;;\n"
        "    esac\n"
        "    exit 0\n"
        "fi\n"
        # Real `gc bd update --claim` prints "✓ Updated issue: <id> — <title>" to
        # stdout; replicate it so the test catches stdout pollution of the echoed id.
        'if [ "$1" = "bd" ] && [ "$2" = "update" ]; then\n'
        '    echo "✓ Updated issue: $3 — claimed"\n'
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    write_executable(path, body)
    return path


def _bead(bead_id: str) -> dict:
    return {"id": bead_id, "status": "open", "assignee": "", "metadata": {}}


def _invoke(tmp: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{tmp}:{env['PATH']}"
    env.setdefault("GC_SESSION_NAME", SESSION_NAME)
    env.setdefault("GC_RIG", RIG)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(SCRIPT)],
        cwd=tmp,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


class ClaimTests(unittest.TestCase):
    def test_claims_already_assigned_bead_and_echoes_id(self) -> None:
        """Tier 1: a bead already in_progress for this session is claimed + echoed."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _spy_gc(tmp, assigned_json=json.dumps([_bead("EL-7")]), routed_json="[]")
            result = _invoke(tmp)
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertEqual(result.stdout.strip(), "EL-7", f"stdout={result.stdout!r}")
            log = (tmp / "gc-argv.log").read_text()
            self.assertIn("bd update EL-7 --claim", log, f"claim not issued; log={log!r}")

    def test_claims_routed_bead_when_none_assigned(self) -> None:
        """Tier 2: no assigned work, but a routed-to bead is available → claim it."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _spy_gc(tmp, assigned_json="[]", routed_json=json.dumps([_bead("EL-9")]))
            result = _invoke(tmp)
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
            self.assertEqual(result.stdout.strip(), "EL-9", f"stdout={result.stdout!r}")
            log = (tmp / "gc-argv.log").read_text()
            self.assertIn("bd update EL-9 --claim", log, f"claim not issued; log={log!r}")
            self.assertIn("sdlc-discipline.slop-reviewer", log, f"routed query missing; log={log!r}")
            # Pin the real bd filter flag: `bd list` has no `--metadata`; the routed-to
            # filter is `--metadata-field key=value`. A regression to the wrong flag must fail.
            self.assertIn("--metadata-field gc.routed_to=", log, f"tier-2 must filter via --metadata-field; log={log!r}")

    def test_no_work_fails_nonzero_and_does_not_claim(self) -> None:
        """Both queries empty → exit non-zero, echo nothing, never call --claim."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _spy_gc(tmp, assigned_json="[]", routed_json="[]")
            result = _invoke(tmp)
            self.assertNotEqual(result.returncode, 0, "no work must fail-closed, not no-op")
            self.assertEqual(result.stdout.strip(), "", f"must echo nothing; stdout={result.stdout!r}")
            log = (tmp / "gc-argv.log").read_text()
            self.assertNotIn("--claim", log, f"must not claim when no work found; log={log!r}")

    def test_missing_session_name_fails(self) -> None:
        """No GC_SESSION_NAME in env → fail-closed (cannot scope the assignee query)."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _spy_gc(tmp, assigned_json="[]", routed_json="[]")
            result = _invoke(tmp, env_extra={"GC_SESSION_NAME": ""})
            self.assertNotEqual(result.returncode, 0, "missing GC_SESSION_NAME must fail, not silently no-op")


if __name__ == "__main__":
    unittest.main()
