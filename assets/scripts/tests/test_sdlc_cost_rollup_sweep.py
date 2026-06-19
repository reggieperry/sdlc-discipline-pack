"""Tests for ``sdlc-cost-rollup-sweep.sh`` (pack #148).

Idempotency is the load-bearing property: the periodic sweep replaces an
event-triggered handler that the upstream gc supervisor has stopped
firing for non-wisp beads (gascity#2546). Re-runs of the sweep must
NOT duplicate rows in the cost-history CSV, and beads whose (story_id,
rig) pair is already recorded must be skipped — the writer is invoked
only for new beads.

Four scenarios:

1. Empty CSV + one closed bead          -> writer invoked once
2. CSV pre-populated with bead's pair   -> writer NOT invoked (skipped)
3. Two consecutive sweep runs           -> writer invoked once total
4. Wisp bead in the closed list         -> writer NOT invoked (filtered)

Test scaffold uses inline shell fakes rather than the ``_spies.py``
helpers because the sweep's ``gc`` peer needs to dispatch on three
distinct subcommand shapes (``gc rig list``, ``gc bd --rig X list``,
``gc bd --rig X show``) that the existing spy factories don't cover
together. Adding a fifth spy for one test would breach the
Rule-of-Three — inline keeps the noise local until a second consumer
emerges.

stdlib-only (unittest + tempfile + subprocess + textwrap). Matches
pack convention.

Run with::

    python3 -m unittest assets/scripts/tests/test_sdlc_cost_rollup_sweep -v
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-cost-rollup-sweep.sh"
assert SCRIPT_PATH.exists(), f"sdlc-cost-rollup-sweep.sh not found at {SCRIPT_PATH}"


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_gc_fake(tmp: Path, rig_name: str, rig_path: str, beads_list: list[dict]) -> Path:
    """Spawn a ``gc`` fake that dispatches three subcommand shapes.

    - ``gc rig list --json``                            -> configured rig list
    - ``gc bd --rig <rig> list --status=closed --json`` -> configured bead list
    - ``gc bd --rig <rig> show <bead-id> --json``       -> the bead's record

    Body uses raw string concatenation (not textwrap.dedent) because the
    heredoc content lines sit at column 0; dedent can't strip indentation
    off the closing markers without also stripping the JSON body. Matches
    the convention noted in ``_spies.py``'s module docstring.
    """
    beads_by_id = {b["id"]: b for b in beads_list}
    gc = tmp / "gc"
    rig_list_json = json.dumps(
        {"rigs": [{"name": rig_name, "path": rig_path, "hq": False, "suspended": False}]}
    )
    beads_json = json.dumps(beads_list)
    bead_cases = ""
    for bid, bead in beads_by_id.items():
        bead_json = json.dumps([bead])
        bead_cases += f'    {bid}) cat <<"__BEAD_EOF__"\n{bead_json}\n__BEAD_EOF__\n;;\n'
    if not bead_cases:
        bead_cases = '    *) echo "[]";;\n'
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/gc-argv.log"\n'
        'if [ "$1" = "rig" ] && [ "$2" = "list" ]; then\n'
        "    cat <<'__RIG_EOF__'\n"
        f"{rig_list_json}\n"
        "__RIG_EOF__\n"
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "bd" ]; then\n'
        "    shift\n"
        '    if [ "$1" = "--rig" ]; then shift 2; fi\n'
        '    sub="$1"\n'
        "    shift || true\n"
        '    if [ "$sub" = "list" ]; then\n'
        "        cat <<'__LIST_EOF__'\n"
        f"{beads_json}\n"
        "__LIST_EOF__\n"
        "        exit 0\n"
        "    fi\n"
        '    if [ "$sub" = "show" ]; then\n'
        '        case "$1" in\n'
        f"{bead_cases}"
        "        esac\n"
        "        exit 0\n"
        "    fi\n"
        "fi\n"
        "exit 0\n"
    )
    _write_exec(gc, body)
    return gc


def _make_writer_fake(tmp: Path, csv: Path) -> Path:
    """Per-bead writer fake: appends one sentinel row per invocation.

    Mirrors the real writer's story_id resolution — read ``metadata.story_id``
    from the bead and fall back to the bead id — so the CSV's story_id
    column matches what the sweep looks up against. The invocation log
    keys on bead_id+rig so the test can verify which beads were processed.
    """
    writer = tmp / "writer-fake.sh"
    body = (
        "#!/bin/bash\n"
        f'echo "$GC_EVENT_SUBJECT|$GC_RIG" >> "{tmp}/writer-invocations.log"\n'
        'BEAD_META=$(gc bd --rig "$GC_RIG" show "$GC_EVENT_SUBJECT" --json 2>/dev/null | jq -r ".[0].metadata // {}")\n'
        'STORY_ID=$(echo "$BEAD_META" | jq -r ".story_id // empty")\n'
        '[ -z "$STORY_ID" ] && STORY_ID="$GC_EVENT_SUBJECT"\n'
        'if [ ! -f "$GC_CITY_ROOT/cost_history.csv" ]; then\n'
        '    echo "timestamp,story_id,phase,session_id,duration_seconds,cost_usd,rig" > "$GC_CITY_ROOT/cost_history.csv"\n'
        "fi\n"
        'echo "2026-01-01T00:00:00Z,$STORY_ID,worker,sess-fake,100,1.0,$GC_RIG" >> "$GC_CITY_ROOT/cost_history.csv"\n'
    )
    _write_exec(writer, body)
    return writer


def _make_rig_lister_fake(tmp: Path, rig_name: str, rig_path: str) -> Path:
    """Rig lister fake: emits one TSV line for the configured rig."""
    lister = tmp / "rig-lister-fake.sh"
    body = f"#!/bin/bash\nprintf '{rig_name}\\t{rig_path}\\n'\n"
    _write_exec(lister, body)
    return lister


def _bead(
    bead_id: str, story_id: str = "", rig: str = "elder", closed_at: str = "2026-05-24T00:00:00Z"
) -> dict:
    return {
        "id": bead_id,
        "status": "closed",
        "closed_at": closed_at,
        "metadata": {
            "story_id": story_id or bead_id,
            "rig": rig,
            "worker.session_id": f"sess-{bead_id}-w",
            "worker.started_at": "2026-05-24T00:00:00Z",
            "worker.completed_at": "2026-05-24T00:10:00Z",
        },
    }


def _run_sweep(
    tmp: Path, gc: Path, writer: Path, lister: Path, env_extra: dict | None = None
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{tmp}:{os.environ.get('PATH', '')}",
        "GC_CITY_ROOT": str(tmp / "city"),
        "SDLC_COST_ROLLUP_PER_BEAD_PATH": str(writer),
        "SDLC_COST_ROLLUP_RIG_LISTER": str(lister),
        # Pin the recency window off by default so these tests are deterministic
        # regardless of when they run (the script otherwise defaults SINCE to
        # 30 days ago). The window itself is exercised by the dedicated tests
        # below, which override this via env_extra.
        "SDLC_COST_ROLLUP_SINCE": "2000-01-01",
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        cwd=tmp,
    )


class SweepIdempotencyTests(unittest.TestCase):
    """Pack #148 — periodic sweep idempotency."""

    def setUp(self) -> None:
        self._tmpdir_ctx = TemporaryDirectory()
        self._tmp = Path(self._tmpdir_ctx.name)
        (self._tmp / "city").mkdir()
        (self._tmp / "city" / "city.toml").write_text("[city]\n")

    def tearDown(self) -> None:
        self._tmpdir_ctx.cleanup()

    def _invocations(self) -> list[str]:
        log = self._tmp / "writer-invocations.log"
        return log.read_text().strip().splitlines() if log.exists() else []

    def _csv_rows(self) -> list[str]:
        csv = self._tmp / "city" / "cost_history.csv"
        return csv.read_text().strip().splitlines() if csv.exists() else []

    def test_empty_csv_one_closed_bead_invokes_writer(self) -> None:
        bead = _bead("el-test1", story_id="EL-101")
        gc = _make_gc_fake(self._tmp, "elder", str(self._tmp / "rig"), [bead])
        writer = _make_writer_fake(self._tmp, self._tmp / "city" / "cost_history.csv")
        lister = _make_rig_lister_fake(self._tmp, "elder", str(self._tmp / "rig"))

        result = _run_sweep(self._tmp, gc, writer, lister)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(self._invocations(), ["el-test1|elder"])

    def test_already_recorded_pair_skips_writer(self) -> None:
        # Pre-populate CSV with a row for (EL-202, elder)
        csv = self._tmp / "city" / "cost_history.csv"
        csv.write_text(
            "timestamp,story_id,phase,session_id,duration_seconds,cost_usd,rig\n"
            "2026-05-23T00:00:00Z,EL-202,worker,sess-prior,100,1.0,elder\n"
        )
        bead = _bead("el-test2", story_id="EL-202")
        gc = _make_gc_fake(self._tmp, "elder", str(self._tmp / "rig"), [bead])
        writer = _make_writer_fake(self._tmp, csv)
        lister = _make_rig_lister_fake(self._tmp, "elder", str(self._tmp / "rig"))

        result = _run_sweep(self._tmp, gc, writer, lister)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            self._invocations(),
            [],
            msg="writer should not have been invoked for already-recorded pair",
        )

    def test_two_consecutive_runs_writer_invoked_once(self) -> None:
        bead = _bead("el-test3", story_id="EL-303")
        gc = _make_gc_fake(self._tmp, "elder", str(self._tmp / "rig"), [bead])
        writer = _make_writer_fake(self._tmp, self._tmp / "city" / "cost_history.csv")
        lister = _make_rig_lister_fake(self._tmp, "elder", str(self._tmp / "rig"))

        result1 = _run_sweep(self._tmp, gc, writer, lister)
        self.assertEqual(result1.returncode, 0, msg=result1.stderr)
        rows_after_first = len(self._csv_rows())

        result2 = _run_sweep(self._tmp, gc, writer, lister)
        self.assertEqual(result2.returncode, 0, msg=result2.stderr)
        rows_after_second = len(self._csv_rows())

        self.assertEqual(
            rows_after_first,
            rows_after_second,
            msg="second sweep run should not have added rows (idempotent)",
        )
        self.assertEqual(
            self._invocations(),
            ["el-test3|elder"],
            msg="writer should have been invoked exactly once across two runs",
        )

    def test_wisp_bead_is_filtered(self) -> None:
        beads = [
            _bead("el-test4", story_id="EL-404"),
            _bead("el-wisp-xyz", story_id="WISP-001"),
        ]
        gc = _make_gc_fake(self._tmp, "elder", str(self._tmp / "rig"), beads)
        writer = _make_writer_fake(self._tmp, self._tmp / "city" / "cost_history.csv")
        lister = _make_rig_lister_fake(self._tmp, "elder", str(self._tmp / "rig"))

        result = _run_sweep(self._tmp, gc, writer, lister)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            self._invocations(),
            ["el-test4|elder"],
            msg="wisp bead should have been filtered; only el-test4 should be processed",
        )


