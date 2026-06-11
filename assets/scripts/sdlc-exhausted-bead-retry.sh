#!/usr/bin/env bash
# sdlc-exhausted-bead-retry.sh — supervisor-side outer-loop retry for
# beads exhausted by the v2.18 claude-with-retry wrapper (pack #47).
#
# The wrapper performs in-process retry (default 5 attempts via
# `claude --resume`) on Mode A (per-turn cap) and Mode B (529 overload)
# exits. When the in-process loop exhausts, the wrapper exits 75 and
# writes:
#
#   <template>.state=exhausted
#   <template>.last_exit_cause=<cause>
#   <template>.exhausted_at=<ISO-8601 timestamp>
#
# This watcher reacts to that state. For each bead at
# <template>.state=exhausted whose exhausted_at is older than the
# backoff threshold, re-slings the bead to the same pool target
# (`gc.routed_to`) and increments a retry-count.
#
# Env knobs:
#   SDLC_EXHAUSTED_BEAD_RETRY_ENABLED   default "false"
#   SDLC_EXHAUSTED_BEAD_BACKOFF_MINUTES default 30
#   SDLC_EXHAUSTED_BEAD_MAX_RETRIES     default 3
#
# Per-bead retry budget enforced via <template>.retry_count. After cap,
# the watcher leaves the bead alone with state=retry_count_exhausted
# + notifies the operator. Manual operator intervention takes over
# from there.
#
# Idempotent: a bead currently being processed by the watcher (state
# already flipped from exhausted to running by an earlier tick) is
# naturally skipped because the watcher only acts on state=exhausted.
#
# Closes pack #47 (supervisor-side outer loop).

set -uo pipefail

# shellcheck source=lib/sdlc-exit-history.sh
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/sdlc-exit-history.sh"

if [ "${SDLC_EXHAUSTED_BEAD_RETRY_ENABLED:-false}" != "true" ]; then
    exit 0
fi

# Resolve the city root via the shared resolver — gascity retired
# GC_CITY_ROOT from the order-exec env (issue #204); it now emits GC_CITY.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CITY_ROOT=$(bash "$SCRIPT_DIR/lib/sdlc-find-city-root.sh" 2>/dev/null) || CITY_ROOT=""
if [ -z "$CITY_ROOT" ] || [ ! -d "$CITY_ROOT" ]; then
    echo "exhausted-bead-retry: cannot resolve city root (GC_CITY_ROOT='${GC_CITY_ROOT:-}' GC_CITY='${GC_CITY:-}' PWD='$PWD')" >&2
    exit 0
fi

if [ -z "${PACK_DIR:-}" ]; then
    PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

BACKOFF_MINUTES="${SDLC_EXHAUSTED_BEAD_BACKOFF_MINUTES:-30}"
MAX_RETRIES="${SDLC_EXHAUSTED_BEAD_MAX_RETRIES:-3}"
NOTIFY="$PACK_DIR/assets/scripts/sdlc-notify.sh"

# Locate the shared rig-enumeration library relative to this script.
# Tests override PACK_DIR to point at a fake pack root; the library
# always ships next to this script so prefer the script-relative path.
RIG_LISTER="$SCRIPT_DIR/lib/sdlc-list-rigs.sh"

