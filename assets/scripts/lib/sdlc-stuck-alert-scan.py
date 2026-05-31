#!/usr/bin/env python3
"""Detection scan for sdlc-stuck-alert (pack #212).

Emits one JSON action line per freshly stranded-awaiting-human bead, read
from `bd list --all --json` in the current rig — or, under
``STUCK_ALERT_SELF_TEST=1``, from a synthetic two-bead set (one per
trigger). The synthetic path is what makes the ``--self-test`` canary
exercise the *real* detection logic rather than a send-only check: if a
schema change or a logic regression makes the detector stop matching, the
canary's synthetic beads stop being flagged and the self-test fails loud.

Two triggers (both stable terminal states a phase sets and exits on):
  - blocked-for-decision: status=blocked + a non-empty human_decision_reason,
    not yet stamped with blocked_alerted_at.
  - bounce-exhausted PR: status=escalated + refresh_status=conflict
    (finalizer at-cap branch), not yet stamped with stuck_alerted_at.

stdlib-only. Run from within a rig root (the bd call is rig-relative).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

SELF_TEST_BEADS = [
    {
        "id": "selftest-blocked",
        "status": "blocked",
        "metadata": {"human_decision_reason": "self-test synthetic decision"},
    },
    {
        "id": "selftest-bounce",
        "status": "escalated",
        "metadata": {"refresh_status": "conflict", "merge_failure_files": "self/test.py"},
    },
]


def _nonempty(meta: dict, key: str) -> str:
    return (meta.get(key) or "").strip()


def load_beads() -> list[dict]:
    if os.environ.get("STUCK_ALERT_SELF_TEST") == "1":
        return SELF_TEST_BEADS
    try:
        proc = subprocess.run(
            ["bd", "list", "--all", "--limit", "5000", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return []
        return json.loads(proc.stdout or "[]")
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return []


def actions_for(bead: dict) -> dict | None:
    meta = bead.get("metadata") or {}
    status = bead.get("status", "")
    bead_id = bead.get("id", "")

    # Trigger 2: blocked-for-decision.
    if status == "blocked" and _nonempty(meta, "human_decision_reason"):
        if _nonempty(meta, "blocked_alerted_at"):
            return None
        return {
            "bead_id": bead_id,
            "trigger": "blocked",
            "stamp": "blocked_alerted_at",
            "detail": meta.get("human_decision_reason", ""),
            "story": meta.get("story_file", ""),
        }

    # Trigger 1: bounce-exhausted PR (finalizer at-cap branch).
    if status == "escalated" and meta.get("refresh_status") == "conflict":
        if _nonempty(meta, "stuck_alerted_at"):
            return None
        return {
            "bead_id": bead_id,
            "trigger": "bounce",
            "stamp": "stuck_alerted_at",
            "detail": "collision files: " + (meta.get("merge_failure_files") or "unknown"),
            "story": meta.get("story_file", ""),
        }

    return None


def main() -> int:
    for bead in load_beads():
        action = actions_for(bead)
        if action is not None:
            print(json.dumps(action))
    return 0


if __name__ == "__main__":
    sys.exit(main())