class CostRollupLimitAndWindowTests(unittest.TestCase):
    """Bug fixes (2026-06-19): the closed-bead query must pass --limit (without
    it the default 50 rows are all order-wisps, so story beads never surface —
    the silent stall from ~2026-06-11), and the sweep must honor a recency
    window so it bounds candidates instead of reprocessing every historical
    closed bead each run."""

    def setUp(self) -> None:
        self._tmpdir_ctx = TemporaryDirectory()
        self._tmp = Path(self._tmpdir_ctx.name)
        (self._tmp / "city").mkdir()
        (self._tmp / "city" / "city.toml").write_text("[city]\n")

    def tearDown(self) -> None:
        self._tmpdir_ctx.cleanup()

    def _invocations(self) -> list[str]:
        log = self._tmp / "writer-invocations.log"
        return log.read_text().strip().splitlines() if log.exists() else []

    def test_list_query_passes_high_limit(self) -> None:
        bead = _bead("el-lim1", story_id="EL-901")
        gc = _make_gc_fake(self._tmp, "elder", str(self._tmp / "rig"), [bead])
        writer = _make_writer_fake(self._tmp, self._tmp / "city" / "cost_history.csv")
        lister = _make_rig_lister_fake(self._tmp, "elder", str(self._tmp / "rig"))

        result = _run_sweep(self._tmp, gc, writer, lister)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        argv = (self._tmp / "gc-argv.log").read_text()
        list_lines = [ln for ln in argv.splitlines() if "list" in ln and "--status=closed" in ln]
        self.assertTrue(list_lines, msg=f"no closed-bead list call in gc argv: {argv!r}")
        self.assertTrue(
            all("--limit" in ln for ln in list_lines),
            msg=f"closed-bead list call must pass --limit (50-default+wisp stall); got {list_lines!r}",
        )

    def test_recency_window_skips_old_bead(self) -> None:
        old = _bead("el-old", story_id="EL-OLD", closed_at="2026-01-01T00:00:00Z")
        recent = _bead("el-new", story_id="EL-NEW", closed_at="2026-06-15T00:00:00Z")
        gc = _make_gc_fake(self._tmp, "elder", str(self._tmp / "rig"), [old, recent])
        writer = _make_writer_fake(self._tmp, self._tmp / "city" / "cost_history.csv")
        lister = _make_rig_lister_fake(self._tmp, "elder", str(self._tmp / "rig"))

        result = _run_sweep(
            self._tmp, gc, writer, lister, env_extra={"SDLC_COST_ROLLUP_SINCE": "2026-06-01"}
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            self._invocations(),
            ["el-new|elder"],
            msg="only the bead closed on/after SINCE should be processed",
        )


if __name__ == "__main__":
    unittest.main()