# Per-rig: walk closed AND open beads with any <template>.state=exhausted
# field. A bead's state could be open (worker died mid-step; supervisor
# may have left it open) or in_progress (worker is "alive" per bd but
# the wrapper exited). Filter via python because bd's CLI flag set
# doesn't expose "any metadata key matches state=exhausted" directly.
reconcile_rig() {
    local rig="$1"
    local rig_root="$2"

    if [ ! -d "$rig_root" ]; then
        return
    fi

    local actions_out
    actions_out=$(cd "$rig_root" && BACKOFF_MINUTES="$BACKOFF_MINUTES" \
        MAX_RETRIES="$MAX_RETRIES" RIG_NAME="$rig" RIG_ROOT="$rig_root" \
        python3 - <<'PYEOF'
"""Per-rig exhausted-bead scan. Outputs one JSON line per actionable
bead; bash dispatches each action."""

import datetime as dt
import json
import os
import subprocess
import sys

BACKOFF_MINUTES = int(os.environ["BACKOFF_MINUTES"])
MAX_RETRIES = int(os.environ["MAX_RETRIES"])
RIG_NAME = os.environ["RIG_NAME"]
RIG_ROOT = os.environ["RIG_ROOT"]

TEMPLATES = ("planner", "worker", "tester", "reviewer", "documenter", "finalizer")

NOW = dt.datetime.now(dt.timezone.utc)
THRESHOLD = NOW - dt.timedelta(minutes=BACKOFF_MINUTES)


def parse_iso(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


try:
    proc = subprocess.run(
        ["bd", "-C", RIG_ROOT, "list", "--all", "--limit", "5000", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        sys.exit(0)
    beads = json.loads(proc.stdout or "[]")
except (subprocess.SubprocessError, json.JSONDecodeError):
    sys.exit(0)


for bead in beads:
    meta = bead.get("metadata") or {}
    # Find which template (if any) is exhausted on this bead.
    matched_template: str | None = None
    for tmpl in TEMPLATES:
        if meta.get(f"{tmpl}.state") == "exhausted":
            matched_template = tmpl
            break
    if matched_template is None:
        continue

    exhausted_at = parse_iso(meta.get(f"{matched_template}.exhausted_at", ""))
    if exhausted_at is None or exhausted_at > THRESHOLD:
        # Either no timestamp (older bead; pre-v2.26.0 wrapper) or
        # not yet past the backoff window. Leave alone.
        continue

    retry_count = int(meta.get(f"{matched_template}.retry_count", "0") or 0)
    bead_id = bead.get("id", "")
    pool_target = meta.get("gc.routed_to", "")
    cause = meta.get(f"{matched_template}.last_exit_cause", "unknown")

    if retry_count >= MAX_RETRIES:
        action = {
            "bead_id": bead_id,
            "template": matched_template,
            "action": "give_up",
            "retry_count": retry_count,
            "cause": cause,
            "pool_target": pool_target,
        }
    else:
        action = {
            "bead_id": bead_id,
            "template": matched_template,
            "action": "resling",
            "retry_count": retry_count + 1,
            "cause": cause,
            "pool_target": pool_target,
        }
    print(json.dumps(action))
PYEOF
        )

    if [ -z "$actions_out" ]; then
        return
    fi

    local reslings=0
    local give_ups=0
    while IFS= read -r action_json; do
        [ -z "$action_json" ] && continue
        local bead_id template action retry_count cause pool_target
        bead_id=$(echo "$action_json" | jq -r '.bead_id')
        template=$(echo "$action_json" | jq -r '.template')
        action=$(echo "$action_json" | jq -r '.action')
        retry_count=$(echo "$action_json" | jq -r '.retry_count')
        cause=$(echo "$action_json" | jq -r '.cause')
        pool_target=$(echo "$action_json" | jq -r '.pool_target')

        if [ "$action" = "resling" ]; then
            # Clear state + increment retry count + route back to pool.
            # Clear assignee so supervisor's pool scale-check sees demand.
            # Append a watcher_resling entry to exit_history so the full
            # retry lifecycle is reachable from one field per pack #182.
            (cd "$rig_root" && bd update "$bead_id" \
                --status=open \
                --assignee "" \
                --set-metadata "${template}.state=resuming" \
                --set-metadata "${template}.retry_count=${retry_count}" \
                --set-metadata "${template}.last_resling_at=$(date -Iseconds)" \
                ${pool_target:+--set-metadata "gc.routed_to=$pool_target"} \
                >/dev/null 2>&1 \
                && sdlc_append_exit_history "$bead_id" "$template" "watcher_resling" "$cause") \
                && reslings=$((reslings + 1)) \
                && echo "exhausted-bead-retry: rig=$rig re-slung $bead_id template=$template count=$retry_count cause=$cause" >&2
        elif [ "$action" = "give_up" ]; then
            # Record both gave_up_at AND gave_up_cause so the give-up
            # record itself pins which cause caused the cap (pack #182).
            (cd "$rig_root" && bd update "$bead_id" \
                --set-metadata "${template}.state=retry_count_exhausted" \
                --set-metadata "${template}.gave_up_at=$(date -Iseconds)" \
                --set-metadata "${template}.gave_up_cause=${cause}" \
                >/dev/null 2>&1 \
                && sdlc_append_exit_history "$bead_id" "$template" "watcher_gave_up" "$cause") \
                && give_ups=$((give_ups + 1)) \
                && echo "exhausted-bead-retry: rig=$rig giving up on $bead_id template=$template count=$retry_count cause=$cause" >&2
        fi
    done <<< "$actions_out"

    if [ "$reslings" -gt 0 ] || [ "$give_ups" -gt 0 ]; then
        if [ -x "$NOTIFY" ]; then
            "$NOTIFY" \
                --subject "[exhausted-bead-retry] rig=$rig — $reslings re-slung, $give_ups gave up" \
                --body "Exhausted-bead-retry watcher fired against rig $rig: $reslings bead(s) re-slung to their pools after the backoff window; $give_ups bead(s) hit the retry cap and are queued for operator triage. See sdlc-exhausted-bead-retry stderr for per-bead detail." \
                2>/dev/null || true
        fi
    fi
}

while IFS=$'\t' read -r rig_name rig_path; do
    [ -z "$rig_name" ] && continue
    reconcile_rig "$rig_name" "$rig_path"
done < <(bash "$RIG_LISTER" "$CITY_ROOT")

exit 0
