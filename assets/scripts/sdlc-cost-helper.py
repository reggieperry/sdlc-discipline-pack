#!/usr/bin/env python3
"""Compute cost_usd for an SDLC chain-agent phase from its Claude Code session JSONL.

Each Gas City pool-agent spawn runs `claude` with a fresh session in a
per-instance worktree (e.g., `.gc/worktrees/<rig>/sdlc/sdlc-discipline.worker-1`).
Claude Code records the conversation, including per-turn token usage and the
model id, in `~/.claude/projects/<encoded-worktree-path>/<session-uuid>.jsonl`.

This helper finds the JSONLs that correspond to a phase's run, sums input,
output, and cache tokens by model, applies Anthropic's per-million-token
pricing, and prints the total cost in USD.

Usage
-----
    sdlc-cost-helper.py --worktree /path/to/agent/worktree \\
                        --started-at 2026-05-11T00:27:48-07:00 \\
                        --completed-at 2026-05-11T01:12:25-07:00

The time window selects only JSONLs whose mtime falls inside it. Multiple
JSONLs in the window are summed (rare, but possible if the agent restarted
itself mid-phase).

Output: a single line with the total cost as a decimal float to four decimal
places (e.g., `0.4317`). Prints `0.0` if no matching JSONL is found or any
parse fails; the script never raises into the caller. The rollup CSV row is
still useful for audit even without a populated cost.

Stdlib only — no dependencies on the pack's other Python code.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Per-million-token pricing in USD. Update when Anthropic publishes new
# pricing or when new models ship. Source:
#   https://docs.claude.com/en/docs/about-claude/models#model-comparison
# (or the API pricing reference page; keep in sync).
#
# Keys are the model ids that Claude Code writes into the JSONL `message.model`
# field. Cache pricing follows Anthropic's prompt-caching docs.
PRICING_PER_MTOK_USD = {
    "claude-opus-4-7": {
        "input": 15.00,
        "output": 75.00,
        "cache_write_5m": 18.75,
        "cache_read": 1.50,
    },
    "claude-opus-4-6": {
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
    "claude-sonnet-4-5": {
        "input": 3.00,
        "output": 15.00,
        "cache_write_5m": 3.75,
        "cache_read": 0.30,
    },
    "claude-haiku-4-5": {
        "input": 0.80,
        "output": 4.00,
        "cache_write_5m": 1.00,
        "cache_read": 0.08,
    },
}


def encode_worktree_path(worktree: str) -> str:
    """Replicate Claude Code's project-directory encoding.

    Claude Code stores per-project conversation logs at
    `~/.claude/projects/<encoded-path>/`. The encoding replaces every `/`,
    `_`, and `.` in the absolute path with `-`. Consecutive substitutions
    produce double-dashes, which is the canonical form — do not collapse.
    Example:
      `/home/reggie/elder_trading_system/.gc` →
      `-home-reggie-elder-trading-system--gc`
    """
    p = os.path.abspath(worktree).rstrip("/")
    encoded = p
    for ch in ("/", "_", "."):
        encoded = encoded.replace(ch, "-")
    return encoded


def parse_iso(ts: str) -> datetime | None:
    """Tolerant ISO 8601 parse. Returns timezone-aware UTC datetime, or None."""
    if not ts:
        return None
    try:
        # Python's fromisoformat handles offsets like +00:00 and -07:00 in 3.11+.
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        return None


def find_jsonls_for_phase(
    worktree: str, started_at: datetime, completed_at: datetime
) -> list[Path]:
    """Return JSONL paths under ~/.claude/projects/<encoded>/ within the time window."""
    encoded = encode_worktree_path(worktree)
    project_dir = Path.home() / ".claude" / "projects" / encoded
    if not project_dir.is_dir():
        return []
    # Allow generous slop (300s) so JSONL files whose mtime trails the bead's
    # recorded `completed_at` still match. The bead's `completed_at` is set
    # by the agent's `bd update` near the end of its work, but the JSONL
    # continues to be appended (and the file system buffer flushed) for tens
    # of seconds afterward as the session cleans up. Empirically observed
    # mtimes ~90s past completed_at; 300s keeps a comfortable margin without
    # false-matching adjacent sessions (which on a pool with idle_timeout=30m
    # are typically many minutes apart).
    slop_s = 300
    out: list[Path] = []
    for jsonl in project_dir.glob("*.jsonl"):
        try:
            mtime = datetime.fromtimestamp(jsonl.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if (
            (started_at.timestamp() - slop_s)
            <= mtime.timestamp()
            <= (completed_at.timestamp() + slop_s)
        ):
            out.append(jsonl)
    return out


def sum_usage_by_model(paths: list[Path]) -> dict[str, dict[str, int]]:
    """Sum input/output/cache_write/cache_read tokens per model across all JSONLs."""
    totals: dict[str, dict[str, int]] = {}
    for path in paths:
        try:
            with path.open() as f:
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
                    model = msg.get("model")
                    usage = msg.get("usage")
                    if not model or not isinstance(usage, dict):
                        continue
                    bucket = totals.setdefault(
                        model,
                        {"input": 0, "output": 0, "cache_write_5m": 0, "cache_read": 0},
                    )
                    bucket["input"] += int(usage.get("input_tokens", 0) or 0)
                    bucket["output"] += int(usage.get("output_tokens", 0) or 0)
                    bucket["cache_write_5m"] += int(
                        usage.get("cache_creation_input_tokens", 0) or 0
                    )
                    bucket["cache_read"] += int(usage.get("cache_read_input_tokens", 0) or 0)
        except OSError:
            continue
    return totals


def usage_to_usd(totals_by_model: dict[str, dict[str, int]]) -> float:
    """Apply pricing. Unknown models contribute 0 with a warning to stderr."""
    cost = 0.0
    for model, toks in totals_by_model.items():
        price = PRICING_PER_MTOK_USD.get(model)
        if price is None:
            sys.stderr.write(f"sdlc-cost-helper: no pricing for model {model!r}; counted as $0\n")
            continue
        for k, v in toks.items():
            cost += (v / 1_000_000) * price.get(k, 0.0)
    return cost


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--worktree", required=True, help="Agent worktree absolute path")
    p.add_argument("--started-at", required=True, help="Phase start (ISO 8601)")
    p.add_argument("--completed-at", required=True, help="Phase end (ISO 8601)")
    args = p.parse_args()

    started = parse_iso(args.started_at)
    completed = parse_iso(args.completed_at)
    if started is None or completed is None or completed < started:
        # Print 0.0 silently — caller (cost-rollup.sh) treats a 0 row as a
        # baseline duration-only entry, not a cost computation failure.
        print("0.0000")
        return 0

    jsonls = find_jsonls_for_phase(args.worktree, started, completed)
    if not jsonls:
        print("0.0000")
        return 0

    totals = sum_usage_by_model(jsonls)
    cost = usage_to_usd(totals)
    print(f"{cost:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
