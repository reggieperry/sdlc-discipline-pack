"""Tests for sdlc-list-rigs.sh — the single source of truth for "list
active rigs" across the four cron-triggered scripts that enumerate
non-HQ, non-suspended rigs (stale-pr-sweeper, zombie-reconciler,
exhausted-bead-retry, stall-detector).

Pre-extraction those four scripts each rolled their own
`gc rig list --json | jq ... select(...)` block, and one of them
filtered on `is_hq` instead of `hq` — silently skipping no HQ rigs.
This library consolidates the four into one place; the test suite
pins the dual-shape filter (`hq` OR `is_hq`) so the next gc schema
drift is caught.

Black-box subprocess pattern matching the rest of the pack's bash-
script tests. Uses a recording-fake `gc` to control the rig list
JSON payload.

stdlib-only (unittest + tempfile + subprocess + textwrap).

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

from _spies import spy_gc_rig_list

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "lib" / "sdlc-list-rigs.sh"
assert SCRIPT_PATH.exists(), f"sdlc-list-rigs.sh not found at {SCRIPT_PATH}"


def _invoke(fakes_dir: Path, city_root: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{fakes_dir}:{os.environ['PATH']}",
        "SDLC_LIST_RIGS_GC": str(fakes_dir / "gc"),
    }
    return subprocess.run(
        [str(SCRIPT_PATH), str(city_root)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _setup(tmp: Path) -> tuple[Path, Path]:
    city_root = tmp / "city"
    fakes_dir = tmp / "fakes"
    city_root.mkdir()
    fakes_dir.mkdir()
    return city_root, fakes_dir


def _rig(
    name: str,
    path: str,
    *,
    hq: bool | None = False,
    is_hq: bool | None = None,
    suspended: bool = False,
) -> dict:
    out: dict = {"name": name, "path": path, "suspended": suspended}
    if hq is not None:
        out["hq"] = hq
    if is_hq is not None:
        out["is_hq"] = is_hq
    return out


class FilterShapeTests(unittest.TestCase):
    """Pin the dual-shape (`hq` OR `is_hq`) HQ filter — the bug that
    prompted this library was `sdlc-stall-detector.sh:64` filtering on
    `is_hq` while the live gc returns `hq`. The library's jq filter
    excludes a rig if EITHER field signals HQ; tests pin both branches.
    """

    def test_hq_true_field_excludes_rig(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, fakes_dir = _setup(tmp)
            rigs_json = json.dumps({"rigs": [_rig("hq-rig", "/p/hq", hq=True)]})
            spy_gc_rig_list(fakes_dir, rigs_json)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                result.stdout.strip(),
                "",
                f"hq=true rig should be filtered out; stdout={result.stdout!r}",
            )

    def test_is_hq_true_field_also_excludes_rig(self) -> None:
        """Defense against the schema drift that produced the bug —
        if gc renames `hq` → `is_hq`, the library still filters out HQ."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, fakes_dir = _setup(tmp)
            rigs_json = json.dumps({"rigs": [_rig("hq-rig", "/p/hq", hq=None, is_hq=True)]})
            spy_gc_rig_list(fakes_dir, rigs_json)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                result.stdout.strip(),
                "",
                f"is_hq=true rig should be filtered out; stdout={result.stdout!r}",
            )

    def test_neither_hq_field_present_treats_as_non_hq(self) -> None:
        """Safe default: if neither field is set, treat the rig as
        non-HQ (jq's `!= true` on missing field evaluates to true,
        so both conditions pass)."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, fakes_dir = _setup(tmp)
            rigs_json = json.dumps({"rigs": [_rig("legacy-rig", "/p/legacy", hq=None)]})
            spy_gc_rig_list(fakes_dir, rigs_json)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                result.stdout.strip(),
                "legacy-rig\t/p/legacy",
                f"missing-field rig should pass; stdout={result.stdout!r}",
            )


class SuspendedFilterTests(unittest.TestCase):
    def test_suspended_rig_excluded(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, fakes_dir = _setup(tmp)
            rigs_json = json.dumps({"rigs": [_rig("sleepy", "/p/sleepy", suspended=True)]})
            spy_gc_rig_list(fakes_dir, rigs_json)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "")


class MixedRigTests(unittest.TestCase):
    def test_only_active_non_hq_rigs_appear(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, fakes_dir = _setup(tmp)
            rigs_json = json.dumps(
                {
                    "rigs": [
                        _rig("hq", "/p/hq", hq=True),
                        _rig("alpha", "/p/alpha"),
                        _rig("napper", "/p/napper", suspended=True),
                        _rig("beta", "/p/beta"),
                    ]
                }
            )
            spy_gc_rig_list(fakes_dir, rigs_json)

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0, result.stderr)
            lines = sorted(result.stdout.strip().splitlines())
            self.assertEqual(
                lines,
                ["alpha\t/p/alpha", "beta\t/p/beta"],
                f"expected only alpha + beta; got {lines!r}",
            )


class ResilienceTests(unittest.TestCase):
    def test_no_city_root_arg_exits_clean(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, fakes_dir = _setup(tmp)
            spy_gc_rig_list(fakes_dir, '{"rigs": []}')

            env = {
                **os.environ,
                "PATH": f"{fakes_dir}:{os.environ['PATH']}",
                "SDLC_LIST_RIGS_GC": str(fakes_dir / "gc"),
            }
            env.pop("GC_CITY_ROOT", None)
            env.pop("CITY_ROOT", None)

            result = subprocess.run(
                [str(SCRIPT_PATH)],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, "missing city root should not crash")
            self.assertEqual(result.stdout.strip(), "")
            self.assertIn("no city root", result.stderr)

    def test_missing_city_root_dir_exits_clean(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            _, fakes_dir = _setup(tmp)
            spy_gc_rig_list(fakes_dir, '{"rigs": []}')

            result = _invoke(fakes_dir, tmp / "does-not-exist")

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "")
            self.assertIn("not a directory", result.stderr)

    def test_malformed_gc_output_emits_nothing(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, fakes_dir = _setup(tmp)
            spy_gc_rig_list(fakes_dir, "not json {")

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "")

    def test_empty_rigs_array_emits_nothing(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city_root, fakes_dir = _setup(tmp)
            spy_gc_rig_list(fakes_dir, '{"rigs": []}')

            result = _invoke(fakes_dir, city_root)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
