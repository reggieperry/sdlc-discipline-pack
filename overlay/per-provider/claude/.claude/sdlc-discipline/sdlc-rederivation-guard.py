#!/usr/bin/env python3
"""sdlc-rederivation-guard.py — pack #197 Part 2.

Called by the tester's bounce-to-worker path. Compares the freshly-computed
gate.blocks (env GATE_BLOCKS) against the prior cycle's gate.blocks already
on the bead. If they are structurally identical and non-empty — the worker
resumed, could not change the gate result, and the gate re-derived the same
failure — this is a confirmed dead-end: park the bead
(requires_human_decision=true, status=blocked, routing cleared, witness
mailed) instead of bouncing it back into the loop. The #197 Part 1 kickoff
guard then refuses to re-arm the parked bead.

Otherwise (blocks changed, first derivation, or an empty/trivial block set)
it performs the normal bounce: record gate.blocks and route to the worker
pool — recording the current blocks so the NEXT cycle has a prior to
compare against.

Usage:
    GATE_BLOCKS='<json>' sdlc-rederivation-guard.py <bead> <worker_target> <witness_target>

Decides structurally (parsed JSON), so cosmetic whitespace / key-order noise
does not read as a changed block set. Never parks on an empty/trivial set
([] / null / ""): that means the gate found nothing to block.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys


def _normalize(raw: str | None):
    """Parse a gate.blocks string to a comparable value, or None for an
    empty / trivial / unset block set (never a dead-end to park on)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw  # unparseable but non-empty — compare as text
    return value or None  # [] / null / {} / 0 → None


def _prior_blocks(bead: str) -> str | None:
    try:
        proc = subprocess.run(
            ["bd", "show", bead, "--json"], capture_output=True, text=True, timeout=30
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout or "[]")
        if not data:
            return None
        return (data[0].get("metadata") or {}).get("gate.blocks")
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError):
        return None


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(
            "usage: GATE_BLOCKS='<json>' sdlc-rederivation-guard.py "
            "<bead> <worker_target> <witness_target>",
            file=sys.stderr,
        )
        return 2

    bead, worker_target, witness_target = argv[1], argv[2], argv[3]
    current_raw = os.environ.get("GATE_BLOCKS", "")
    current = _normalize(current_raw)
    prior = _normalize(_prior_blocks(bead))
    now = dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")

    if current is not None and current == prior:
        # Confirmed-identical re-derivation → park; do NOT bounce.
        subprocess.run(
            [
                "bd",
                "update",
                bead,
                "--set-metadata",
                f"gate.blocks={current_raw}",
                "--set-metadata",
                "requires_human_decision=true",
                "--set-metadata",
                f"rederivation_parked_at={now}",
                "--status=blocked",
                "--set-metadata",
                "gc.routed_to=",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        subject = f"ESCALATION: {bead} parked — identical gate re-derivation [HIGH]"
        message = (
            f"{bead} bounced through the chain and the gate re-derived the SAME blocks two "
            f"cycles running — the worker cannot change the gate result, so this is a dead-end, "
            f"not a transient failure. Parked with requires_human_decision=true; it will not "
            f"re-spawn (the #197 kickoff guard refuses to re-arm it). Resolve via "
            f"`sdlc-human-decision.sh resolve {bead} --action merge|rescope|waive`. "
            f"gate.blocks: {current_raw}"
        )
        subprocess.run(
            ["gc", "mail", "send", witness_target, "-s", subject, "-m", message],
            capture_output=True,
            text=True,
            timeout=30,
        )
        print(
            f"rederivation-guard: {bead} PARKED — identical gate.blocks two cycles running; "
            f"requires_human_decision set, status=blocked, routing cleared, witness mailed."
        )
        return 0

    # Normal bounce: record the current blocks (so the next cycle has a prior
    # to compare) and route to the worker pool.
    subprocess.run(
        [
            "bd",
            "update",
            bead,
            "--set-metadata",
            f"gate.blocks={current_raw}",
            "--status=open",
            "--assignee",
            "",
            "--set-metadata",
            f"gc.routed_to={worker_target}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    reason = "blocks changed" if prior is not None else "first derivation"
    print(f"rederivation-guard: {bead} bounced to worker ({reason}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
