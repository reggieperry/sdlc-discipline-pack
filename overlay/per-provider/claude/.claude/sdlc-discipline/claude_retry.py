#!/usr/bin/env python3
"""Claude Code retry helper for SDLC chain pool workers (pack #47).

Decides whether to exit, retry, or escalate after a `claude` session ends in
a pool worker context. Wraps two complementary checks:

1. **State-based handoff check** — did the bead's `current_step` advance past
   this pool template's terminal step? If yes, the worker completed its
   handoff and the wrapper exits cleanly. The state is the authoritative
   signal — claude's own exit code doesn't tell us whether the work landed.

2. **Log-based cause classification** — when the handoff did NOT advance,
   what made claude exit? Used for the retry policy (sleep delay) and for
   the metadata trail (alerts, future debugging). Reads the session JSONL
   tail. Five outcomes: `turn_cap`, `api_529`, `api_429`, `crash`, `unknown`.

Subcommands
-----------
classify-exit    Print the cause string from a session log + return code.
                 Used by the bash wrapper to choose a retry delay and to
                 record `<template>.last_exit_cause` metadata.

retry-delay      Print the sleep seconds for a (cause, attempt) pair.
                 Per-cause policy: turn_cap=5, api_429=retry-after-hint,
                 api_529=exponential, crash=60, unknown=60.

build-prompt     Print the OQ1-validated continuation prompt sent to
                 `claude --resume` on retry.

handoff-complete Exit 0 if the bead's current_step advanced past this
                 template's terminal step; exit 1 otherwise. Reads bd
                 metadata.

decide           One-shot orchestrator combining all four. Reads bead,
                 session log, return code, attempt counter; prints one of
                 `EXIT_SUCCESS`, `EXIT_EXHAUSTED <cause>`, or
                 `RETRY <delay> <cause>`. Lets the bash wrapper stay thin.

Per-template phase progression
------------------------------
Each pool template walks an ordered list of steps recorded in
`bead.metadata.current_step`. The wrapper considers the work handed off
when `current_step` is NOT in the running template's step list — that is,
it advanced past the template's authority. The lists below mirror the
chain's six formulas and must stay in lockstep with the formula step
definitions.

Open questions
--------------
OQ3 from the pack #47 grounding pass is still unverified: the exact event
shape Anthropic writes when the API returns 529 was not captured before
EL-070's session log was overwritten. The classifier falls back to
heuristic pattern matching on the JSONL text; the first real 529 in chain
operation will let us refine the parser.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Cause(str, Enum):
    """Classification of why a `claude` session ended without a handoff.

    Inherits from `str` so JSON serialization is trivial and equality
    with bare strings works in test assertions.
    """

    TURN_CAP = "turn_cap"
    API_529 = "api_529"
    API_429 = "api_429"
    CRASH = "crash"
    UNKNOWN = "unknown"


class Action(str, Enum):
    """Decision categories emitted to the bash wrapper."""

    EXIT_SUCCESS = "EXIT_SUCCESS"
    EXIT_EXHAUSTED = "EXIT_EXHAUSTED"
    RETRY = "RETRY"


# Per-template phase progression. Each list is the ORDERED set of steps
# recorded in `bead.metadata.current_step` while this template owns the
# bead. When `current_step` leaves this list (advances to the next
# template's first step) the handoff is complete.
PHASE_ORDER: dict[str, list[str]] = {
    "worker": [
        "load-context",
        "plan",
        "workspace-setup",
        "implement",
        "self-audit",
        "submit-and-exit",
    ],
    "tester": [
        "tester-setup",
        "capture-baseline-tester",
        "run-tests",
        "audit-residue",
        "submit-and-exit",
    ],
    "reviewer": [
        "read-diff",
        "review",
        "emit-recommendation",
        "submit-and-exit",
    ],
    "documenter": [
        "read-changes",
        "draft-docs",
        "submit-and-exit",
    ],
    "finalizer": [
        "fetch-baseline",
        "rebase",
        "merge",
        "submit-and-exit",
    ],
}


MAX_ATTEMPTS_DEFAULT = 5

# Per-cause retry delay schedules (seconds).
# `api_529` uses exponential backoff per the design (30, 60, 120, 300, 600).
# `turn_cap` retries quickly — the agent's loop just hit its turn budget.
# `crash` waits longer — process exited abnormally; give the system room.
# `unknown` is the same as crash — conservative default.
# `api_429` is computed from a hint when available; falls back to this list.
RETRY_SCHEDULE: dict[Cause, list[int]] = {
    Cause.TURN_CAP: [5, 5, 5, 5, 5],
    Cause.API_529: [30, 60, 120, 300, 600],
    Cause.API_429: [60, 60, 60, 60, 60],
    Cause.CRASH: [60, 60, 60, 60, 60],
    Cause.UNKNOWN: [60, 60, 60, 60, 60],
}


CONTINUATION_PROMPT = (
    "You were interrupted. Check git status and your task list. "
    "Continue your plan from where you stopped. Re-run the last "
    "tool call IF you can determine it was incomplete."
)


@dataclass(frozen=True)
class Decision:
    """The full output of `decide()`. The bash wrapper acts on `action`."""

    action: Action
    cause: Cause | None = None
    delay_seconds: int | None = None


def expected_terminal_step(template: str) -> str:
    """The step that marks this template's own end — the last step in its phase list.

    Raises `ValueError` for unknown templates so the wrapper fails loudly
    on a misconfigured city.toml rather than silently mis-routing.
    """
    steps = PHASE_ORDER.get(template)
    if not steps:
        raise ValueError(f"unknown template: {template!r}")
    return steps[-1]


def handoff_complete(current_step: str, template: str) -> bool:
    """True iff the bead has advanced past this template's authority.

    Heuristic: the handoff has happened when `current_step` is not in this
    template's phase list. A step inside the list means the template is
    still mid-work. Empty `current_step` (never set) means setup not done
    yet — also not a handoff.

    Raises `ValueError` for unknown templates.
    """
    steps = PHASE_ORDER.get(template)
    if not steps:
        raise ValueError(f"unknown template: {template!r}")
    if not current_step:
        return False
    return current_step not in steps


def classify_exit(session_log_path: str | Path, return_code: int) -> Cause:
    """Classify why a `claude` session ended, given its session JSONL.

    Reads the tail of the session log (last ~50 events) and looks for the
    cause markers grounded in the OQ1 investigation:

    - `system` event with `subtype: turn_duration` and `preventedContinuation: false`
      → `TURN_CAP` (the per-turn duration cap, Mode B from the design)
    - JSON or text marker matching 529/Overloaded → `API_529` (Mode A;
      heuristic pending OQ3)
    - JSON or text marker matching 429/rate_limit → `API_429`
    - Otherwise, non-zero `return_code` → `CRASH`
    - Otherwise → `UNKNOWN` (zero return code but no clean handoff —
      something exited the loop without a recognized signal)

    The classifier is order-sensitive: `TURN_CAP` is checked first because
    it's the most frequent observed cause (Mode B in EL-073/EL-076/EL-013).
    529 and 429 are pattern-based until OQ3 produces a verified schema.
    """
    path = Path(session_log_path)
    if not path.exists():
        return Cause.CRASH if return_code != 0 else Cause.UNKNOWN

    tail_lines = _read_tail_lines(path, count=50)

    for line in reversed(tail_lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "system" and event.get("subtype") == "turn_duration":
            return Cause.TURN_CAP

    joined = "\n".join(tail_lines).lower()
    if "529" in joined or "overloaded" in joined:
        return Cause.API_529
    if "429" in joined or "rate_limit" in joined or "rate limit" in joined:
        return Cause.API_429

    if return_code != 0:
        return Cause.CRASH
    return Cause.UNKNOWN


def retry_delay(cause: Cause, attempt: int) -> int:
    """Sleep seconds for the given (cause, attempt) pair.

    `attempt` is 1-indexed (first retry is attempt=1). Attempts beyond the
    schedule length reuse the schedule's last entry — a sensible fallback
    that bounds the longest sleep instead of indexing out of range.
    """
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt}")
    schedule = RETRY_SCHEDULE.get(cause, RETRY_SCHEDULE[Cause.UNKNOWN])
    idx = min(attempt - 1, len(schedule) - 1)
    return schedule[idx]


def build_continuation_prompt() -> str:
    """The OQ1-validated text sent on `claude --resume` after a turn-cap exit.

    Validated 2026-05-16: this exact prompt cleanly resumes a SIGKILL-mid-tool
    session. The 'IF you can determine it was incomplete' clause nudges the
    agent to verify before re-issuing a possibly-completed write — important
    for `git commit` idempotency.
    """
    return CONTINUATION_PROMPT


# ---------------------------------------------------------------------------
# bd integration — used by `handoff-complete` and `decide` subcommands.
# Injectable runner for unit-tested call sites.
# ---------------------------------------------------------------------------


BdRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _default_bd_runner(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Default `bd` subprocess runner. Replaced by a fake in tests."""
    return subprocess.run(args, capture_output=True, text=True, check=False)


