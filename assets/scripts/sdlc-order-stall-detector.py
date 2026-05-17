"""Order-fire stall detector (pack #44 sub-story 5).

Companion to `sdlc-stall-detector.py` (sub-story 4). The bead-phase
detector catches chains stuck mid-phase; this script catches the
*other* silent failure: cron-triggered orders that should be firing on
a cooldown but aren't. The motivating case is the rebase-watcher
non-fire incident from earlier in May 2026 — silent for ~9 hours
overnight, surfaced only by manual investigation.

Approach: query `gc order list` for the set of cooldown-trigger orders
and their configured intervals. Query `gc order history <name>` for the
most recent execution timestamp. If the gap exceeds `interval × 2`,
emit an operator email via `sdlc-notify.sh`.

The `× 2` multiplier covers the supervisor tick jitter and the natural
case where a cooldown has just elapsed but the next tick hasn't fired
yet. A multiplier of 1 would produce false positives at every healthy
order's cooldown boundary; a higher multiplier delays detection of
real failures. 2 is the design-note default.

Throttle: same shape as sub-story 4 — `(order_name, last_alerted)`
dedup with a four-hour window. The throttle state lives in a per-rig
state file at `<rig-root>/.gc/order-stall-state.json` because orders
themselves aren't beads and have no metadata surface to write to.

Graceful degradation:

- `gc order list` fails → log to stderr, exit 0. Next tick retries.
- `gc order history <name>` empty (order has never fired) → skip;
  fresh orders need time to establish a baseline.
- The state file is corrupt or unwritable → log, exit 0. The next
  tick will recreate it from a fresh state.

CLI:

    python3 sdlc-order-stall-detector.py
        [--now <iso8601>]         # injectable clock for tests
        [--throttle-hours <int>]  # default 4
        [--multiplier <int>]      # default 2
        [--state-file <path>]     # default $GC_CITY_ROOT/.gc/order-stall-state.json
        [--notify-bin <path>]     # default sdlc-notify.sh
        [--orders-only <csv>]     # restrict to a comma-separated subset (test hook)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_DEFAULT_THROTTLE_HOURS = 4
_DEFAULT_MULTIPLIER = 2

# `gc order list` outputs whitespace-delimited tabular text. The interval
# column carries Go-duration strings (`5m`, `30s`, `15m`, `1h`, `24h`,
# `6h`). Cron-trigger orders appear with a schedule like `0 */4 * * *`
# in the same column slot, so we exclude rows whose trigger isn't
# `cooldown`.
_DURATION_RE = re.compile(r"^(\d+)(s|m|h|d)$")


@dataclass(frozen=True)
class OrderInfo:
    name: str
    interval_seconds: int
    rig: str


@dataclass(frozen=True)
class OrderStallAlert:
    order_name: str
    rig: str
    elapsed_seconds: int
    expected_seconds: int  # interval × multiplier
    last_executed_iso: str


def parse_duration(s: str) -> int | None:
    """Parse a Go-duration string into seconds. Returns None for unknown shapes."""
    m = _DURATION_RE.match(s)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return n * mult


def parse_order_list(output: str) -> list[OrderInfo]:
    """Parse the tabular output of `gc order list` into OrderInfo records.

    The output has a header line, then rows with whitespace-separated
    columns. The columns are not single-token: the SCHED column can be
    a cron expression with spaces. Right-side columns (RIG, TARGET) can
    be `-`. The order is: NAME, TYPE, TRIGGER, INTERVAL_OR_SCHED, RIG,
    TARGET. We restrict to TRIGGER == cooldown so we sidestep cron
    schedules (sub-story 4 doesn't claim coverage of cron triggers).
    """
    out: list[OrderInfo] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("NAME") or line.startswith("-"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        name, kind, trigger = parts[0], parts[1], parts[2]
        if trigger != "cooldown":
            continue
        interval_str = parts[3]
        rig = parts[4] if len(parts) > 4 else "-"
        seconds = parse_duration(interval_str)
        if seconds is None:
            continue
        out.append(OrderInfo(name=name, interval_seconds=seconds, rig=rig))
    return out


def parse_order_history_latest(output: str) -> datetime | None:
    """Return the most-recent EXECUTED timestamp from `gc order history` output.

    History rows are sorted newest-first by the gc command, so the first
    data row is the latest. Returns None when no fire is recorded
    (fresh order with no history).
    """
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("ORDER"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        ts_str = parts[-1]
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    return None


def find_stalls(
    orders: list[OrderInfo],
    last_fires: dict[str, datetime],
    now: datetime,
    multiplier: int,
    throttled: set[str],
) -> list[OrderStallAlert]:
    """Compute the list of orders whose last fire is older than interval × multiplier.

    `last_fires` maps order_name → most-recent execution; orders absent
    from the dict have never fired and are skipped. `throttled` is the
    set of order names whose alert is suppressed by the throttle state.
    Pure function — no I/O.
    """
    alerts: list[OrderStallAlert] = []
    for order in orders:
        if order.name in throttled:
            continue
        last = last_fires.get(order.name)
        if last is None:
            continue
        expected_seconds = order.interval_seconds * multiplier
        elapsed = (now - last).total_seconds()
        if elapsed <= expected_seconds:
            continue
        alerts.append(
            OrderStallAlert(
                order_name=order.name,
                rig=order.rig,
                elapsed_seconds=int(elapsed),
                expected_seconds=expected_seconds,
                last_executed_iso=last.isoformat(),
            )
        )
    return alerts


def load_state(path: Path) -> dict[str, str]:
    """Load the throttle state file. Returns {} on missing or corrupt file."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"sdlc-order-stall-detector: state file unreadable ({exc}); resetting",
            file=sys.stderr,
        )
        return {}


