"""Shared test-spy factories for assets/scripts/tests/ black-box subprocess tests.

The fakes here are Test Spies in Meszaros xUnit Test Patterns terminology:
each is a small shell script placed on $PATH that records argv (and
sometimes stdin) to a log file, then returns canned output. They behave
like Stubs from the script-under-test's perspective and like Spies from
the assertion's perspective.

v2.29.3 consolidated the `gc rig list` spy shape and its co-travelling
helpers across four test files:

  - test_sdlc_list_rigs.py             (gc)
  - test_sdlc_exhausted_bead_retry.py  (gc, bd-list, notify)
  - test_sdlc_zombie_reconciler.py     (gc, bd-list, gh-pr-list,
                                        python3-stories-archive, notify)
  - test_sdlc_stale_pr_sweeper.py      (gc, bd-dispatch, gh-pr-view,
                                        python3-stories-passthrough)

Three further test files still carry their own `_fake_gc` builders for
gc surfaces this module does not yet cover — `gc bd peek`, `gc rig show`,
event-stream payloads:

  - test_sdlc_alive_idle_detector.py   (gc with bd-list + tmux-peek)
  - test_sdlc_drain_ack_recover.py     (gc with bead-show)
  - test_sdlc_find_city_root.py        (gc cities fallback)

Migrating those needs new factories (the rig-list shape doesn't fit
them). Tracked as a follow-on; until then the Rule of Three is partly
breached — four files imported, three still inline.

`_write_executable` is re-exported from `_helpers` so callers need only
one import. `_fake_msmtp` stays in `_helpers.py` until its callers
(`test_sdlc_notify.py`, `test_sdlc_finalizer_notify.py`) migrate in a
later pass — `_fake_msmtp` is itself a Spy (records argv + stdin) and
will land in this module as `spy_msmtp` when the collapse happens.
Underscore-prefix exports keep unittest discovery clean.

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

import subprocess
from pathlib import Path

from _helpers import _write_executable

__all__ = [
    "_write_executable",
    "spy_gc_rig_list",
    "spy_bd_list",
    "spy_bd_dispatch",
    "spy_gh_pr_list",
    "spy_gh_pr_view",
    "spy_python3_stories_archive",
    "spy_python3_stories_passthrough",
    "spy_notify",
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