def read_current_step(bead_id: str, run_bd: BdRunner | None = None) -> str:
    """Read `bead.metadata.current_step` via `bd show <bead-id> --json`.

    Returns the empty string when the field is unset, the bead is missing,
    or the JSON shape is unexpected. The bash wrapper treats empty step as
    "not yet handed off" so the conservative default falls through to a
    retry.
    """
    runner = run_bd or _default_bd_runner
    result = runner(["bd", "show", bead_id, "--json"])
    if result.returncode != 0:
        return ""
    try:
        data = json.loads(result.stdout)
        return data[0].get("metadata", {}).get("current_step", "") or ""
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        return ""


# ---------------------------------------------------------------------------
# Top-level decision orchestrator.
# ---------------------------------------------------------------------------


def decide(
    bead_id: str,
    template: str,
    session_log_path: str | Path,
    return_code: int,
    attempt: int,
    max_attempts: int = MAX_ATTEMPTS_DEFAULT,
    run_bd: BdRunner | None = None,
) -> Decision:
    """Combine the handoff check, cause classification, and attempt cap.

    Order matters: handoff is checked FIRST because if the work landed,
    the cause classifier may misfire on a clean log that nevertheless
    looks "incomplete" by some heuristic. Once we know there's no handoff,
    we classify; then we check the attempt cap; then we compute the delay.
    """
    current = read_current_step(bead_id, run_bd=run_bd)
    if handoff_complete(current, template):
        return Decision(action=Action.EXIT_SUCCESS)

    cause = classify_exit(session_log_path, return_code)

    if attempt >= max_attempts:
        return Decision(action=Action.EXIT_EXHAUSTED, cause=cause)

    delay = retry_delay(cause, attempt)
    return Decision(action=Action.RETRY, cause=cause, delay_seconds=delay)


