"""Tests for sdlc-cross-batch-dep-watcher.sh (pack #154).

Five scenarios pin the watcher's promotion rules:

1. Single predecessor with final_state=merged → admit (defer cleared,
   metadata marker unset).
2. Single predecessor with final_state=pr_open_for_human (PR still open) →
   left deferred; no admit call.
3. Multi-dep: one predecessor terminal, one not → left deferred.
4. Predecessor PR rejected (final_state=pr_open_for_human + gh state=CLOSED,
   no mergedAt) → notify invoked; no admit call.
5. Feature gate (SDLC_CROSS_BATCH_DEP_WATCHER_ENABLED=false) → watcher
   exits without any work.

Inline gc / gh / notify fakes on PATH for black-box subprocess testing.

stdlib-only (unittest + tempfile + subprocess + textwrap). Matches pack
convention; bodies use raw-string concatenation per the convention noted
in _spies.py's module docstring (textwrap.dedent breaks heredoc bodies
that sit at column 0).

Run with::

    python3 -m unittest assets.scripts.tests.test_sdlc_cross_batch_dep_watcher -v
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-cross-batch-dep-watcher.sh"
assert SCRIPT_PATH.exists(), f"sdlc-cross-batch-dep-watcher.sh not found at {SCRIPT_PATH}"


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_gc_fake(
    tmp: Path,
    rig_name: str,
    rig_path: str,
    bead_list_json: str,
    bead_show_responses: dict[str, str],
) -> Path:
    """Spawn a `gc` fake that dispatches the four subcommand shapes
    the watcher calls.

    - `gc bd --rig <rig> list --status=<s> --limit 5000 --json`
      → echoes the beads in `bead_list_json` whose `.status` equals the
      queried `--status` value (modeling bd: a deferred bead has
      `status=open`, so `--status=deferred` matches nothing). See #208.
    - `gc bd --rig <rig> show <bead-id> --json`
      → looks up `bead_show_responses[bead_id]` and echoes; defaults to `[]`.
    - `gc bd --rig <rig> update <bead-id> ...` → exit 0, argv logged.
    - Anything else → exit 0 silently.

    Argv recorded to `<tmp>/gc-argv.log`.
    """
    gc = tmp / "gc"
    show_cases = ""
    for bead_id, response_json in bead_show_responses.items():
        show_cases += (
            f'        {bead_id}) cat <<"__SHOW_EOF__"\n{response_json}\n__SHOW_EOF__\n;;\n'
        )
    if not show_cases:
        show_cases = '        *) echo "[]";;\n'

    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/gc-argv.log"\n'
        'if [ "$1" = "bd" ]; then\n'
        "    shift\n"
        '    if [ "$1" = "--rig" ]; then shift 2; fi\n'
        '    sub="$1"\n'
        "    shift || true\n"
        '    if [ "$sub" = "list" ]; then\n'
        '        status=""\n'
        '        for a in "$@"; do case "$a" in --status=*) status="${a#--status=}";; esac; done\n'
        "        cat <<'__LIST_EOF__' | jq --arg s \"$status\" '[.[] | select(.status == $s)]'\n"
        f"{bead_list_json}\n"
        "__LIST_EOF__\n"
        "        exit 0\n"
        "    fi\n"
        '    if [ "$sub" = "show" ]; then\n'
        '        case "$1" in\n'
        f"{show_cases}"
        "        esac\n"
        "        exit 0\n"
        "    fi\n"
        '    if [ "$sub" = "update" ]; then\n'
        "        exit 0\n"
        "    fi\n"
        "fi\n"
        "exit 0\n"
    )
    _write_exec(gc, body)
    return gc


def _make_gh_fake(tmp: Path, pr_responses: dict[str, str]) -> Path:
    """Fake `gh` that responds to `gh pr view <url> --json state,mergedAt`.

    `pr_responses` maps PR URL → JSON response shape.
    """
    gh = tmp / "gh"
    cases = ""
    for url, response in pr_responses.items():
        cases += f'        {url}) cat <<"__GH_EOF__"\n{response}\n__GH_EOF__\n;;\n'
    if not cases:
        cases = '        *) echo "{}";;\n'
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/gh-argv.log"\n'
        'if [ "$1" = "pr" ] && [ "$2" = "view" ]; then\n'
        '        case "$3" in\n'
        f"{cases}"
        "        esac\n"
        "        exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_exec(gh, body)
    return gh


def _make_notify_fake(tmp: Path) -> Path:
    """Fake sdlc-notify.sh that logs subject + body to a per-call file."""
    notify = tmp / "sdlc-notify.sh"
    body = (
        "#!/bin/bash\n"
        'subject=""\n'
        "while [ $# -gt 0 ]; do\n"
        '    if [ "$1" = "--subject" ]; then subject="$2"; shift 2; else shift; fi\n'
        "done\n"
        f'echo "subject=$subject" >> "{tmp}/notify-calls.log"\n'
        f'cat >> "{tmp}/notify-calls.log"\n'
        f'echo "---END---" >> "{tmp}/notify-calls.log"\n'
        "exit 0\n"
    )
    _write_exec(notify, body)
    return notify


def _make_rig_lister_fake(tmp: Path, rig_name: str, rig_path: str) -> Path:
    lister = tmp / "rig-lister-fake.sh"
    body = f"#!/bin/bash\nprintf '{rig_name}\\t{rig_path}\\n'\n"
    _write_exec(lister, body)
    return lister


def _bead(bead_id: str, predecessors_csv: str = "") -> dict:
    """A bead-list shape that the watcher's jq filter selects on."""
    metadata = {"final_state": "", "story_id": bead_id, "rig": "elder"}
    if predecessors_csv:
        metadata["cross_batch_dep_predecessors"] = predecessors_csv
    return {"id": bead_id, "status": "open", "metadata": metadata}


