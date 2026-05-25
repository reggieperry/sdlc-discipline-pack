"""Tests for stories.py cmd_file cross-batch dep-edge translation (pack #152).

Symptom this regression test pins: filing a story whose `deps:` list points
at a predecessor filed in an EARLIER `stories.py file` invocation must result
in a `bd dep add <new-bead> --depends-on <pred-bead>` call. Without this
edge, the pool reconciler's `bd ready --metadata-field gc.routed_to=… --unassigned`
query returns the successor immediately and a worker spawns before the
predecessor merges — the race the dep edge is supposed to prevent.

The fix lives in `cmd_file`'s second pass after `bd create --graph`. The
graph plan itself can't carry cross-batch edges (bd errors on a `to_key`
not in the plan's `nodes:` list), so the second pass calls `bd dep add`
explicitly for each cross-batch dep.

Test scaffold: inline `bd` fake on PATH that handles `bd create --graph`
+ `bd dep add` + responds to the prefix lookup. Filesystem rig at a
tempdir with two story specs (predecessor already filed; successor
status=ready). Run `stories.py file EL-101` as a subprocess; inspect
the fake's argv log to assert the dep-add call landed.

Three scenarios:

1. Successor with one cross-batch dep → exactly one `bd dep add` call to
   the predecessor's bead.
2. Successor with a dep that doesn't have `filed_as_bead` yet → cmd_file
   exits nonzero with a clear error; no `bd dep add` is issued.
3. Successor with both an in-batch dep and a cross-batch dep → only the
   cross-batch one produces a `bd dep add` (the in-batch goes through
   the graph plan's edges, not visible to the test's argv log beyond the
   `bd create --graph` invocation).

stdlib-only (unittest + tempfile + subprocess + textwrap). Matches pack
convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_stories_cmd_file_cross_batch_deps -v
"""

from __future__ import annotations

import os
import stat
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STORIES_PY = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "overlay"
    / "per-provider"
    / "claude"
    / ".claude"
    / "sdlc-discipline"
    / "stories.py"
)
assert STORIES_PY.exists(), f"stories.py not found at {STORIES_PY}"


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_bd_fake(tmp: Path, succ_bead: str) -> Path:
    """Inline `bd` fake covering the four subcommands cmd_file calls.

    - ``bd create --graph <path>`` → echoes ``EL-101 -> <succ_bead>`` so
      ``parse_bd_create_output`` can pick up the assignment.
    - ``bd dep add ...`` → exit 0, logging argv.
    - ``bd update <bead> --defer <date> --set-metadata <kv>`` → exit 0,
      logging argv. (pack #154 — merge-gating defer + metadata tag.)
    - Anything else → exit 0 silently (defensive; cmd_file does not call
      anything else in the happy path).

    Argv recorded to ``<tmp>/bd-argv.log`` one call per line.
    """
    bd = tmp / "bd"
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/bd-argv.log"\n'
        'if [ "$1" = "create" ] && [ "$2" = "--graph" ]; then\n'
        f'    echo "EL-101 -> {succ_bead}"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "dep" ] && [ "$2" = "add" ]; then\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "update" ]; then\n'
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_exec(bd, body)
    return bd


def _write_spec(stories_dir: Path, story_id: str, body: str) -> Path:
    path = stories_dir / f"{story_id}-test.md"
    path.write_text(body)
    return path


def _make_rig(tmp: Path) -> Path:
    rig = tmp / "rig"
    rig.mkdir()
    (rig / "stories").mkdir()
    beads_dir = rig / ".beads"
    beads_dir.mkdir()
    (beads_dir / "config.yaml").write_text('issue-prefix: "bd"\n')
    return rig


