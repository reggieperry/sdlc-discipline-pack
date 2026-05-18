#!/usr/bin/env python3
"""sdlc-cost-by-step.py — decompose a worker session's cost by formula step.

The pack's mol-sdlc-work formula walks six steps in one worker session:
load-context, plan, workspace-setup, implement, self-audit, submit-and-exit.
Each transition is marked by a `bd update --set-metadata current_step=X`
call in the worker's session JSONL. This helper bins token usage by step
window so cost can be attributed to the specific step that incurred it.

Used by VAL-005 to answer "what fraction of worker cost is planning vs.
implementation?" — the question that distinguishes "Sonnet worker fails
because planning is hard" from "Sonnet worker fails because implementation
is hard."

Usage
-----

::

    sdlc-cost-by-step.py --jsonl /path/to/session.jsonl

Or, given a worktree dir, find the most recent JSONL in it:

::

    sdlc-cost-by-step.py --worktree /path/to/agent/worktree

Output: tab-separated, sorted by step start time.

::

    step               start                          tokens_in    tokens_out    cache_read   cache_write   cost_usd
    pre-plan           2026-05-14T20:36:11.375Z       1234         5678          12345        678           0.4321
    plan               2026-05-14T20:37:09.261Z       ...
    ...
    total              -                              ...

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# Same pricing table as sdlc-cost-helper.py. Keep in sync.
PRICING_PER_MTOK_USD = {
    "claude-opus-4-7": {
        "input": 15.00,
        "output": 75.00,
        "cache_write_5m": 18.75,
        "cache_read": 1.50,
    },
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_write_5m": 3.75,
        "cache_read": 0.30,
    },
    "claude-haiku-4-5-20251001": {
        "input": 0.80,
        "output": 4.00,
        "cache_write_5m": 1.00,
        "cache_read": 0.08,
    },
}

STEP_MARKER_RE = re.compile(r"current_step=[\"']?([\w\-]+)[\"']?")


def parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def find_jsonl_in_worktree(worktree: Path) -> Path | None:
    """Locate the most recent session JSONL recorded by Claude Code for ``worktree``.

    Claude Code stores transcripts at ``~/.claude/projects/<encoded-worktree-path>/<uuid>.jsonl``
    where the encoded path replaces ``/`` with ``-``. Returns the path with the
    largest mtime, or None if no JSONL exists for that worktree.
    """
    encoded = str(worktree).replace("/", "-")
    base = Path.home() / ".claude" / "projects" / encoded
    if not base.is_dir():
        return None
    candidates = list(base.glob("*.jsonl"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def extract_step_transitions(jsonl: Path) -> list[tuple[datetime, str]]:
    """Return [(ts, step_name), ...] for each step transition recorded in the JSONL.

    A transition is any Bash tool_use whose command contains a
    ``current_step=<name>`` marker (with or without quotes).
    """
    out: list[tuple[datetime, str]] = []
    with jsonl.open() as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = rec.get("message", {})
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    block.get("type") == "tool_use"
                    and block.get("name") == "Bash"
                    and "current_step" in block.get("input", {}).get("command", "")
                ):
                    cmd = block["input"]["command"]
                    m = STEP_MARKER_RE.search(cmd)
                    if not m:
                        continue
                    ts = parse_iso(rec.get("timestamp", ""))
                    if ts is None:
                        continue
                    out.append((ts, m.group(1)))
    return out


def extract_usage_records(jsonl: Path) -> list[tuple[datetime, str, dict[str, int]]]:
    """Return [(ts, model, usage_dict), ...] for each assistant message with token usage."""
    out: list[tuple[datetime, str, dict[str, int]]] = []
    with jsonl.open() as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            model = msg.get("model")
            if not isinstance(usage, dict) or not model:
                continue
            ts = parse_iso(rec.get("timestamp", ""))
            if ts is None:
                continue
            out.append((ts, model, usage))
    return out


def bin_usage_by_step(
    transitions: list[tuple[datetime, str]],
    usage: list[tuple[datetime, str, dict[str, int]]],
    session_start: datetime | None,
) -> list[dict[str, object]]:
    """Bin usage records into step windows.

    Each transition's timestamp is treated as the START of that step. Usage
    records before the first transition belong to a synthetic "pre-<first-step>"
    bin (typically load-context). The final step extends to the last usage
    record.
    """
    if not transitions:
        # No step markers; bucket everything under "unknown".
        bucket: dict[str, dict[str, int]] = {}
        for _, model, u in usage:
            b = bucket.setdefault(
                model,
                {"input": 0, "output": 0, "cache_write_5m": 0, "cache_read": 0},
            )
            _accumulate(b, u)
        return [{"step": "unknown", "start": "-", "by_model": bucket}]

    # Build [(start, end, step_name), ...] windows.
    windows: list[tuple[datetime, datetime, str]] = []
    if session_start and session_start < transitions[0][0]:
        windows.append((session_start, transitions[0][0], f"pre-{transitions[0][1]}"))
    for i, (ts, step) in enumerate(transitions):
        end = transitions[i + 1][0] if i + 1 < len(transitions) else None
        windows.append((ts, end, step))

    rows: list[dict[str, object]] = []
    for start, end, step in windows:
        by_model: dict[str, dict[str, int]] = {}
        for ts, model, u in usage:
            if ts < start:
                continue
            if end is not None and ts >= end:
                continue
            b = by_model.setdefault(
                model,
                {"input": 0, "output": 0, "cache_write_5m": 0, "cache_read": 0},
            )
            _accumulate(b, u)
        rows.append({"step": step, "start": start.isoformat(), "by_model": by_model})
    return rows


def _accumulate(bucket: dict[str, int], usage: dict[str, int]) -> None:
    bucket["input"] += int(usage.get("input_tokens", 0) or 0)
    bucket["output"] += int(usage.get("output_tokens", 0) or 0)
    bucket["cache_write_5m"] += int(usage.get("cache_creation_input_tokens", 0) or 0)
    bucket["cache_read"] += int(usage.get("cache_read_input_tokens", 0) or 0)


def cost_for_bucket(model: str, bucket: dict[str, int]) -> float:
    price = PRICING_PER_MTOK_USD.get(model)
    if price is None:
        return 0.0
    return sum((v / 1_000_000) * price.get(k, 0.0) for k, v in bucket.items())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--jsonl", type=Path, help="path to a Claude Code session JSONL")
    src.add_argument(
        "--worktree",
        type=Path,
        help="worktree directory; the most recent JSONL recorded for it is used",
    )
    p.add_argument("--json", action="store_true", help="emit JSON instead of tab-separated text")
    args = p.parse_args(argv)

    if args.jsonl:
        jsonl = args.jsonl
    else:
        jsonl = find_jsonl_in_worktree(args.worktree)
        if jsonl is None:
            print(f"no JSONL found for worktree {args.worktree}", file=sys.stderr)
            return 1

    if not jsonl.is_file():
        print(f"no such file: {jsonl}", file=sys.stderr)
        return 1

    transitions = extract_step_transitions(jsonl)
    usage = extract_usage_records(jsonl)
    session_start = usage[0][0] if usage else None
    rows = bin_usage_by_step(transitions, usage, session_start)

    if args.json:
        out: list[dict[str, object]] = []
        for row in rows:
            by_model = row["by_model"]
            cost_sum = sum(cost_for_bucket(m, b) for m, b in by_model.items())
            out.append(
                {
                    "step": row["step"],
                    "start": row["start"],
                    "models": {
                        m: {"tokens": b, "cost_usd": cost_for_bucket(m, b)}
                        for m, b in by_model.items()
                    },
                    "cost_usd": round(cost_sum, 4),
                }
            )
        json.dump(out, sys.stdout, indent=2)
        print()
        return 0

    # Text table.
    hdr = ("step", "start", "model", "in", "out", "cache_r", "cache_w", "cost_usd")
    print("\t".join(hdr))
    total = 0.0
    for row in rows:
        by_model = row["by_model"]
        if not by_model:
            print(f"{row['step']}\t{row['start']}\t-\t0\t0\t0\t0\t0.0000")
            continue
        for model, bucket in by_model.items():
            cost = cost_for_bucket(model, bucket)
            total += cost
            print(
                f"{row['step']}\t{row['start']}\t{model}\t"
                f"{bucket['input']}\t{bucket['output']}\t"
                f"{bucket['cache_read']}\t{bucket['cache_write_5m']}\t"
                f"{cost:.4f}"
            )
    print(f"total\t-\t-\t-\t-\t-\t-\t{total:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