def _pred_bead(bead_id: str, final_state: str, pr_url: str = "") -> dict:
    """A bead-show shape for a predecessor."""
    metadata = {"final_state": final_state, "story_id": bead_id}
    if pr_url:
        metadata["pr_url"] = pr_url
    return {"id": bead_id, "status": "closed", "metadata": metadata}


def _run_watcher(
    tmp: Path,
    gc_fake: Path,
    gh_fake: Path,
    notify_fake: Path,
    lister_fake: Path,
    *,
    notify_recipient: str | None = "ops@example.com",
    enabled: bool = True,
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{tmp}:{os.environ.get('PATH', '')}",
        "GC_CITY_ROOT": str(tmp / "city"),
        "SDLC_CROSS_BATCH_DEP_WATCHER_RIG_LISTER": str(lister_fake),
        "SDLC_CROSS_BATCH_DEP_WATCHER_NOTIFY": str(notify_fake),
    }
    if notify_recipient is not None:
        env["SDLC_NOTIFY_RECIPIENT"] = notify_recipient
    if not enabled:
        env["SDLC_CROSS_BATCH_DEP_WATCHER_ENABLED"] = "false"
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        cwd=tmp,
    )


class CrossBatchDepWatcherTests(unittest.TestCase):
    """Pack #154 — admit-watcher promotion rules."""

    def setUp(self) -> None:
        self._tmpdir_ctx = TemporaryDirectory()
        self._tmp = Path(self._tmpdir_ctx.name)
        (self._tmp / "city").mkdir()
        (self._tmp / "city" / "city.toml").write_text("[city]\n")

    def tearDown(self) -> None:
        self._tmpdir_ctx.cleanup()

    def _gc_calls(self) -> list[str]:
        log = self._tmp / "gc-argv.log"
        return log.read_text().strip().splitlines() if log.exists() else []

    def _notify_calls(self) -> str:
        log = self._tmp / "notify-calls.log"
        return log.read_text() if log.exists() else ""

    def test_admits_when_single_predecessor_merged(self) -> None:
        """All preds terminal → defer cleared via `bd update` + marker unset."""
        succ = _bead("bd-succ001", predecessors_csv="bd-pred001")
        pred = _pred_bead("bd-pred001", final_state="merged")
        gc_fake = _make_gc_fake(
            self._tmp,
            "elder",
            str(self._tmp / "rig"),
            bead_list_json=json.dumps([succ]),
            bead_show_responses={"bd-pred001": json.dumps([pred])},
        )
        gh_fake = _make_gh_fake(self._tmp, {})
        notify_fake = _make_notify_fake(self._tmp)
        lister_fake = _make_rig_lister_fake(self._tmp, "elder", str(self._tmp / "rig"))

        result = _run_watcher(self._tmp, gc_fake, gh_fake, notify_fake, lister_fake)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        calls = self._gc_calls()
        update_calls = [c for c in calls if "update bd-succ001" in c]
        self.assertEqual(
            len(update_calls),
            1,
            msg=f"expected exactly one update on bd-succ001; got {update_calls}",
        )
        self.assertIn("--defer ", update_calls[0])  # defer cleared (empty string)
        self.assertIn("--unset-metadata cross_batch_dep_predecessors", update_calls[0])
        self.assertEqual(self._notify_calls(), "")  # no escalation

    def test_leaves_deferred_when_predecessor_unmerged(self) -> None:
        """Predecessor still pr_open_for_human (PR open) → no admit."""
        succ = _bead("bd-succ002", predecessors_csv="bd-pred002")
        pred = _pred_bead(
            "bd-pred002",
            final_state="pr_open_for_human",
            pr_url="https://github.com/example/repo/pull/100",
        )
        gc_fake = _make_gc_fake(
            self._tmp,
            "elder",
            str(self._tmp / "rig"),
            bead_list_json=json.dumps([succ]),
            bead_show_responses={"bd-pred002": json.dumps([pred])},
        )
        gh_fake = _make_gh_fake(
            self._tmp,
            {
                "https://github.com/example/repo/pull/100": json.dumps(
                    {"state": "OPEN", "mergedAt": ""}
                )
            },
        )
        notify_fake = _make_notify_fake(self._tmp)
        lister_fake = _make_rig_lister_fake(self._tmp, "elder", str(self._tmp / "rig"))

        result = _run_watcher(self._tmp, gc_fake, gh_fake, notify_fake, lister_fake)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        calls = self._gc_calls()
        update_calls = [c for c in calls if "update bd-succ002" in c]
        self.assertEqual(
            len(update_calls),
            0,
            msg=f"successor should NOT have been admitted; got {update_calls}",
        )
        self.assertEqual(self._notify_calls(), "")

    def test_partial_multi_dep_leaves_deferred(self) -> None:
        """One predecessor merged, one not → no admit."""
        succ = _bead("bd-succ003", predecessors_csv="bd-predA,bd-predB")
        pred_a = _pred_bead("bd-predA", final_state="merged")
        pred_b = _pred_bead(
            "bd-predB",
            final_state="pr_open_for_human",
            pr_url="https://github.com/example/repo/pull/200",
        )
        gc_fake = _make_gc_fake(
            self._tmp,
            "elder",
            str(self._tmp / "rig"),
            bead_list_json=json.dumps([succ]),
            bead_show_responses={
                "bd-predA": json.dumps([pred_a]),
                "bd-predB": json.dumps([pred_b]),
            },
        )
        gh_fake = _make_gh_fake(
            self._tmp,
            {
                "https://github.com/example/repo/pull/200": json.dumps(
                    {"state": "OPEN", "mergedAt": ""}
                )
            },
        )
        notify_fake = _make_notify_fake(self._tmp)
        lister_fake = _make_rig_lister_fake(self._tmp, "elder", str(self._tmp / "rig"))

        result = _run_watcher(self._tmp, gc_fake, gh_fake, notify_fake, lister_fake)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        calls = self._gc_calls()
        update_calls = [c for c in calls if "update bd-succ003" in c]
        self.assertEqual(len(update_calls), 0)

    def test_rejected_pr_escalates_via_notify(self) -> None:
        """final_state=pr_open_for_human + gh state=CLOSED-no-merge → notify called."""
        succ = _bead("bd-succ004", predecessors_csv="bd-pred004")
        pred = _pred_bead(
            "bd-pred004",
            final_state="pr_open_for_human",
            pr_url="https://github.com/example/repo/pull/300",
        )
        gc_fake = _make_gc_fake(
            self._tmp,
            "elder",
            str(self._tmp / "rig"),
            bead_list_json=json.dumps([succ]),
            bead_show_responses={"bd-pred004": json.dumps([pred])},
        )
        gh_fake = _make_gh_fake(
            self._tmp,
            {
                "https://github.com/example/repo/pull/300": json.dumps(
                    {"state": "CLOSED", "mergedAt": ""}
                )
            },
        )
        notify_fake = _make_notify_fake(self._tmp)
        lister_fake = _make_rig_lister_fake(self._tmp, "elder", str(self._tmp / "rig"))

        result = _run_watcher(self._tmp, gc_fake, gh_fake, notify_fake, lister_fake)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        notify_log = self._notify_calls()
        self.assertIn("subject=", notify_log)
        self.assertIn("cross-batch successor stuck", notify_log)
        self.assertIn("bd-succ004", notify_log)
        self.assertIn("bd-pred004", notify_log)

        # Successor stays deferred
        calls = self._gc_calls()
        update_calls = [c for c in calls if "update bd-succ004" in c]
        self.assertEqual(len(update_calls), 0)

    def test_feature_gate_disabled_exits_without_work(self) -> None:
        """SDLC_CROSS_BATCH_DEP_WATCHER_ENABLED=false → no work done."""
        succ = _bead("bd-succ005", predecessors_csv="bd-pred005")
        pred = _pred_bead("bd-pred005", final_state="merged")
        gc_fake = _make_gc_fake(
            self._tmp,
            "elder",
            str(self._tmp / "rig"),
            bead_list_json=json.dumps([succ]),
            bead_show_responses={"bd-pred005": json.dumps([pred])},
        )
        gh_fake = _make_gh_fake(self._tmp, {})
        notify_fake = _make_notify_fake(self._tmp)
        lister_fake = _make_rig_lister_fake(self._tmp, "elder", str(self._tmp / "rig"))

        result = _run_watcher(self._tmp, gc_fake, gh_fake, notify_fake, lister_fake, enabled=False)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(self._gc_calls(), [], msg="no gc calls expected when gated off")

    def test_watcher_queries_open_beads_not_deferred(self) -> None:
        """The watcher's `bd list` call must filter on `--status=open`.

        A deferred bead (future `defer_until`, set by `stories.py:cmd_file`
        via `bd update --defer 2099-01-01 --set-metadata
        cross_batch_dep_predecessors=...` per pack #154) has `status=open`
        in bd — there is no `deferred` status. A `--status=deferred` filter
        matches nothing, so the watcher's entire workload is invisible to
        its own scan. This reverts the pack #179 regression (#208); verified
        empirically against bd on 2026-05-29.
        """
        succ = _bead("bd-succ-open", predecessors_csv="bd-pred-open")
        pred = _pred_bead("bd-pred-open", final_state="merged")
        gc_fake = _make_gc_fake(
            self._tmp,
            "elder",
            str(self._tmp / "rig"),
            bead_list_json=json.dumps([succ]),
            bead_show_responses={"bd-pred-open": json.dumps([pred])},
        )
        gh_fake = _make_gh_fake(self._tmp, {})
        notify_fake = _make_notify_fake(self._tmp)
        lister_fake = _make_rig_lister_fake(self._tmp, "elder", str(self._tmp / "rig"))

        result = _run_watcher(self._tmp, gc_fake, gh_fake, notify_fake, lister_fake)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        calls = self._gc_calls()
        list_calls = [c for c in calls if "list" in c and "--status=" in c]
        self.assertTrue(
            any("--status=open" in c for c in list_calls),
            msg=(
                "watcher must list --status=open (a deferred bead is "
                "status=open; --status=deferred matches nothing); see #208. "
                f"got list calls: {list_calls}"
            ),
        )
        self.assertFalse(
            any("--status=deferred" in c for c in list_calls),
            msg=(
                "watcher must NOT list --status=deferred (matches no bead); "
                f"see #208. got list calls: {list_calls}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
