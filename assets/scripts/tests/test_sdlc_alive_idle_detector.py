"""Tests for sdlc-alive-idle-detector.sh (pack#86 — Mode C workaround).

Black-box subprocess tests. Each test stands up a tempdir holding fake
`gc`, `tmux`, and `sdlc-notify.sh` binaries that record their argv. The
script under test is invoked with a controlled env; assertions read the
recorded argv to verify the call sequence.

stdlib-only (`unittest` + tempfile + subprocess + textwrap). Matches the
pack convention (`test_sdlc_drain_ack_recover.py`, `test_sdlc_notify.py`).

Run with:

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sdlc-alive-idle-detector.sh"
assert SCRIPT_PATH.exists(), f"sdlc-alive-idle-detector.sh not found at {SCRIPT_PATH}"


# Pane snapshots — match the literal Claude Code TUI strings the detector parses.

PANE_AT_PROMPT_IDLE = """\
  Some prior tool output...

✻ Brewed for 17m 43s

❯
────────────────────────────────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ctrl+t to hide tasks · ← fo…
                                        new task? /clear to save 176.9k tokens
"""

PANE_BUSY_IMPLEMENTING = """\
  Some prior tool output...

✽ Implementing…
  ⎿  ◼ Step 4: implement
     ◻ Step 5: self-audit

❯ continue
────────────────────────────────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ctrl+t t…
"""

PANE_BUSY_BREWED = """\
  Some prior tool output...

✻ Brewed for 5s

❯
────────────────────────────────────────────────────────────────────────────────
  ⏵⏵ esc to interrupt · ctrl+t t…
