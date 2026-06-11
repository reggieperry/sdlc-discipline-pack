"""Bead-phase stall detector (pack #44 sub-story 4).

Periodically scans `in_progress` chain beads across registered rigs and
emits an operator email when a bead's `current_step` has elapsed past
its phase-specific SLO. Invoked from `sdlc-stall-detector.sh` via the
`orders/sdlc-stall-detector.toml` cron order at a 15-minute cooldown.

The mostly-unattended chain-operation goal lives or dies on this script.
Pack #44 sub-stories 1-3 (helper, human_required alerts, all-closes
alerts) cover the *success-side* signals — the operator hears when a
chain completes or parks for review. This script covers the *silent-
failure* signal: a chain that gets stuck mid-phase with no completion
event to trigger an email, leaving the operator unaware until they poll.

Per-phase SLO defaults (in minutes), tuned to the long-tail observed in
real chains. The implement phase carries the widest cap because it
covers substantive migrations like Elder's EL-070 security audit
(~80 min) and EL-008 typed-event migration (~60 min); the others
reflect the empirical distribution under healthy operation.

Throttle: dedup by `(bead_id, phase)`. The first alert for a given
stall is loud; subsequent alerts for the same stall stay silent until
either the bead's `current_step` changes (the stall resolved) or four
hours have passed (the stall is still going and deserves a fresh
notification). The throttle state lives on the bead itself as
`stall_alert.<phase>.last_at`, so a respawned cron tick reads the same
state and a manually-cleared bead naturally re-enters the alert window.

Graceful degradation:

- `bd list` fails → log to stderr, exit 0. The next tick will retry.
- Bead missing the `<phase>.started_at` metadata → skip the bead. This
  happens for beads in transition between phases; the next tick will
  see the new started_at once the receiving pool agent writes it.
- `sdlc-notify.sh` not on PATH → the helper itself logs the absence
  and exits 0; the detector also exits 0 with a stderr note.

CLI:

    python3 sdlc-stall-detector.py
        [--now <iso8601>]          # injectable clock for tests
        [--throttle-hours <int>]   # default 4
        [--notify-bin <path>]      # override sdlc-notify.sh path

Reads `bd list --status in_progress --json` from cwd. The bash wrapper
sets cwd per rig and invokes once per rig.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_DEFAULT_SLOS_MINUTES = {
    # Planner-session steps (mol-sdlc-plan, pack #226).
    "load-context": 5,
    "workspace-setup": 5,
    "plan": 30,
    "submit-plan": 10,
    # Worker-session steps (mol-sdlc-work).
    "workspace-resume": 5,
    "capture-baseline": 15,
    "implement": 120,
    "self-audit": 10,
    "submit-and-exit": 10,
    # Downstream single-conversation pools.
    "tester": 15,
    "reviewer": 20,
    "documenter": 20,
    "finalizer": 15,
}

_DEFAULT_THROTTLE_HOURS = 4


@dataclass(frozen=True)
class StallAlert:
    """One stalled bead. Carries the data the notifier needs for the email."""

    bead_id: str
    phase: str
    elapsed_minutes: int
    slo_minutes: int
    started_at_iso: str
    rig: str


def slos_with_overrides(env: dict[str, str]) -> dict[str, int]:
    """Apply env overrides to the default SLO table.

    Override env var: `SDLC_STALL_SLO_OVERRIDE` carries a comma-separated
    list of `phase=minutes` pairs (e.g., `implement=180,tester=20`).
    Malformed entries are skipped with a stderr log; the default for
    that phase stays in effect.
    """
    slos = dict(_DEFAULT_SLOS_MINUTES)
    raw = env.get("SDLC_STALL_SLO_OVERRIDE", "").strip()
    if not raw:
        return slos
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            print(f"sdlc-stall-detector: bad SLO override (no `=`): {item!r}", file=sys.stderr)
            continue
        phase, minutes_str = item.split("=", 1)
        phase = phase.strip()
        try:
            slos[phase] = int(minutes_str.strip())
        except ValueError:
            print(
                f"sdlc-stall-detector: bad SLO override (not int): {item!r}",
                file=sys.stderr,
            )
    return slos


def parse_iso(ts: str) -> datetime | None:
    """Parse an ISO 8601 timestamp into a tz-aware datetime.

    Returns None on parse failure so the caller skips the bead rather
    than crashing the whole tick.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def find_stalls(
    beads: list[dict[str, Any]],
    slos: dict[str, int],
    now: datetime,
    throttle: timedelta,
    rig: str,
) -> list[StallAlert]:
    """Compute the list of beads whose current_step has elapsed past SLO and aren't throttled.

    Pure function — no I/O, no `bd` calls. Test surface for the detector.
    """
    alerts: list[StallAlert] = []
    for bead in beads:
        bead_id = bead.get("id", "")
        meta = bead.get("metadata") or {}
        phase = meta.get("current_step", "")
        if not phase or phase not in slos:
            continue
        started_at = parse_iso(meta.get(f"{phase}.started_at", ""))
        if started_at is None:
            continue
        elapsed = now - started_at
        slo_minutes = slos[phase]
        if elapsed < timedelta(minutes=slo_minutes):
            continue
        last_alerted = parse_iso(meta.get(f"stall_alert.{phase}.last_at", ""))
        if last_alerted and (now - last_alerted) < throttle:
            continue
        alerts.append(
            StallAlert(
                bead_id=bead_id,
                phase=phase,
                elapsed_minutes=int(elapsed.total_seconds() / 60),
                slo_minutes=slo_minutes,
                started_at_iso=started_at.isoformat(),
                rig=rig,
            )
        )
    return alerts


