"""Shared test-spy factories for assets/scripts/tests/ black-box subprocess tests.

The fakes here are Test Spies in Meszaros xUnit Test Patterns terminology:
each is a small shell script placed on $PATH that records argv (and
sometimes stdin) to a log file, then returns canned output. They behave
like Stubs from the script-under-test's perspective and like Spies from
the assertion's perspective.

v2.29.3 consolidated the `gc rig list` spy shape and its co-travelling
helpers across four test files. v2.29.7 (issue #112) extended the
coverage with three further gc shapes — `gc bd / session / peek / submit`,
`gc bd show / update + session kill + supervisor reload`, and the
`gc cities` fallback — closing the spy migration for seven test files:

  - test_sdlc_list_rigs.py             (gc-rig-list)
  - test_sdlc_exhausted_bead_retry.py  (gc-rig-list, bd-list, notify)
  - test_sdlc_zombie_reconciler.py     (gc-rig-list, bd-list, gh-pr-list,
                                        python3-stories-archive, notify)
  - test_sdlc_stale_pr_sweeper.py      (gc-rig-list, bd-dispatch,
                                        gh-pr-view, python3-stories-passthrough)
  - test_sdlc_alive_idle_detector.py   (gc-session-dispatch, recorder)
  - test_sdlc_drain_ack_recover.py     (gc-bd-show, recorder)
  - test_sdlc_find_city_root.py        (gc-cities)

`test_sdlc_claude_with_retry.py` still carries inline `_fake_bd_*`
builders for the synthetic-retry test mode; deferred — its dispatch
shape is sufficiently narrow that the consolidation cost isn't yet
justified per Rule of Three.

v2.29.8 (issue #113) collapsed the previous `_helpers.py` module into
this one. `_write_executable` now lives here directly (no re-export
hop); `_fake_msmtp` was renamed `spy_msmtp` per Meszaros's Test Spy
vocabulary. Underscore-prefix exports keep unittest discovery clean.

Convention: every factory takes the tempdir as its first positional arg,
writes the spy script under it, and returns the Path to the spy. The
caller is responsible for putting the tempdir on $PATH (or threading
the path into the script-under-test via the appropriate env var).

Implementation note: spy bodies use raw string concatenation rather than
textwrap.dedent. `textwrap.dedent` cannot strip indentation when any
non-empty line (notably heredoc bodies and case-statement bodies) sits
at column 0 — it would leave the shebang at column 8, which the kernel
refuses to exec. Concatenation is uglier but byte-equivalent to the
originals and exec-clean.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path


def _write_executable(path: Path, body: str) -> None:
    """Write a shell script and chmod it executable.

    The chmod adds u+x, g+x, o+x while preserving the existing mode bits.
    Tests rely on this for any binary they place into a tempdir then add
    to PATH.
    """
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


__all__ = [
    "_write_executable",
    "spy_gc_rig_list",
    "spy_gc_with_session_dispatch",
    "spy_gc_with_bd_show",
    "spy_gc_cities",
    "spy_bd_list",
    "spy_bd_dispatch",
    "spy_gh_pr_list",
    "spy_gh_pr_view",
    "spy_python3_stories_archive",
    "spy_python3_stories_passthrough",
    "spy_notify",
    "spy_msmtp",
    "spy_recorder",
]


def spy_gc_rig_list(tmp: Path, rig_list_json: str) -> Path:
    """Fake `gc` that emits ``rig_list_json`` on ``gc rig list ...``.

    Records argv to ``<tmp>/gc-argv.log``. Returns the spy's Path.
    """
    path = tmp / "gc"
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/gc-argv.log"\n'
        'if [ "$1" = "rig" ] && [ "$2" = "list" ]; then\n'
        "    cat <<'__GC_EOF__'\n"
        f"{rig_list_json}\n"
        "__GC_EOF__\n"
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_executable(path, body)
    return path


def spy_bd_list(tmp: Path, list_response: str = "[]") -> Path:
    """Fake `bd` that returns ``list_response`` for ``bd list ...``.

    Strips a ``-C <dir>`` prefix if present. Logs all argv to
    ``<tmp>/bd-argv.log``. Other subcommands exit 0 silently. Returns the
    spy's Path.
    """
    path = tmp / "bd"
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/bd-argv.log"\n'
        'if [ "$1" = "-C" ]; then shift 2; fi\n'
        'if [ "$1" = "list" ]; then\n'
        "    cat <<'__BD_LIST_EOF__'\n"
        f"{list_response}\n"
        "__BD_LIST_EOF__\n"
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_executable(path, body)
    return path


def spy_bd_dispatch(tmp: Path, bead_responses: dict[str, str]) -> Path:
    """Fake `bd` that dispatches ``bd list``, ``bd show <id>``, ``bd update``.

    ``bead_responses`` maps:

    - ``"list"``       → JSON for ``bd list --json``
    - ``"<bead_id>"``  → JSON for ``bd show <bead_id> --json``

    ``bd update`` is silently accepted (exit 0). Logs argv to
    ``<tmp>/bd-argv.log``. Returns the spy's Path.

    The case-statement bodies are pre-built as strings and concatenated
    into the outer script. The terminator ``__BD_SHOW_EOF__`` must sit at
    column 0 in the emitted script for the heredoc to close — concatenation
    preserves that. (textwrap.dedent would break the shebang; see module
    docstring.)
    """
    path = tmp / "bd"
    list_response = bead_responses.get("list", "[]")
    show_cases: list[str] = []
    for bead_id, json_body in bead_responses.items():
        if bead_id == "list":
            continue
        show_cases.append(
            f"    {bead_id})\n"
            "        cat <<'__BD_SHOW_EOF__'\n"
            f"{json_body}\n"
            "__BD_SHOW_EOF__\n"
            "        ;;\n"
        )
    show_block = "".join(show_cases)
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/bd-argv.log"\n'
        "# Strip -C <dir> prefix if present.\n"
        'if [ "$1" = "-C" ]; then shift 2; fi\n'
        'if [ "$1" = "list" ]; then\n'
        "    cat <<'__BD_LIST_EOF__'\n"
        f"{list_response}\n"
        "__BD_LIST_EOF__\n"
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "show" ]; then\n'
        '    case "$2" in\n'
        f"{show_block}"
        "        *) echo '[]' ;;\n"
        "    esac\n"
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "update" ]; then\n'
        "    # Record the update call; exit 0.\n"
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_executable(path, body)
    return path


def spy_gh_pr_list(tmp: Path, pr_list_response: str = "[]") -> Path:
    """Fake `gh` that returns ``pr_list_response`` for ``gh pr list ...``.

    Logs argv to ``<tmp>/gh-argv.log``. Returns the spy's Path.
    """
    path = tmp / "gh"
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/gh-argv.log"\n'
        'if [ "$1" = "pr" ] && [ "$2" = "list" ]; then\n'
        "    cat <<'__GH_EOF__'\n"
        f"{pr_list_response}\n"
        "__GH_EOF__\n"
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_executable(path, body)
    return path


def spy_gh_pr_view(tmp: Path, pr_responses: dict[int, str]) -> Path:
    """Fake `gh` that returns canned responses for ``gh pr view <N>``.

    ``pr_responses`` maps PR number → JSON string. Unknown PRs return
    ``{}``. Logs argv to ``<tmp>/gh-argv.log``. Returns the spy's Path.

    Same case-statement-via-concatenation pattern as ``spy_bd_dispatch``
    so the heredoc terminator lands at column 0.
    """
    path = tmp / "gh"
    cases: list[str] = []
    for pr_num, json_body in pr_responses.items():
        cases.append(
            f"    {pr_num})\n"
            "        cat <<'__GH_PR_EOF__'\n"
            f"{json_body}\n"
            "__GH_PR_EOF__\n"
            "        ;;\n"
        )
    case_block = "".join(cases)
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/gh-argv.log"\n'
        'if [ "$1" = "pr" ] && [ "$2" = "view" ]; then\n'
        '    case "$3" in\n'
        f"{case_block}"
        "        *) echo '{}' ;;\n"
        "    esac\n"
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_executable(path, body)
    return path


def spy_python3_stories_archive(tmp: Path, archive_exit: int = 0) -> Path:
    """Fake `python3` that short-circuits ``python3 <stories.py> archive ...``.

    Substitutes python3 on PATH with a wrapper. When ``archive`` appears
    in argv the wrapper logs the call and exits ``archive_exit``; any
    other invocation falls through to the real python3 (resolved via
    ``which python3`` at fake-build time). Logs to
    ``<tmp>/stories-archive-argv.log``. Returns the spy's Path.
    """
    path = tmp / "python3"
    real_python = subprocess.run(
        ["which", "python3"], capture_output=True, text=True
    ).stdout.strip()
    body = (
        "#!/bin/bash\n"
        "# If invoked with a stories.py path + 'archive' subcommand, record + exit.\n"
        'for arg in "$@"; do\n'
        '    if [ "$arg" = "archive" ]; then\n'
        f'        echo "$@" >> "{tmp}/stories-archive-argv.log"\n'
        f"        exit {archive_exit}\n"
        "    fi\n"
        "done\n"
        f'exec "{real_python}" "$@"\n'
    )
    _write_executable(path, body)
    return path


def spy_python3_stories_passthrough(tmp: Path) -> Path:
    """Fake `python3` that fake-succeeds any ``python3 <stories.py> ...`` call.

    Substitutes python3 on PATH. When any argv token ends with
    ``stories.py``, logs argv and exits 0. Any other invocation falls
    through to ``/usr/bin/python3`` (no `which` lookup — same hard-coded
    fallback as the inline at ``test_sdlc_stale_pr_sweeper.py:156``).
    Logs to ``<tmp>/python3-argv.log``. Returns the spy's Path.
    """
    path = tmp / "python3"
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/python3-argv.log"\n'
        "# If any arg looks like a stories.py path, fake-succeed.\n"
        'for arg in "$@"; do\n'
        '    case "$arg" in\n'
        "        *stories.py) exit 0 ;;\n"
        "    esac\n"
        "done\n"
        "# Fallback: dispatch to the real python3 for anything else.\n"
        'exec /usr/bin/python3 "$@"\n'
    )
    _write_executable(path, body)
    return path


def spy_notify(tmp: Path) -> Path:
    """Fake `sdlc-notify.sh` under a fake-pack layout.

    Creates ``<tmp>/fake-pack/assets/scripts/sdlc-notify.sh`` that
    records argv to ``<tmp>/notify-argv.log`` and exits 0. The fake-pack
    layout is what scripts that invoke notify-by-relative-path expect.
    Returns the spy's Path (the sdlc-notify.sh file itself).
    """
    pack_assets_scripts = tmp / "fake-pack" / "assets" / "scripts"
    pack_assets_scripts.mkdir(parents=True, exist_ok=True)
    path = pack_assets_scripts / "sdlc-notify.sh"
    body = f'#!/bin/bash\necho "$@" >> "{tmp}/notify-argv.log"\nexit 0\n'
    _write_executable(path, body)
    return path


def spy_gc_with_session_dispatch(
    tmp: Path,
    *,
    bd_list_json: str = "[]",
    session_list_json: str = "[]",
    peek_output: str = "",
    submit_exit: int = 0,
) -> Path:
    """Fake `gc` that dispatches on bd/session subcommands.

    Used by `sdlc-alive-idle-detector.sh` tests. Records argv to
    ``<tmp>/gc-argv.log`` AND appends ``gc <argv>`` to
    ``<tmp>/call-sequence.log`` so tests can verify ordering across peers
    in a single read.

    Dispatch:
    - ``gc bd list ...``      → echoes ``bd_list_json``
    - ``gc session list ...`` → echoes ``session_list_json``
    - ``gc session peek ...`` → echoes ``peek_output``
    - ``gc session submit ...`` → exits ``submit_exit``
    - anything else → exits 0 silently

    Returns the spy's Path.
    """
    path = tmp / "gc"
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/gc-argv.log"\n'
        f'echo "gc $@" >> "{tmp}/call-sequence.log"\n'
        'if [ "$1" = "bd" ] && [ "$2" = "list" ]; then\n'
        "    cat <<'__BD_EOF__'\n"
        f"{bd_list_json}\n"
        "__BD_EOF__\n"
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "session" ] && [ "$2" = "list" ]; then\n'
        "    cat <<'__SL_EOF__'\n"
        f"{session_list_json}\n"
        "__SL_EOF__\n"
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "session" ] && [ "$2" = "peek" ]; then\n'
        "    cat <<'__PEEK_EOF__'\n"
        f"{peek_output}\n"
        "__PEEK_EOF__\n"
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "session" ] && [ "$2" = "submit" ]; then\n'
        f"    exit {submit_exit}\n"
        "fi\n"
        "exit 0\n"
    )
    _write_executable(path, body)
    return path


def spy_gc_with_bd_show(
    tmp: Path,
    *,
    bead_json: str = "",
    show_exit: int = 0,
    kill_exit: int = 0,
    reload_exit: int = 0,
    update_exit: int = 0,
) -> Path:
    """Fake `gc` that dispatches on bd-show, bd-update, session kill, supervisor reload.

    Used by `sdlc-drain-ack-recover.sh` tests. Records argv to
    ``<tmp>/gc-argv.log`` AND appends ``gc <argv>`` to
    ``<tmp>/call-sequence.log``.

    Strips a ``--rig <rig>`` prefix on `bd` subcommands so callers don't
    need to encode the rig namespace in their dispatch test.

    Dispatch:
    - ``gc bd [--rig <rig>] show <bead-id> --json`` → echoes ``bead_json``, exits ``show_exit``
    - ``gc bd [--rig <rig>] update <bead-id> ...``  → exits ``update_exit``
    - ``gc session kill <session-id>``              → exits ``kill_exit``
    - ``gc supervisor reload``                      → exits ``reload_exit``
    - anything else → exits 0 silently

    Tests pass a non-zero exit for one subcommand at a time to verify
    fail-closed semantics without disturbing the other steps. Returns
    the spy's Path.
    """
    path = tmp / "gc"
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/gc-argv.log"\n'
        f'echo "gc $@" >> "{tmp}/call-sequence.log"\n'
        'if [ "$1" = "bd" ]; then\n'
        "    shift\n"
        "    # consume --rig <rig> if present\n"
        '    if [ "${1:-}" = "--rig" ]; then shift 2; fi\n'
        '    sub="${1:-}"\n'
        '    if [ "$sub" = "show" ]; then\n'
        "        cat <<'__BEAD_EOF__'\n"
        f"{bead_json}\n"
        "__BEAD_EOF__\n"
        f"        exit {show_exit}\n"
        '    elif [ "$sub" = "update" ]; then\n'
        f"        exit {update_exit}\n"
        "    fi\n"
        "    exit 0\n"
        'elif [ "$1" = "session" ] && [ "${2:-}" = "kill" ]; then\n'
        f"    exit {kill_exit}\n"
        'elif [ "$1" = "supervisor" ] && [ "${2:-}" = "reload" ]; then\n'
        f"    exit {reload_exit}\n"
        "fi\n"
        "exit 0\n"
    )
    _write_executable(path, body)
    return path


def spy_gc_cities(tmp: Path, cities_output: str = "") -> Path:
    """Fake `gc` that returns ``cities_output`` for ``gc cities``.

    Used by `sdlc-find-city-root.sh` tests. Records argv to
    ``<tmp>/gc-argv.log``. Anything other than ``gc cities`` exits 0
    silently. Returns the spy's Path.
    """
    path = tmp / "gc"
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/gc-argv.log"\n'
        'if [ "$1" = "cities" ]; then\n'
        "    cat <<'__GC_EOF__'\n"
        f"{cities_output}\n"
        "__GC_EOF__\n"
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_executable(path, body)
    return path


def spy_msmtp(tmp: Path, *, exit_code: int = 0) -> Path:
    """Fake `msmtp` binary that records argv + stdin then exits.

    Records:
    - argv  → ``<tmp>/msmtp-argv.log`` (one line per call, space-separated)
    - stdin → ``<tmp>/msmtp-stdin.log`` (multi-line, appended across calls)

    Tests inspect those log files to assert on recipient (argv) and
    Subject + body (stdin). The exit code defaults to 0; tests for
    transport failure pass a non-zero value.

    Used by `sdlc-notify.sh` and `sdlc-finalizer-notify.sh` tests.
    Returns the spy's Path.
    """
    path = tmp / "msmtp"
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/msmtp-argv.log"\n'
        f'cat >> "{tmp}/msmtp-stdin.log"\n'
        f"exit {exit_code}\n"
    )
    _write_executable(path, body)
    return path


def spy_recorder(tmp: Path, name: str, *, exit_code: int = 0) -> Path:
    """Generic argv-recording spy for peer binaries the script-under-test
    calls but where the tests don't need a parameterized response.

    Records:
    - argv  → ``<tmp>/<name>-argv.log`` (one line per call)
    - stdin → ``<tmp>/<name>-stdin.log`` (best-effort; never fails)
    - sequence → ``<tmp>/call-sequence.log`` (``<name> <argv>`` per call,
      so cross-peer ordering can be verified in one read)

    Used by `sdlc-drain-ack-recover.sh` and `sdlc-alive-idle-detector.sh`
    tests for binaries like ``git``, ``sdlc-stall-recover.sh``,
    ``sdlc-notify.sh`` (when not under the fake-pack layout that
    ``spy_notify`` provides). Returns the spy's Path.
    """
    path = tmp / name
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/{name}-argv.log"\n'
        f'echo "{name} $@" >> "{tmp}/call-sequence.log"\n'
        f'cat >> "{tmp}/{name}-stdin.log" 2>/dev/null || true\n'
        f"exit {exit_code}\n"
    )
    _write_executable(path, body)
    return path