"""


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_gc(
    tmp: Path,
    *,
    bd_list_json: str = "[]",
    session_list_json: str = "[]",
    submit_exit: int = 0,
) -> Path:
    """Build a fake `gc` binary that dispatches on subcommand and records argv.

    Recorded:
    - argv → <tmp>/gc-argv.log (one line per invocation)
    - sequenced calls → <tmp>/call-sequence.log

    Dispatch:
    - `gc bd list --status=in_progress --json` → echoes bd_list_json
    - `gc session list --json` → echoes session_list_json
    - `gc session submit <id> "continue" --intent default` → exits submit_exit
    - everything else → exits 0
    """
    path = tmp / "gc"
    body = textwrap.dedent(
        f"""\
        #!/bin/bash
        echo "$@" >> "{tmp}/gc-argv.log"
        echo "gc $@" >> "{tmp}/call-sequence.log"
        if [ "$1" = "bd" ] && [ "$2" = "list" ]; then
            cat <<'__BD_EOF__'
{bd_list_json}
__BD_EOF__
            exit 0
        fi
        if [ "$1" = "session" ] && [ "$2" = "list" ]; then
            cat <<'__SL_EOF__'
{session_list_json}
__SL_EOF__
            exit 0
        fi
        if [ "$1" = "session" ] && [ "$2" = "submit" ]; then
            exit {submit_exit}
        fi
        exit 0
        """
    )
    _write_executable(path, body)
    return path


def _fake_tmux(tmp: Path, pane_content: str) -> Path:
    """Build a fake `tmux` binary that returns pane_content for capture-pane.

    Dispatch:
    - `tmux capture-pane ... -p` → emits pane_content to stdout
    - everything else → exits 0

    Records argv to <tmp>/tmux-argv.log.
    """
    path = tmp / "tmux"
    # Encode pane content as a Python expression to handle any shell-unfriendly chars.
    body = textwrap.dedent(
        f"""\
        #!/bin/bash
        echo "$@" >> "{tmp}/tmux-argv.log"
        echo "tmux $@" >> "{tmp}/call-sequence.log"
        for arg in "$@"; do
            if [ "$arg" = "capture-pane" ]; then
                cat <<'__PANE_EOF__'
{pane_content}
__PANE_EOF__
                exit 0
            fi
        done
        exit 0
        """
    )
    _write_executable(path, body)
    return path


def _fake_recorder(tmp: Path, name: str, *, exit_code: int = 0) -> Path:
    """Build a fake binary that records argv and exits."""
    path = tmp / name
    body = textwrap.dedent(
        f"""\
        #!/bin/bash
        echo "$@" >> "{tmp}/{name}-argv.log"
        echo "{name} $@" >> "{tmp}/call-sequence.log"
        cat >> "{tmp}/{name}-stdin.log" 2>/dev/null || true
        exit {exit_code}
        """
    )
    _write_executable(path, body)
    return path


def _bead(
    *,
    bead_id: str = "el-test",
    assignee: str = "sdlc-discipline__worker-bl-test",
    rig: str = "elder",
    work_dir: str = "/tmp/wd",
    status: str = "in_progress",
) -> dict:
    return {
        "id": bead_id,
        "status": status,
        "assignee": assignee,
        "metadata": {
            "rig": rig,
            "work_dir": work_dir,
            "worker": {"session_id": assignee},
        },
    }


def _session(
    *,
    session_id: str = "sdlc-discipline__worker-bl-test",
    state: str = "active",
    pane: str = "bright-lights:sdlc-discipline__worker-bl-test",
    tmux_socket: str = "/tmp/tmux-1000/bright-lights",
) -> dict:
    return {
        "id": session_id,
        "state": state,
        "metadata": {
            "session_name": session_id,
            "tmux_pane": pane,
            "tmux_socket": tmux_socket,
        },
    }


def _events_file(tmp: Path, *, bead_id: str, last_update_seconds_ago: int) -> Path:
    """Write a controlled events.jsonl with a single bead.updated entry.

    The detector reads this to compute the gap from now back to the most-recent
    bead.updated event for the given bead_id.
    """
    path = tmp / "events.jsonl"
    now = int(time.time())
    ts_epoch = now - last_update_seconds_ago
    ts_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts_epoch))
    line = json.dumps({"ts": ts_iso, "type": "bead.updated", "message": f"{bead_id}: stuff"})
    path.write_text(line + "\n")
    return path


def _base_env(tmp: Path, gc_path: Path, tmux_path: Path, notify_path: Path) -> dict:
    """Standard test env wiring all injection points + clearing PATH."""
    return {
        **os.environ,
        "PATH": f"{tmp}:{os.environ.get('PATH', '')}",
        "SDLC_ALIVE_IDLE_GC": str(gc_path),
        "SDLC_ALIVE_IDLE_TMUX": str(tmux_path),
        "SDLC_ALIVE_IDLE_NOTIFY": str(notify_path),
        "SDLC_ALIVE_IDLE_STATE_DIR": str(tmp / "state"),
    }


class FeatureGateTests(unittest.TestCase):
    """Cycle 1 — the script ships disabled."""

    def test_exits_zero_when_enabled_env_unset(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            gc = _fake_gc(tmp, bd_list_json=json.dumps([_bead()]))
            tmux = _fake_tmux(tmp, PANE_AT_PROMPT_IDLE)
            notify = _fake_recorder(tmp, "sdlc-notify.sh")

            env = _base_env(tmp, gc, tmux, notify)
            env.pop("SDLC_ALIVE_IDLE_DETECTOR_ENABLED", None)

            result = subprocess.run(
                [str(SCRIPT_PATH)],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(
                result.returncode,
                0,
                f"disabled gate should exit 0; stderr={result.stderr!r}",
            )
            self.assertFalse(
                (tmp / "gc-argv.log").exists(),
                "disabled gate should not invoke gc",
            )
            self.assertFalse(
                (tmp / "tmux-argv.log").exists(),
                "disabled gate should not invoke tmux",
            )

    def test_exits_zero_when_enabled_env_is_false(self) -> None:
        with TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            gc = _fake_gc(tmp, bd_list_json=json.dumps([_bead()]))
            tmux = _fake_tmux(tmp, PANE_AT_PROMPT_IDLE)
            notify = _fake_recorder(tmp, "sdlc-notify.sh")

            env = _base_env(tmp, gc, tmux, notify)
            env["SDLC_ALIVE_IDLE_DETECTOR_ENABLED"] = "false"

            result = subprocess.run(
                [str(SCRIPT_PATH)],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0)
            self.assertFalse((tmp / "gc-argv.log").exists())


class DetectionTests(unittest.TestCase):
    """Cycle 2 — two-stage detection."""

    def _run(
        self,
        *,
        bead_list: list,
        session_list: list,
        pane: str,
        events_age_seconds: int,
        threshold_minutes: int = 20,
    ) -> tuple[subprocess.CompletedProcess, Path]:
        tmp = Path(self._tmp_str)
        gc = _fake_gc(
            tmp,
            bd_list_json=json.dumps(bead_list),
            session_list_json=json.dumps(session_list),
        )
        tmux = _fake_tmux(tmp, pane)
        notify = _fake_recorder(tmp, "sdlc-notify.sh")

        bead_id = bead_list[0]["id"] if bead_list else "el-test"
        events = _events_file(tmp, bead_id=bead_id, last_update_seconds_ago=events_age_seconds)

        env = _base_env(tmp, gc, tmux, notify)
        env["SDLC_ALIVE_IDLE_DETECTOR_ENABLED"] = "true"
        env["SDLC_ALIVE_IDLE_EVENTS_PATH"] = str(events)
        env["SDLC_ALIVE_IDLE_THRESHOLD_MINUTES"] = str(threshold_minutes)

        result = subprocess.run(
            [str(SCRIPT_PATH)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result, tmp

    def setUp(self) -> None:
        self._tmpdir_ctx = TemporaryDirectory()
        self._tmp_str = self._tmpdir_ctx.name

    def tearDown(self) -> None:
        self._tmpdir_ctx.cleanup()

    def test_no_in_progress_beads_exits_clean(self) -> None:
        result, tmp = self._run(
            bead_list=[],
            session_list=[],
            pane=PANE_AT_PROMPT_IDLE,
            events_age_seconds=0,
        )
        self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
        # Submit must not fire; tmux must not be invoked.
        self.assertFalse(
            (tmp / "sdlc-notify.sh-argv.log").exists(),
            "no beads → no notify",
        )

    def test_recent_event_skips_stage_2(self) -> None:
        """Stage 1: gap is small. Detector should not even peek the pane."""
        result, tmp = self._run(
            bead_list=[_bead()],
            session_list=[_session()],
            pane=PANE_AT_PROMPT_IDLE,
            events_age_seconds=60,  # 1 min ago — well under threshold
            threshold_minutes=20,
        )
        self.assertEqual(result.returncode, 0)
        # tmux should never be invoked when stage 1 fails.
        self.assertFalse(
            (tmp / "tmux-argv.log").exists(),
            "recent event → no pane capture",
        )

    def test_busy_pane_skips_submit(self) -> None:
        """Stage 1 trips (old event) but stage 2 fails (busy marker present)."""
        result, tmp = self._run(
            bead_list=[_bead()],
            session_list=[_session()],
            pane=PANE_BUSY_IMPLEMENTING,
            events_age_seconds=1800,  # 30 min ago — past threshold
            threshold_minutes=20,
        )
        self.assertEqual(result.returncode, 0)
        # tmux was called for capture; submit was not.
        self.assertTrue((tmp / "tmux-argv.log").exists())
        gc_calls = (tmp / "gc-argv.log").read_text()
        self.assertNotIn("session submit", gc_calls, "busy pane → no submit")

    def test_brewed_pane_skips_submit(self) -> None:
        """Brewed-for marker is also a busy marker — claude is mid-turn."""
        result, tmp = self._run(
            bead_list=[_bead()],
            session_list=[_session()],
            pane=PANE_BUSY_BREWED,
            events_age_seconds=1800,
            threshold_minutes=20,
        )
        self.assertEqual(result.returncode, 0)
        gc_calls = (tmp / "gc-argv.log").read_text() if (tmp / "gc-argv.log").exists() else ""
        self.assertNotIn("session submit", gc_calls)


class ActionTests(unittest.TestCase):
    """Cycle 3 — both stages trip, action fires."""

    def setUp(self) -> None:
        self._tmpdir_ctx = TemporaryDirectory()
        self._tmp_str = self._tmpdir_ctx.name

    def tearDown(self) -> None:
        self._tmpdir_ctx.cleanup()

    def test_submit_fires_when_both_stages_trip(self) -> None:
        tmp = Path(self._tmp_str)
        bead = _bead(bead_id="el-stuck")
        session = _session(session_id="sdlc-discipline__worker-bl-test")
        gc = _fake_gc(
            tmp,
            bd_list_json=json.dumps([bead]),
            session_list_json=json.dumps([session]),
        )
        tmux = _fake_tmux(tmp, PANE_AT_PROMPT_IDLE)
        notify = _fake_recorder(tmp, "sdlc-notify.sh")
        events = _events_file(tmp, bead_id="el-stuck", last_update_seconds_ago=1800)

        env = _base_env(tmp, gc, tmux, notify)
        env["SDLC_ALIVE_IDLE_DETECTOR_ENABLED"] = "true"
        env["SDLC_ALIVE_IDLE_EVENTS_PATH"] = str(events)
        env["SDLC_ALIVE_IDLE_THRESHOLD_MINUTES"] = "20"

        result = subprocess.run(
            [str(SCRIPT_PATH)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")

        # The submit call should appear in gc argv log
        gc_calls = (tmp / "gc-argv.log").read_text()
        self.assertIn("session submit", gc_calls, f"gc_calls=\n{gc_calls}")
        self.assertIn("continue", gc_calls)
        self.assertIn("sdlc-discipline__worker-bl-test", gc_calls)

        # Notify should have fired
        self.assertTrue(
            (tmp / "sdlc-notify.sh-argv.log").exists(),
            "notify should fire on successful nudge",
        )

    def test_submit_failure_notifies_and_exits_nonzero(self) -> None:
        tmp = Path(self._tmp_str)
        bead = _bead(bead_id="el-stuck")
        session = _session(session_id="sdlc-discipline__worker-bl-test")
        gc = _fake_gc(
            tmp,
            bd_list_json=json.dumps([bead]),
            session_list_json=json.dumps([session]),
            submit_exit=1,  # the submit fails
        )
        tmux = _fake_tmux(tmp, PANE_AT_PROMPT_IDLE)
        notify = _fake_recorder(tmp, "sdlc-notify.sh")
        events = _events_file(tmp, bead_id="el-stuck", last_update_seconds_ago=1800)

        env = _base_env(tmp, gc, tmux, notify)
        env["SDLC_ALIVE_IDLE_DETECTOR_ENABLED"] = "true"
        env["SDLC_ALIVE_IDLE_EVENTS_PATH"] = str(events)

        result = subprocess.run(
            [str(SCRIPT_PATH)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertNotEqual(result.returncode, 0, "submit failure should exit non-zero")
        # Notify should still fire (with failure indication in argv)
        self.assertTrue((tmp / "sdlc-notify.sh-argv.log").exists())


class RateLimitTests(unittest.TestCase):
    """Cycle 4 — per-bead cooldown."""

    def setUp(self) -> None:
        self._tmpdir_ctx = TemporaryDirectory()
        self._tmp_str = self._tmpdir_ctx.name

    def tearDown(self) -> None:
        self._tmpdir_ctx.cleanup()

    def test_recently_nudged_bead_is_skipped(self) -> None:
        """State file says we nudged this bead 1 minute ago → skip."""
        tmp = Path(self._tmp_str)
        bead = _bead(bead_id="el-recent")
        session = _session(session_id="sdlc-discipline__worker-bl-test")
        gc = _fake_gc(
            tmp,
            bd_list_json=json.dumps([bead]),
            session_list_json=json.dumps([session]),
        )
        tmux = _fake_tmux(tmp, PANE_AT_PROMPT_IDLE)
        notify = _fake_recorder(tmp, "sdlc-notify.sh")
        events = _events_file(tmp, bead_id="el-recent", last_update_seconds_ago=1800)

        # Pre-populate the state file: we nudged this bead 60s ago.
        state_dir = tmp / "state"
        state_dir.mkdir()
        now = int(time.time())
        state = {"el-recent": now - 60}
        (state_dir / "alive-idle-nudges.json").write_text(json.dumps(state))

        env = _base_env(tmp, gc, tmux, notify)
        env["SDLC_ALIVE_IDLE_DETECTOR_ENABLED"] = "true"
        env["SDLC_ALIVE_IDLE_EVENTS_PATH"] = str(events)
        env["SDLC_ALIVE_IDLE_NUDGE_COOLDOWN_MINUTES"] = "10"

        result = subprocess.run(
            [str(SCRIPT_PATH)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0)
        gc_calls = (tmp / "gc-argv.log").read_text() if (tmp / "gc-argv.log").exists() else ""
        self.assertNotIn("session submit", gc_calls, "recently-nudged → no submit")

    def test_old_nudge_outside_cooldown_proceeds(self) -> None:
        """State file says we nudged 20 minutes ago — past cooldown — submit fires."""
        tmp = Path(self._tmp_str)
        bead = _bead(bead_id="el-stale")
        session = _session(session_id="sdlc-discipline__worker-bl-test")
        gc = _fake_gc(
            tmp,
            bd_list_json=json.dumps([bead]),
            session_list_json=json.dumps([session]),
        )
        tmux = _fake_tmux(tmp, PANE_AT_PROMPT_IDLE)
        notify = _fake_recorder(tmp, "sdlc-notify.sh")
        events = _events_file(tmp, bead_id="el-stale", last_update_seconds_ago=1800)

        state_dir = tmp / "state"
        state_dir.mkdir()
        now = int(time.time())
        state = {"el-stale": now - 1200}  # 20 min ago
        (state_dir / "alive-idle-nudges.json").write_text(json.dumps(state))

        env = _base_env(tmp, gc, tmux, notify)
        env["SDLC_ALIVE_IDLE_DETECTOR_ENABLED"] = "true"
        env["SDLC_ALIVE_IDLE_EVENTS_PATH"] = str(events)
        env["SDLC_ALIVE_IDLE_NUDGE_COOLDOWN_MINUTES"] = "10"

        result = subprocess.run(
            [str(SCRIPT_PATH)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
        gc_calls = (tmp / "gc-argv.log").read_text()
        self.assertIn("session submit", gc_calls)


class RealGcShapeTests(unittest.TestCase):
    """Cycle 5 — pin the actual shapes the real `gc` CLI returns.

    Surfaced on the v2.21.0 deploy smoke: `gc session list --json` returns
    an object with a `sessions` key, not a bare array. The detector must
    tolerate both shapes since the original tests passed arrays.
    """

    def setUp(self) -> None:
        self._tmpdir_ctx = TemporaryDirectory()
        self._tmp_str = self._tmpdir_ctx.name

    def tearDown(self) -> None:
        self._tmpdir_ctx.cleanup()

    def test_session_list_object_shape_is_parsed(self) -> None:
        """gc session list --json returns {filters, ok, sessions, summary}."""
        tmp = Path(self._tmp_str)
        bead = _bead(bead_id="el-stuck")
        session = _session(session_id="sdlc-discipline__worker-bl-test")
        # Real production shape from gc session list --json output:
        session_envelope = {
            "filters": {},
            "ok": True,
            "schema_version": "1",
            "sessions": [session],
            "summary": {"total": 1, "active": 1, "suspended": 0, "closed": 0},
        }
        gc = _fake_gc(
            tmp,
            bd_list_json=json.dumps([bead]),
            session_list_json=json.dumps(session_envelope),
        )
        tmux = _fake_tmux(tmp, PANE_AT_PROMPT_IDLE)
        notify = _fake_recorder(tmp, "sdlc-notify.sh")
        events = _events_file(tmp, bead_id="el-stuck", last_update_seconds_ago=1800)

        env = _base_env(tmp, gc, tmux, notify)
        env["SDLC_ALIVE_IDLE_DETECTOR_ENABLED"] = "true"
        env["SDLC_ALIVE_IDLE_EVENTS_PATH"] = str(events)

        result = subprocess.run(
            [str(SCRIPT_PATH)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, f"stderr={result.stderr!r}")
        gc_calls = (tmp / "gc-argv.log").read_text()
        self.assertIn(
            "session submit",
            gc_calls,
            f"object-shape session list should still resolve the assignee; gc_calls=\n{gc_calls}",
        )


if __name__ == "__main__":
    unittest.main()