def render_email_body(alert: StallAlert) -> tuple[str, str]:
    """Build (subject, body) for `sdlc-notify.sh`.

    Subject uses the `[stall-warning]` prefix per the issue's design note:
    stalls are uncertain (might be a slow chain, might be a real stuck
    process), so the tone should signal investigation, not alarm.
    """
    subject = (
        f"[{alert.rig}] [stall-warning] bead {alert.bead_id} stuck in "
        f"`{alert.phase}` for {alert.elapsed_minutes} min (SLO {alert.slo_minutes} min)"
    )
    body = (
        f"Chain bead `{alert.bead_id}` has been in the `{alert.phase}` phase for "
        f"{alert.elapsed_minutes} minutes — past the {alert.slo_minutes}-minute "
        f"SLO for that phase.\n\n"
        f"Started at: {alert.started_at_iso}\n"
        f"Rig: {alert.rig}\n\n"
        f"Investigate with:\n\n"
        f"    bd show {alert.bead_id} --json | jq '.[0].metadata'\n\n"
        f"This alert is throttled per `(bead_id, phase)`. Re-alert fires "
        f"when the phase changes or four hours pass."
    )
    return subject, body


def _project_key(rig_root: Path) -> str:
    """Compute the Claude Code project-key directory name for a rig path.

    Claude Code stores per-project session JSONLs at
    `~/.claude/projects/<project-key>/`. The key is the absolute rig path
    with `/`, `.`, and `_` normalized to `-`, preceded by a leading `-`.
    Matches `snapshot_operator_memory.py`'s normalization (per v2.13.1).
    """
    normalized = str(rig_root.resolve())
    for ch in ("/", ".", "_"):
        normalized = normalized.replace(ch, "-")
    return normalized if normalized.startswith("-") else "-" + normalized


def _session_mentions_bead(path: Path, bead_id: str) -> bool:
    """Return True if any line of `path` contains the bead id (line-streamed)."""
    try:
        with path.open("r", errors="replace") as fh:
            for line in fh:
                if bead_id in line:
                    return True
    except OSError:
        return False
    return False


