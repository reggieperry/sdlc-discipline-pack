"""Shared test helpers for assets/scripts/tests/ black-box subprocess tests.

The `assets/scripts/` directory ships shell scripts as the pack's primary
chain machinery. Their tests stand up a tempdir with fake binaries on PATH
(claude / bd / msmtp / gh as needed), invoke the script under test, and
assert on argv + stdin captures recorded by the fakes.

The fake-binary helpers were originally inlined in each test file. They've
since duplicated across `test_sdlc_notify.py` and
`test_sdlc_finalizer_notify.py`; this module is the single source.

Add additional fakes here as new tests need them. The pack convention is
underscore-prefix exports so unittest discovery (which only loads files
matching `test_*.py` by default) treats this as helper, not test.
"""

from __future__ import annotations

import stat
import textwrap
from pathlib import Path


def _write_executable(path: Path, body: str) -> None:
    """Write a shell script and chmod it executable.

    The chmod adds u+x, g+x, o+x while preserving the existing mode bits.
    Tests rely on this for any binary they place into a tempdir then add
    to PATH.
    """
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_msmtp(tmp: Path, *, exit_code: int = 0) -> Path:
    """Build a fake `msmtp` binary that records argv + stdin then exits.

    Records:
    - argv → `<tmp>/msmtp-argv.log` (one line per call, space-separated)
    - stdin → `<tmp>/msmtp-stdin.log` (multi-line, appended across calls)

    Tests then inspect those log files to assert on recipient (argv) and
    Subject + body (stdin). The exit code defaults to 0 for the
    happy-path scenarios; tests for transport failure pass a non-zero
    value.
    """
    path = tmp / "msmtp"
    body = textwrap.dedent(
        f"""\
        #!/bin/bash
        echo "$@" >> "{tmp}/msmtp-argv.log"
        cat >> "{tmp}/msmtp-stdin.log"
        exit {exit_code}
        """
    )
    _write_executable(path, body)
    return path