def save_state(path: Path, state: dict[str, str]) -> None:
    """Persist throttle state. Best-effort; non-fatal on write failure."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, sort_keys=True, indent=2))
    except OSError as exc:
        print(
            f"sdlc-order-stall-detector: state file write failed ({exc})",
            file=sys.stderr,
        )


def throttled_orders(state: dict[str, str], now: datetime, window: timedelta) -> set[str]:
    """Return order names whose alert is currently within the throttle window."""
    out: set[str] = set()
    for name, ts_str in state.items():
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if (now - ts) < window:
            out.add(name)
    return out


def render_email_body(alert: OrderStallAlert) -> tuple[str, str]:
    """Build (subject, body) for the alert."""
    elapsed_min = alert.elapsed_seconds // 60
    expected_min = alert.expected_seconds // 60
    rig_tag = f"[{alert.rig}] " if alert.rig and alert.rig != "-" else ""
    subject = (
        f"{rig_tag}[order-stall-warning] order `{alert.order_name}` last fired "
        f"{elapsed_min} min ago (expected within {expected_min} min)"
    )
    body = (
        f"The cooldown-triggered order `{alert.order_name}` has not fired in "
        f"{elapsed_min} minutes. Its configured interval × 2 is "
        f"{expected_min} minutes, so the gap is past the expected window.\n\n"
        f"Last execution: {alert.last_executed_iso}\n"
        f"Rig: {alert.rig}\n\n"
        f"Investigate with:\n\n"
        f"    gc order history {alert.order_name}\n"
        f"    gc order check\n\n"
        f"This alert is throttled. Re-alert fires after four hours if the "
        f"order still has not fired."
    )
    return subject, body


def invoke_notify(notify_bin: str, subject: str, body: str) -> int:
    """Send the alert via sdlc-notify.sh. Returns helper exit code; 127 on exec failure."""
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
            f"sdlc-order-stall-detector: sdlc-notify.sh not found at {notify_bin}; alert dropped",
            file=sys.stderr,
        )
        return 127
    if proc.returncode != 0:
        print(
            f"sdlc-order-stall-detector: sdlc-notify.sh exited "
            f"{proc.returncode}: {proc.stderr.strip()}",
            file=sys.stderr,
        )
    return proc.returncode


def fetch_orders() -> list[OrderInfo]:
    """Run `gc order list` and parse. Returns [] on gc failure."""
    proc = subprocess.run(
        ["gc", "order", "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(
            f"sdlc-order-stall-detector: gc order list failed: {proc.stderr.strip()}",
            file=sys.stderr,
        )
        return []
    return parse_order_list(proc.stdout)


def fetch_last_fire(order_name: str) -> datetime | None:
    """Run `gc order history <name>` and parse the most recent fire timestamp."""
    proc = subprocess.run(
        ["gc", "order", "history", order_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(
            f"sdlc-order-stall-detector: gc order history {order_name} failed: "
            f"{proc.stderr.strip()}",
            file=sys.stderr,
        )
        return None
    return parse_order_history_latest(proc.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect cron-order missed fires and emit operator email alerts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--now", type=str, default=None)
    parser.add_argument("--throttle-hours", type=int, default=_DEFAULT_THROTTLE_HOURS)
    parser.add_argument("--multiplier", type=int, default=_DEFAULT_MULTIPLIER)
    parser.add_argument(
        "--state-file",
        type=Path,
        default=None,
        help="path for throttle state (default: $GC_CITY_ROOT/.gc/order-stall-state.json)",
    )
    parser.add_argument(
        "--notify-bin",
        type=str,
        default=os.environ.get("SDLC_NOTIFY_BIN", "sdlc-notify.sh"),
    )
    parser.add_argument(
        "--orders-only",
        type=str,
        default="",
        help="comma-separated subset of order names to check (default: all cooldown orders)",
    )
    args = parser.parse_args(argv)

    if args.now:
        try:
            now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
            if now.tzinfo is None:
                now = now.replace(tzinfo=UTC)
        except ValueError:
            print(f"sdlc-order-stall-detector: bad --now {args.now!r}", file=sys.stderr)
            return 2
    else:
        now = datetime.now(UTC)

    state_file = (
        args.state_file
        or Path(os.environ.get("GC_CITY_ROOT", ".")) / ".gc" / "order-stall-state.json"
    )
    state = load_state(state_file)
    throttle = timedelta(hours=args.throttle_hours)
    throttled = throttled_orders(state, now, throttle)

    orders = fetch_orders()
    if args.orders_only:
        wanted = {n.strip() for n in args.orders_only.split(",") if n.strip()}
        orders = [o for o in orders if o.name in wanted]
    if not orders:
        return 0

    last_fires: dict[str, datetime] = {}
    for order in orders:
        ts = fetch_last_fire(order.name)
        if ts is not None:
            last_fires[order.name] = ts

    alerts = find_stalls(orders, last_fires, now, args.multiplier, throttled)
    if not alerts:
        return 0

    for alert in alerts:
        subject, body = render_email_body(alert)
        if invoke_notify(args.notify_bin, subject, body) == 0:
            state[alert.order_name] = now.isoformat()
    save_state(state_file, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
