"""Tests for sdlc-find-city-root.sh — shared city-root resolution
across three scripts that previously duplicated the walk-up
(sdlc-stall-detector.sh, sdlc-order-stall-detector.sh,
sdlc-alive-idle-detector.sh). The first two looked for `city.toml`
as the walk-up marker; the third looked for `.gc/events.jsonl`.
The library accepts the marker as the first arg.

Pins three resolution sources (GC_CITY_ROOT > walk-up > gc-cities)
plus the marker-as-arg behavior plus the failure path.

stdlib-only.

Run with::

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import os
import stat
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "lib" / "sdlc-find-city-root.sh"
assert SCRIPT_PATH.exists(), f"sdlc-find-city-root.sh not found at {SCRIPT_PATH}"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_gc(fakes_dir: Path, cities_output: str = "") -> None:
    """Fake `gc` that returns cities_output on `gc cities`."""
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{fakes_dir}/gc-argv.log"\n'
        'if [ "$1" = "cities" ]; then\n'
        "    cat <<'__GC_EOF__'\n"
        f"{cities_output}\n"
        "__GC_EOF__\n"
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_executable(fakes_dir / "gc", body)


def _invoke(
    fakes_dir: Path,
    *,
    marker: str = "",
    gc_city_root: str | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{fakes_dir}:{os.environ['PATH']}",
        "SDLC_FIND_CITY_GC": str(fakes_dir / "gc"),
    }
    if gc_city_root is not None:
        env["GC_CITY_ROOT"] = gc_city_root
    else:
        env.pop("GC_CITY_ROOT", None)
    args = [str(SCRIPT_PATH)]
    if marker:
        args.append(marker)
    return subprocess.run(
        args,
        env=env,
        cwd=str(cwd) if cwd else fakes_dir,
        capture_output=True,
        text=True,
        timeout=10,
    )


class GcCityRootTests(unittest.TestCase):
    """Source 1: $GC_CITY_ROOT wins when set and valid."""

    def test_gc_city_root_with_marker_present_returned(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city = tmp / "city"
            city.mkdir()
            (city / "city.toml").write_text("[city]\n")
            fakes = tmp / "fakes"
            fakes.mkdir()
            _fake_gc(fakes)

            result = _invoke(fakes, gc_city_root=str(city))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, str(city))

    def test_gc_city_root_without_marker_falls_through(self) -> None:
        """If GC_CITY_ROOT points somewhere that doesn't have the marker,
        the library falls through to walk-up + gc cities."""
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            wrong = tmp / "wrong"
            wrong.mkdir()
            fakes = tmp / "fakes"
            fakes.mkdir()
            _fake_gc(fakes)

            result = _invoke(fakes, gc_city_root=str(wrong), cwd=fakes)

            self.assertNotEqual(result.returncode, 0)


class WalkUpTests(unittest.TestCase):
    """Source 2: walk up from $PWD until the marker is found."""

    def test_walk_up_finds_marker(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city = tmp / "city"
            deep = city / "deep" / "nested"
            deep.mkdir(parents=True)
            (city / "city.toml").write_text("[city]\n")
            fakes = tmp / "fakes"
            fakes.mkdir()
            _fake_gc(fakes)

            result = _invoke(fakes, cwd=deep)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, str(city))


class GcCitiesFallbackTests(unittest.TestCase):
    """Source 3: `gc cities` first row when env + walk-up both miss."""

    def test_gc_cities_fallback_used(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city = tmp / "city"
            city.mkdir()
            (city / "city.toml").write_text("[city]\n")
            fakes = tmp / "fakes"
            fakes.mkdir()
            _fake_gc(
                fakes,
                cities_output=f"HEADER\nbright-lights {city}\n",
            )

            result = _invoke(fakes, cwd=tmp)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, str(city))


class MarkerArgTests(unittest.TestCase):
    """Marker as first positional arg — alive-idle-detector passes
    `.gc/events.jsonl` instead of the default `city.toml`."""

    def test_custom_marker_is_used_for_walk_up(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city = tmp / "city"
            (city / ".gc").mkdir(parents=True)
            (city / ".gc" / "events.jsonl").write_text("")
            # No city.toml — only the alive-idle marker exists.
            fakes = tmp / "fakes"
            fakes.mkdir()
            _fake_gc(fakes)

            result = _invoke(fakes, marker=".gc/events.jsonl", cwd=city)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, str(city))

    def test_wrong_marker_fails(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            city = tmp / "city"
            (city / ".gc").mkdir(parents=True)
            (city / ".gc" / "events.jsonl").write_text("")
            fakes = tmp / "fakes"
            fakes.mkdir()
            _fake_gc(fakes)

            # Asks for city.toml but only .gc/events.jsonl exists.
            result = _invoke(fakes, marker="city.toml", cwd=city)

            self.assertNotEqual(result.returncode, 0)


class FailurePathTests(unittest.TestCase):
    def test_no_source_resolves_returns_nonzero_with_stderr(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            fakes = tmp / "fakes"
            fakes.mkdir()
            _fake_gc(fakes)  # empty cities output

            result = _invoke(fakes, cwd=tmp)

            self.assertEqual(result.returncode, 1)
            self.assertIn("could not resolve city root", result.stderr)


if __name__ == "__main__":
    unittest.main()