def find_session_jsonl(
    bead_id: str,
    rig_root: Path,
    *,
    home: Path | None = None,
    max_files: int = 5,
) -> Path | None:
    """Find the most recently-modified Claude Code session JSONL that names a bead.

    Looks under `~/.claude/projects/<project-key>/` (computed from `rig_root`)
    for `*.jsonl`, sorted by mtime descending. Returns the first one that
    mentions `bead_id` in its content. Caps the scan at `max_files` to keep
    the per-bead cost bounded — chains write a session per phase, so the
    most-recent five almost always covers the relevant worker.

    Returns None if the project directory doesn't exist, no JSONLs match, or
    none of the candidate files mention the bead.
    """
    home_dir = home or Path.home()
    project_dir = home_dir / ".claude" / "projects" / _project_key(rig_root)
    if not project_dir.is_dir():
        return None
    try:
        files = sorted(
            project_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:max_files]
    except OSError:
        return None
    for f in files:
        if _session_mentions_bead(f, bead_id):
            return f
    return None


def classify_session_mode(
    classify_bin: str,
    session_path: Path,
) -> tuple[str, str] | None:
    """Run sdlc-mode-classify.sh against `session_path`.

    Returns `(verdict, reason)` where verdict is one of `mode_a` /
    `mode_b` / `uncertain` and reason is the explainer line the classifier
    writes to stderr. Returns None on exec failure (missing binary,
    non-zero exit) — caller falls back to "unavailable" annotation.
    """
    try:
        proc = subprocess.run(
            [classify_bin, "--session", str(session_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    verdict_lines = (proc.stdout or "").strip().splitlines()
    if not verdict_lines:
        return None
    verdict = verdict_lines[0].strip()
    reason = ""
    for line in (proc.stderr or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("reason:"):
            reason = stripped[len("reason:") :].strip()
            break
    return (verdict, reason)


def _recovery_hint(verdict: str) -> str:
    """One-line recovery guidance keyed by mode verdict."""
    if verdict == "mode_a":
        return (
            "Mode A (API overload / 529 storm). Retry the session — the "
            "wrapper may have given up. See pack #47 retry-wrapper notes."
        )
    if verdict == "mode_b":
        return (
            "Mode B (per-turn-cap exhausted). Commit worker WIP, clear "
            "assignee, kill session, `gc supervisor reload`. See "
            "gascity#2293 (reactive recovery in drain-ack handler)."
        )
    return "Uncertain — inspect the session JSONL manually."


def augment_body_with_mode(
    body: str,
    *,
    session_path: Path | None,
    mode_info: tuple[str, str] | None,
) -> str:
    """Append mode-classification details to the email body.

    Three states:
    - Session located and classified → verdict, reason, recovery hint.
    - Session located but classifier failed → note the path so operator
      can run the classifier manually.
    - Session not located → note that auto-classification is unavailable.
    """
    if mode_info is not None:
        verdict, reason = mode_info
        return (
            body
            + "\n\n"
            + f"Mode classification: **{verdict}**\n"
            + f"  reason: {reason}\n"
            + f"  recovery: {_recovery_hint(verdict)}\n"
            + f"  session: `{session_path}`\n"
        )
    if session_path is not None:
        return (
            body
            + "\n\n"
            + "Mode classification: classifier failed; run manually:\n"
            + f"  sdlc-mode-classify.sh --session {session_path}\n"
        )
    return (
        body
        + "\n\n"
        + "Mode classification: unavailable (no recent session JSONL "
        + "mentions this bead under ~/.claude/projects/<project-key>/)\n"
    )


def invoke_notify(notify_bin: str, subject: str, body: str) -> int:
    """Send the alert via sdlc-notify.sh, returning the helper's exit code.

    On exec failure (helper missing), returns 127 and logs to stderr.
    Errors are non-fatal: the detector continues with the next alert.
    """
    try:
        proc = subprocess.run(
            [notify_bin, "--subject", subject],
            input=body,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print(
            f"sdlc-stall-detector: sdlc-notify.sh not found at {notify_bin}; alert dropped",
            file=sys.stderr,
        )
        return 127
    if proc.returncode != 0:
        print(
            f"sdlc-stall-detector: sdlc-notify.sh exited {proc.returncode}: {proc.stderr.strip()}",
            file=sys.stderr,
        )
    return proc.returncode


def mark_alerted(bead_id: str, phase: str, now: datetime) -> int:
    """Record the throttle timestamp on the bead via `bd update --set-metadata`.

    Returns bd's exit code. Errors are non-fatal: the detector logs and
    continues. The worst case is a re-alert on the next tick, which is
    still rate-limited by the cooldown interval.
    """
    proc = subprocess.run(
        [
            "bd",
            "update",
            bead_id,
            "--set-metadata",
            f"stall_alert.{phase}.last_at={now.isoformat()}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(
            f"sdlc-stall-detector: bd update {bead_id} failed: {proc.stderr.strip()}",
            file=sys.stderr,
        )
    return proc.returncode


def fetch_beads() -> list[dict[str, Any]]:
    """Read `in_progress` beads from the current rig via `bd list`.

    Returns an empty list on bd failure rather than raising — the next
    cron tick will retry. The bash wrapper handles cd-into-rig before
    invoking this script.
    """
    proc = subprocess.run(
        ["bd", "list", "--status", "in_progress", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(
            f"sdlc-stall-detector: bd list failed: {proc.stderr.strip()}",
            file=sys.stderr,
        )
        return []
    try:
        return json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        print(f"sdlc-stall-detector: bd output not JSON: {exc}", file=sys.stderr)
        return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect bead-phase stalls and emit operator email alerts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--now",
        type=str,
        default=None,
        help="ISO 8601 timestamp to use as `now` (default: actual current time)",
    )
    parser.add_argument(
        "--throttle-hours",
        type=int,
        default=_DEFAULT_THROTTLE_HOURS,
        help=f"hours to throttle re-alerts on the same (bead, phase) pair (default: {_DEFAULT_THROTTLE_HOURS})",
    )
    parser.add_argument(
        "--notify-bin",
        type=str,
        default=os.environ.get(
            "SDLC_NOTIFY_BIN",
            "sdlc-notify.sh",
        ),
        help="path to the sdlc-notify.sh helper (default: $SDLC_NOTIFY_BIN or PATH lookup)",
    )
    parser.add_argument(
        "--rig",
        type=str,
        default=os.environ.get("GC_RIG", "unknown"),
        help="rig name for the subject line (default: $GC_RIG or 'unknown')",
    )
    default_classify = str(Path(__file__).resolve().parent / "sdlc-mode-classify.sh")
    parser.add_argument(
        "--classify-bin",
        type=str,
        default=os.environ.get("SDLC_MODE_CLASSIFY_BIN", default_classify),
        help="path to sdlc-mode-classify.sh (default: sibling script or $SDLC_MODE_CLASSIFY_BIN)",
    )
    parser.add_argument(
        "--rig-root",
        type=str,
        default=os.environ.get("GC_RIG_ROOT", os.getcwd()),
        help="rig root path for project-key lookup (default: $GC_RIG_ROOT or cwd)",
    )
    args = parser.parse_args(argv)

    now = parse_iso(args.now) if args.now else datetime.now(UTC)
    if now is None:
        print(f"sdlc-stall-detector: bad --now {args.now!r}", file=sys.stderr)
        return 2

    slos = slos_with_overrides(dict(os.environ))
    throttle = timedelta(hours=args.throttle_hours)
    beads = fetch_beads()
    alerts = find_stalls(beads, slos, now, throttle, args.rig)

    if not alerts:
        return 0

    rig_root = Path(args.rig_root)
    for alert in alerts:
        subject, body = render_email_body(alert)
        session_path = find_session_jsonl(alert.bead_id, rig_root)
        mode_info = (
            classify_session_mode(args.classify_bin, session_path)
            if session_path is not None
            else None
        )
        augmented = augment_body_with_mode(body, session_path=session_path, mode_info=mode_info)
        if invoke_notify(args.notify_bin, subject, augmented) == 0:
            mark_alerted(alert.bead_id, alert.phase, now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