# ---------------------------------------------------------------------------
# Helpers — file tail, CLI dispatch.
# ---------------------------------------------------------------------------


def _read_tail_lines(path: Path, count: int) -> list[str]:
    """Read up to `count` final non-empty lines from a text file.

    Reads the full file; session logs are small enough (one JSONL line per
    event, a few hundred per session) that streaming optimization isn't
    worth the complexity. Strips trailing newlines.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            lines = [line.rstrip("\n") for line in f if line.strip()]
    except OSError:
        return []
    return lines[-count:]


def _cli_classify_exit(args: argparse.Namespace) -> int:
    cause = classify_exit(args.session_log, args.return_code)
    print(cause.value)
    return 0


def _cli_retry_delay(args: argparse.Namespace) -> int:
    try:
        cause = Cause(args.cause)
    except ValueError:
        print(f"unknown cause: {args.cause!r}", file=sys.stderr)
        return 2
    print(retry_delay(cause, args.attempt))
    return 0


def _cli_build_prompt(_args: argparse.Namespace) -> int:
    print(build_continuation_prompt())
    return 0


def _cli_handoff_complete(args: argparse.Namespace) -> int:
    step = read_current_step(args.bead)
    return 0 if handoff_complete(step, args.template) else 1


def _cli_decide(args: argparse.Namespace) -> int:
    decision = decide(
        bead_id=args.bead,
        template=args.template,
        session_log_path=args.session_log,
        return_code=args.return_code,
        attempt=args.attempt,
        max_attempts=args.max_attempts,
    )
    if decision.action is Action.EXIT_SUCCESS:
        print(Action.EXIT_SUCCESS.value)
    elif decision.action is Action.EXIT_EXHAUSTED:
        cause_str = decision.cause.value if decision.cause else Cause.UNKNOWN.value
        print(f"{Action.EXIT_EXHAUSTED.value} {cause_str}")
    else:
        cause_str = decision.cause.value if decision.cause else Cause.UNKNOWN.value
        print(f"{Action.RETRY.value} {decision.delay_seconds} {cause_str}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude_retry",
        description="Pack #47 claude-retry decision helper. See module docstring.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_classify = sub.add_parser("classify-exit", help="Classify why claude exited.")
    p_classify.add_argument("--session-log", required=True, type=Path)
    p_classify.add_argument("--return-code", required=True, type=int)
    p_classify.set_defaults(func=_cli_classify_exit)

    p_delay = sub.add_parser("retry-delay", help="Print retry delay (seconds).")
    p_delay.add_argument("--cause", required=True)
    p_delay.add_argument("--attempt", required=True, type=int)
    p_delay.set_defaults(func=_cli_retry_delay)

    p_prompt = sub.add_parser("build-prompt", help="Print continuation prompt.")
    p_prompt.set_defaults(func=_cli_build_prompt)

    p_handoff = sub.add_parser(
        "handoff-complete",
        help="Exit 0 if bead has advanced past this template's terminal step.",
    )
    p_handoff.add_argument("--bead", required=True)
    p_handoff.add_argument("--template", required=True)
    p_handoff.set_defaults(func=_cli_handoff_complete)

    p_decide = sub.add_parser("decide", help="One-shot retry decision.")
    p_decide.add_argument("--bead", required=True)
    p_decide.add_argument("--template", required=True)
    p_decide.add_argument("--session-log", required=True, type=Path)
    p_decide.add_argument("--return-code", required=True, type=int)
    p_decide.add_argument("--attempt", required=True, type=int)
    p_decide.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS_DEFAULT)
    p_decide.set_defaults(func=_cli_decide)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # args.func is set by each subparser's set_defaults; argparse's type system
    # doesn't track those defaults so the call result reads as Any without an
    # explicit cast. The handlers are all `_cli_*` functions returning int.
    rc: int = args.func(args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