def _run_cmd_file(rig: Path, tmp: Path, *story_ids: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{tmp}:{os.environ.get('PATH', '')}",
    }
    return subprocess.run(
        ["python3", str(STORIES_PY), "file", *story_ids],
        cwd=rig,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


PRED_FILED_SPEC = textwrap.dedent("""\
    ---
    story_id: EL-100
    title: Predecessor (already filed)
    status: filed
    filed_as_bead: bd-pred001
    ---

    # body
    """)

SUCC_WITH_CROSS_BATCH_DEP = textwrap.dedent("""\
    ---
    story_id: EL-101
    title: Successor with cross-batch dep on EL-100
    status: ready
    deps:
      - EL-100
    ---

    # body
    """)

PRED_UNFILED_SPEC = textwrap.dedent("""\
    ---
    story_id: EL-100
    title: Predecessor (not yet filed)
    status: ready
    ---

    # body
    """)

PRED_MERGED_SPEC = textwrap.dedent("""\
    ---
    story_id: EL-100
    title: Predecessor (already merged)
    status: closed
    filed_as_bead: bd-pred001
    merged_pr: "#100"
    ---

    # body
    """)


class CmdFileCrossBatchDepTests(unittest.TestCase):
    """Pack #152 — cross-batch dep-edge translation."""

    def setUp(self) -> None:
        self._tmpdir_ctx = TemporaryDirectory()
        self._tmp = Path(self._tmpdir_ctx.name)
        self._rig = _make_rig(self._tmp)

    def tearDown(self) -> None:
        self._tmpdir_ctx.cleanup()

    def _bd_calls(self) -> list[str]:
        log = self._tmp / "bd-argv.log"
        return log.read_text().strip().splitlines() if log.exists() else []

    def test_cross_batch_dep_emits_bd_dep_add(self) -> None:
        """Successor with a dep on an already-filed predecessor → bd dep add called."""
        _write_spec(self._rig / "stories", "EL-100", PRED_FILED_SPEC)
        _write_spec(self._rig / "stories", "EL-101", SUCC_WITH_CROSS_BATCH_DEP)
        _make_bd_fake(self._tmp, succ_bead="bd-succ002")

        result = _run_cmd_file(self._rig, self._tmp, "EL-101")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._bd_calls()
        dep_add_calls = [c for c in calls if c.startswith("dep add ")]
        self.assertEqual(
            len(dep_add_calls),
            1,
            msg=f"expected exactly one `bd dep add` call; got {dep_add_calls}",
        )
        self.assertIn("bd-succ002", dep_add_calls[0])
        self.assertIn("--depends-on", dep_add_calls[0])
        self.assertIn("bd-pred001", dep_add_calls[0])

    def test_cross_batch_dep_defers_successor_with_marker(self) -> None:
        """pack #154: successor is deferred + tagged with predecessor bead id."""
        _write_spec(self._rig / "stories", "EL-100", PRED_FILED_SPEC)
        _write_spec(self._rig / "stories", "EL-101", SUCC_WITH_CROSS_BATCH_DEP)
        _make_bd_fake(self._tmp, succ_bead="bd-succ002")

        result = _run_cmd_file(self._rig, self._tmp, "EL-101")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._bd_calls()
        update_calls = [c for c in calls if c.startswith("update bd-succ002")]
        self.assertEqual(
            len(update_calls),
            1,
            msg=f"expected exactly one `bd update bd-succ002` call; got {update_calls}",
        )
        update_call = update_calls[0]
        self.assertIn("--defer", update_call)
        self.assertIn("2099-01-01", update_call)
        self.assertIn("--set-metadata", update_call)
        self.assertIn("cross_batch_dep_predecessors=bd-pred001", update_call)

    def test_multi_dep_joins_predecessors_with_comma(self) -> None:
        """A successor with two cross-batch deps gets one defer with both predecessor bead ids."""
        pred_b_spec = textwrap.dedent("""\
            ---
            story_id: EL-099
            title: Predecessor B (already filed)
            status: filed
            filed_as_bead: bd-predB001
            ---

            # body
            """)
        succ_multi_spec = textwrap.dedent("""\
            ---
            story_id: EL-101
            title: Successor with two cross-batch deps
            status: ready
            deps:
              - EL-099
              - EL-100
            ---

            # body
            """)
        _write_spec(self._rig / "stories", "EL-099", pred_b_spec)
        _write_spec(self._rig / "stories", "EL-100", PRED_FILED_SPEC)
        _write_spec(self._rig / "stories", "EL-101", succ_multi_spec)
        _make_bd_fake(self._tmp, succ_bead="bd-succ002")

        result = _run_cmd_file(self._rig, self._tmp, "EL-101")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._bd_calls()
        update_calls = [c for c in calls if c.startswith("update bd-succ002")]
        self.assertEqual(len(update_calls), 1)
        update_call = update_calls[0]
        # Both predecessor bead ids present, comma-joined
        self.assertIn("bd-predB001", update_call)
        self.assertIn("bd-pred001", update_call)
        self.assertIn("cross_batch_dep_predecessors=", update_call)

    def test_unfiled_predecessor_hard_fails(self) -> None:
        """Successor deps on a predecessor with no filed_as_bead → cmd_file exits nonzero."""
        _write_spec(self._rig / "stories", "EL-100", PRED_UNFILED_SPEC)
        _write_spec(self._rig / "stories", "EL-101", SUCC_WITH_CROSS_BATCH_DEP)
        _make_bd_fake(self._tmp, succ_bead="bd-succ002")

        result = _run_cmd_file(self._rig, self._tmp, "EL-101")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EL-100", result.stderr)
        self.assertIn("filed_as_bead", result.stderr)
        # bd create --graph still runs (the successor IS filed at the bd layer);
        # the cross-batch second pass is where the failure surfaces. So we
        # expect a bd create call but NO bd dep add call.
        calls = self._bd_calls()
        self.assertTrue(any(c.startswith("create --graph") for c in calls))
        self.assertFalse(
            any(c.startswith("dep add ") for c in calls),
            msg=f"bd dep add should NOT have been called; got {calls}",
        )

    def test_merged_predecessor_skips_both_dep_add_and_defer(self) -> None:
        """pack #157: when the predecessor spec has `status: closed` + a
        populated `merged_pr`, the cross-batch machinery treats the
        predecessor as already-merged. No `bd dep add` is issued
        (the predecessor bead is gone, the call would fail), and no
        `bd update --defer` fires (the race the defer guards against is
        already closed). The successor enters bd ready immediately.

        Without this guard, the successor was deferred to 2099-01-01 and
        invisible to the chain pool reconciler — the exact failure that
        bit Elder filings on 2026-05-24.
        """
        _write_spec(self._rig / "stories", "EL-100", PRED_MERGED_SPEC)
        _write_spec(self._rig / "stories", "EL-101", SUCC_WITH_CROSS_BATCH_DEP)
        _make_bd_fake(self._tmp, succ_bead="bd-succ002")

        result = _run_cmd_file(self._rig, self._tmp, "EL-101")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._bd_calls()
        # No bd dep add — would have failed because the predecessor bead
        # is gone (story-spec layer says closed; bd layer agrees: removed).
        self.assertFalse(
            any(c.startswith("dep add ") for c in calls),
            msg=f"bd dep add should NOT have been called for a merged predecessor; got {calls}",
        )
        # No bd update --defer — the race the defer guards against is closed
        # the moment the predecessor's PR merges.
        update_calls = [c for c in calls if c.startswith("update bd-succ002")]
        self.assertEqual(
            len(update_calls),
            0,
            msg=f"bd update --defer should NOT have been called for a merged predecessor; got {update_calls}",
        )

    def test_partial_merged_predecessors_only_defers_pending_ones(self) -> None:
        """pack #157: when a successor has multiple cross-batch deps and SOME
        are merged while others are still pending, the defer should fire only
        with the pending predecessors named in `cross_batch_dep_predecessors`.
        The merged predecessor is filtered out of the comma-joined list.
        """
        pred_pending_spec = textwrap.dedent("""\
            ---
            story_id: EL-099
            title: Predecessor B (still pending)
            status: filed
            filed_as_bead: bd-predB001
            ---

            # body
            """)
        succ_multi_spec = textwrap.dedent("""\
            ---
            story_id: EL-101
            title: Successor with one merged + one pending dep
            status: ready
            deps:
              - EL-099
              - EL-100
            ---

            # body
            """)
        _write_spec(self._rig / "stories", "EL-099", pred_pending_spec)
        _write_spec(self._rig / "stories", "EL-100", PRED_MERGED_SPEC)
        _write_spec(self._rig / "stories", "EL-101", succ_multi_spec)
        _make_bd_fake(self._tmp, succ_bead="bd-succ002")

        result = _run_cmd_file(self._rig, self._tmp, "EL-101")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._bd_calls()
        # bd dep add fires for the pending predecessor, not the merged one
        dep_add_calls = [c for c in calls if c.startswith("dep add ")]
        self.assertEqual(len(dep_add_calls), 1, msg=f"expected one dep add; got {dep_add_calls}")
        self.assertIn("bd-predB001", dep_add_calls[0])
        self.assertNotIn("bd-pred001", dep_add_calls[0])
        # defer carries only the pending predecessor bead id
        update_calls = [c for c in calls if c.startswith("update bd-succ002")]
        self.assertEqual(len(update_calls), 1, msg=f"expected one defer; got {update_calls}")
        self.assertIn("cross_batch_dep_predecessors=bd-predB001", update_calls[0])
        self.assertNotIn("bd-pred001", update_calls[0])

    def test_no_cross_batch_deps_is_quiet(self) -> None:
        """Successor with no cross-batch deps → no `bd dep add` invocation."""
        no_dep_spec = textwrap.dedent("""\
            ---
            story_id: EL-101
            title: Successor with no deps
            status: ready
            ---

            # body
            """)
        _write_spec(self._rig / "stories", "EL-101", no_dep_spec)
        _make_bd_fake(self._tmp, succ_bead="bd-succ002")

        result = _run_cmd_file(self._rig, self._tmp, "EL-101")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._bd_calls()
        self.assertFalse(
            any(c.startswith("dep add ") for c in calls),
            msg=f"bd dep add should NOT have been called; got {calls}",
        )


def _make_bd_fake_multi(
    tmp: Path, beads: dict[str, str], bead_states: dict[str, dict] | None = None
) -> Path:
    """bd fake supporting multi-spec batches + bd show queries.

    - `beads` maps story_id → bead_id. The mock's `bd create --graph` echo
      emits one line per entry so `parse_bd_create_output` picks up every
      assignment.
    - `bead_states` (optional) maps bead_id → JSON-serializable dict the
      mock returns on `bd show <bead-id> --json`. Used by the stale-
      frontmatter face to assert the helper's defensive check consults
      the bd layer when the spec frontmatter is stale.
    """
    bd = tmp / "bd"
    create_output = "\n".join(f"{sid} -> {bid}" for sid, bid in beads.items())
    bead_states = bead_states or {}
    show_branches = []
    for bead_id, state in bead_states.items():
        import json as _json

        state_json = _json.dumps([state]).replace('"', '\\"')
        show_branches.append(
            f'if [ "$1" = "show" ] && [ "$2" = "{bead_id}" ] && [ "$3" = "--json" ]; then\n'
            f'    echo "{state_json}"\n'
            "    exit 0\n"
            "fi\n"
        )
    body = (
        "#!/bin/bash\n"
        f'echo "$@" >> "{tmp}/bd-argv.log"\n'
        'if [ "$1" = "create" ] && [ "$2" = "--graph" ]; then\n'
        f"    cat <<'BEAD_EOF'\n{create_output}\nBEAD_EOF\n"
        "    exit 0\n"
        "fi\n" + "".join(show_branches) + 'if [ "$1" = "show" ] && [ "$3" = "--json" ]; then\n'
        '    echo "[]"\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "dep" ] && [ "$2" = "add" ]; then\n'
        "    exit 0\n"
        "fi\n"
        'if [ "$1" = "update" ]; then\n'
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _write_exec(bd, body)
    return bd


class CmdFileCrossBatchDepIssue164Tests(unittest.TestCase):
    """Pack #164 — both faces of the cross-batch dep machinery edge cases.

    Face 1 (within-batch chain dependents): filing N specs in one batch
    where each depends on the previous in the batch should defer every
    dependent, not just the first. Pre-#164 only the cross-batch
    successor (whose predecessor was in a prior batch) got the defer +
    metadata treatment; within-batch successors got the bd-dep edge via
    `bd create --graph` but no defer, so their workers spawned out-of-
    order against incomplete predecessor state.

    Face 2 (stale finalizer writeback): `_predecessor_already_merged`
    reads only the spec frontmatter. When a chain finalizer fails to
    write back `status: closed` + `merged_pr`, the helper returns False
    and the defer fires on the new dependent — even though the
    predecessor's bead has reached merge-equivalent terminal state. The
    fix extends the helper with a defensive bd-layer check.
    """

    def setUp(self) -> None:
        self._tmpdir_ctx = TemporaryDirectory()
        self._tmp = Path(self._tmpdir_ctx.name)
        self._rig = _make_rig(self._tmp)

    def tearDown(self) -> None:
        self._tmpdir_ctx.cleanup()

    def _bd_calls(self) -> list[str]:
        log = self._tmp / "bd-argv.log"
        return log.read_text().strip().splitlines() if log.exists() else []

    def test_within_batch_chain_dep_defers_each_successor(self) -> None:
        """Face 1: file EL-101 + EL-102 in one batch where EL-102 deps on
        EL-101. Both deferred-and-tagged setup must fire for EL-102 against
        EL-101's just-assigned bead, even though EL-101 is being filed in
        the same operation (within-batch predecessor).
        """
        pred_spec = textwrap.dedent("""\
            ---
            story_id: EL-101
            title: First in batch
            status: ready
            ---

            # body
            """)
        succ_spec = textwrap.dedent("""\
            ---
            story_id: EL-102
            title: Second in batch (deps on EL-101)
            status: ready
            deps:
              - EL-101
            ---

            # body
            """)
        _write_spec(self._rig / "stories", "EL-101", pred_spec)
        _write_spec(self._rig / "stories", "EL-102", succ_spec)
        _make_bd_fake_multi(self._tmp, beads={"EL-101": "bd-101", "EL-102": "bd-102"})

        result = _run_cmd_file(self._rig, self._tmp, "EL-101", "EL-102")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._bd_calls()
        # The within-batch successor MUST receive defer + metadata against
        # the within-batch predecessor's just-assigned bead.
        update_calls = [c for c in calls if c.startswith("update bd-102")]
        self.assertEqual(
            len(update_calls),
            1,
            msg=(
                "expected exactly one `bd update bd-102 --defer ...` call for the "
                f"within-batch successor; got {update_calls}"
            ),
        )
        update_call = update_calls[0]
        self.assertIn("--defer", update_call)
        self.assertIn("2099-01-01", update_call)
        self.assertIn("--set-metadata", update_call)
        self.assertIn("cross_batch_dep_predecessors=bd-101", update_call)
        # The within-batch predecessor (EL-101) has no deps, so no defer
        # should fire for it.
        pred_update_calls = [c for c in calls if c.startswith("update bd-101")]
        self.assertEqual(
            len(pred_update_calls),
            0,
            msg=f"predecessor EL-101 has no deps; should not be deferred; got {pred_update_calls}",
        )

    def test_stale_spec_with_merged_bead_admits_via_defensive_check(self) -> None:
        """Face 2: predecessor spec frontmatter says `status: filed` (stale —
        chain finalizer didn't write back) but the bd layer shows the bead
        is `closed` with `final_state: merged`. The defensive check in
        `_predecessor_already_merged` must admit the predecessor; no defer
        should fire on the successor.
        """
        # Predecessor spec frontmatter is stale: status: filed (the chain
        # finalizer's writeback failed silently), filed_as_bead populated.
        pred_stale_spec = textwrap.dedent("""\
            ---
            story_id: EL-100
            title: Predecessor (stale frontmatter — finalizer writeback failed)
            status: filed
            filed_as_bead: bd-pred001
            ---

            # body
            """)
        _write_spec(self._rig / "stories", "EL-100", pred_stale_spec)
        _write_spec(self._rig / "stories", "EL-101", SUCC_WITH_CROSS_BATCH_DEP)
        # The bd layer's view of bd-pred001 is closed + final_state=merged
        # (chain reached merge-equivalent terminal state even though the
        # spec frontmatter writeback failed).
        _make_bd_fake_multi(
            self._tmp,
            beads={"EL-101": "bd-succ002"},
            bead_states={
                "bd-pred001": {
                    "id": "bd-pred001",
                    "status": "closed",
                    "metadata": {"final_state": "merged"},
                },
            },
        )

        result = _run_cmd_file(self._rig, self._tmp, "EL-101")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._bd_calls()
        # No bd update --defer should fire — the defensive bd-layer check
        # admitted the predecessor as already-merged.
        update_calls = [c for c in calls if c.startswith("update bd-succ002")]
        self.assertEqual(
            len(update_calls),
            0,
            msg=(
                "stale frontmatter + closed-merged bead → defensive check should "
                f"admit; expected no defer call; got {update_calls}"
            ),
        )
        # No bd dep add should fire either — same reasoning, the predecessor
        # is gone at the bd layer.
        dep_add_calls = [c for c in calls if c.startswith("dep add ")]
        self.assertEqual(
            len(dep_add_calls),
            0,
            msg=(
                "stale frontmatter + closed-merged bead → defensive check should "
                f"skip bd dep add; got {dep_add_calls}"
            ),
        )

    def test_stale_spec_with_non_merged_bead_still_defers(self) -> None:
        """Face 2 negative: predecessor spec is stale AND the bd layer shows
        the bead is closed but with a non-merge-equivalent `final_state`
        (e.g., the chain abandoned without producing a merge). The defensive
        check must NOT admit; the defer should fire so the successor waits
        for operator triage.
        """
        pred_stale_spec = textwrap.dedent("""\
            ---
            story_id: EL-100
            title: Predecessor (stale; bead closed but not merged)
            status: filed
            filed_as_bead: bd-pred001
            ---

            # body
            """)
        _write_spec(self._rig / "stories", "EL-100", pred_stale_spec)
        _write_spec(self._rig / "stories", "EL-101", SUCC_WITH_CROSS_BATCH_DEP)
        # Bead is closed but `final_state` is missing — chain finished
        # without a merge (e.g., abandoned, manually closed, error path).
        # The defensive check should refuse to admit on this signal.
        _make_bd_fake_multi(
            self._tmp,
            beads={"EL-101": "bd-succ002"},
            bead_states={
                "bd-pred001": {
                    "id": "bd-pred001",
                    "status": "closed",
                    "metadata": {},
                },
            },
        )

        result = _run_cmd_file(self._rig, self._tmp, "EL-101")

        # The bd dep add path may fail (predecessor bead doesn't actually
        # exist in the test fixture; the mock returns success but the real
        # bd would error). The successor's defer is what we care about.
        calls = self._bd_calls()
        update_calls = [c for c in calls if c.startswith("update bd-succ002")]
        self.assertEqual(
            len(update_calls),
            1,
            msg=(
                "stale frontmatter + closed-but-not-merged bead → defensive check "
                f"should NOT admit; expected one defer call; got {update_calls}"
            ),
        )
        self.assertIn("--defer", update_calls[0])
        self.assertIn("cross_batch_dep_predecessors=bd-pred001", update_calls[0])


if __name__ == "__main__":
    unittest.main()
