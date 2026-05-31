"""Tests for stories.py cmd_validate same-file co-location warning (pack #213 v1).

Symptom: filing a batch of ready stories that touch the same file as parallel
deps=[] opens N simultaneous PRs that collide at merge (the 2026-05-30 7-PR
slop wave: all touched tests/unit/test_slop_analysis.py -> 6 manual rebases).

v1 surfaces the risk at validate time: when two `ready` stories both list the
same `sensitive_files` entry and declare no dep edge between them (direct or
transitive), `stories validate` emits a NON-FATAL warning suggesting a
predecessor-first `deps:` edge. It never acts and never fails the validation —
sensitive_files is a co-location hint, not a verified write-write conflict
(per the #213 design: the reviewer-phase changed-files check is the deferred,
higher-value successor that operates on real write info).

Black-box subprocess tests: a tempdir rig with a stories/ dir and a few specs,
run `stories.py validate`, assert on exit code + stderr. No bd needed (validate
does not touch bd). stdlib-only; matches pack convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_stories_validate_colocation -v
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STORIES_PY = (
    Path(__file__).resolve().parents[3]
    / "overlay"
    / "per-provider"
    / "claude"
    / ".claude"
    / "sdlc-discipline"
    / "stories.py"
)
assert STORIES_PY.exists(), f"stories.py not found at {STORIES_PY}"


def _spec(story_id: str, sensitive: list[str] | None = None, deps: list[str] | None = None) -> str:
    lines = [
        "---",
        f"story_id: {story_id}",
        f"title: {story_id} title",
        "phase: 3",
        "status: ready",
    ]
    if deps:
        lines.append("deps:")
        lines += [f"  - {d}" for d in deps]
    if sensitive:
        lines.append("sensitive_files:")
        lines += [f"  - {s}" for s in sensitive]
    lines += ["---", "", "# body", ""]
    return "\n".join(lines)


def _make_rig(tmp: Path, specs: dict[str, str]) -> Path:
    rig = tmp / "rig"
    stories = rig / "stories"
    stories.mkdir(parents=True)
    for story_id, body in specs.items():
        (stories / f"{story_id}-slug.md").write_text(body)
    return rig


def _run_validate(rig: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(STORIES_PY), "validate"],
        cwd=rig,
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=15,
    )


class ColocationWarningTests(unittest.TestCase):
    def test_shared_file_no_dep_warns_non_fatally(self) -> None:
        with TemporaryDirectory() as tmp_str:
            rig = _make_rig(
                Path(tmp_str),
                {
                    "EL-301": _spec("EL-301", sensitive=["core/state.py"]),
                    "EL-302": _spec("EL-302", sensitive=["core/state.py"]),
                },
            )
            result = _run_validate(rig)
            out = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, f"co-location is non-fatal; out={out!r}")
            self.assertIn("WARN", out, "expected a non-fatal warning")
            self.assertIn("EL-301", out)
            self.assertIn("EL-302", out)
            self.assertIn("core/state.py", out, "the warning must name the shared file")

    def test_shared_file_with_dep_does_not_warn(self) -> None:
        with TemporaryDirectory() as tmp_str:
            rig = _make_rig(
                Path(tmp_str),
                {
                    "EL-301": _spec("EL-301", sensitive=["core/state.py"]),
                    "EL-302": _spec("EL-302", sensitive=["core/state.py"], deps=["EL-301"]),
                },
            )
            result = _run_validate(rig)
            self.assertEqual(result.returncode, 0)
            self.assertNotIn("WARN", result.stdout + result.stderr, "dep edge present → no warning")

    def test_transitive_dep_does_not_warn(self) -> None:
        """EL-303 -> EL-302 -> EL-301; EL-301 and EL-303 share a file but are
        transitively connected, so they cannot collide — no warning."""
        with TemporaryDirectory() as tmp_str:
            rig = _make_rig(
                Path(tmp_str),
                {
                    "EL-301": _spec("EL-301", sensitive=["core/state.py"]),
                    "EL-302": _spec("EL-302", sensitive=["x.py"], deps=["EL-301"]),
                    "EL-303": _spec("EL-303", sensitive=["core/state.py"], deps=["EL-302"]),
                },
            )
            result = _run_validate(rig)
            self.assertEqual(result.returncode, 0)
            self.assertNotIn("WARN", result.stdout + result.stderr, "transitive dep → no collision")

    def test_disjoint_files_do_not_warn(self) -> None:
        with TemporaryDirectory() as tmp_str:
            rig = _make_rig(
                Path(tmp_str),
                {
                    "EL-301": _spec("EL-301", sensitive=["core/a.py"]),
                    "EL-302": _spec("EL-302", sensitive=["core/b.py"]),
                },
            )
            result = _run_validate(rig)
            self.assertEqual(result.returncode, 0)
            self.assertNotIn("WARN", result.stdout + result.stderr, "no shared file → no warning")


if __name__ == "__main__":
    unittest.main()
