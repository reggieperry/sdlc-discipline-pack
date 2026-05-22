#!/usr/bin/env bash
# sdlc-alive-idle-detector.sh — pack-side workaround for Mode C stalls
# (pack#86).
#
# Triggered periodically by orders/sdlc-alive-idle-detector.toml. For each
# in_progress chain bead with an assignee, computes elapsed time since the
# last bead.updated event in events.jsonl. When the gap exceeds the
# threshold, captures the worker's tmux pane and matches against the
# at-prompt signature (presence of '❯ ' + 'new task?' AND absence of
# busy markers). On match, invokes `gc session submit <id> "continue"
# --intent default` to inject a synthetic user turn. Notifies the operator
# on every nudge.
#
# See the issue body at reggieperry/sdlc-discipline-pack#86 for the
# full design and the gascity verification report at
# /home/reggie/strategic/gc-session-submit-viability-2026-05-22.md
# for transport, intent, and async-API details.
#
# Feature gate: defaults OFF. Activation via
# SDLC_ALIVE_IDLE_DETECTOR_ENABLED=true in the supervisor's env.
#
# Environment provided by the order trigger:
#   GC_RIG              rig name (optional; multi-rig support is v2)
#
# Test injection points (override the executables and paths used):
#   SDLC_ALIVE_IDLE_GC             default `gc`
#   SDLC_ALIVE_IDLE_TMUX           default `tmux`
#   SDLC_ALIVE_IDLE_NOTIFY         default `sdlc-notify.sh`
#   SDLC_ALIVE_IDLE_STATE_DIR      default `$HOME/.gc`
#   SDLC_ALIVE_IDLE_EVENTS_PATH    override the events.jsonl path
#
# Tunables:
#   SDLC_ALIVE_IDLE_DETECTOR_ENABLED       default "false"
#   SDLC_ALIVE_IDLE_THRESHOLD_MINUTES      default 20
#   SDLC_ALIVE_IDLE_NUDGE_COOLDOWN_MINUTES default 10
#
# Exit codes:
#   0   Detector ran (with or without action taken). Includes feature-gate-off.
#   1   Submit failed for at least one bead (notify still fires).
#   2   Unexpected internal error (state-file IO, malformed bd output).

set -uo pipefail

GC_BIN="${SDLC_ALIVE_IDLE_GC:-gc}"
TMUX_BIN="${SDLC_ALIVE_IDLE_TMUX:-tmux}"
NOTIFY_BIN="${SDLC_ALIVE_IDLE_NOTIFY:-sdlc-notify.sh}"
STATE_DIR="${SDLC_ALIVE_IDLE_STATE_DIR:-$HOME/.gc}"
EVENTS_PATH="${SDLC_ALIVE_IDLE_EVENTS_PATH:-}"

THRESHOLD_MINUTES="${SDLC_ALIVE_IDLE_THRESHOLD_MINUTES:-20}"
COOLDOWN_MINUTES="${SDLC_ALIVE_IDLE_NUDGE_COOLDOWN_MINUTES:-10}"
ENABLED="${SDLC_ALIVE_IDLE_DETECTOR_ENABLED:-false}"

if [ "$ENABLED" != "true" ]; then
    exit 0
fi

mkdir -p "$STATE_DIR" 2>/dev/null || { echo "state dir create failed" >&2; exit 2; }
STATE_FILE="$STATE_DIR/alive-idle-nudges.json"

# Resolve events.jsonl path. Production sources, in order:
#   1. SDLC_ALIVE_IDLE_EVENTS_PATH explicit override (used by tests)
#   2. $GC_CITY_ROOT/.gc/events.jsonl
#   3. Walk up from $PWD looking for a directory containing .gc/events.jsonl
#   4. gc cities first-row fallback
# Fail-closed: if we can't resolve, log and exit 2 — refusing to act is the
# right behavior, since without event-age we'd false-positive on every bead.
if [ -z "$EVENTS_PATH" ]; then
    CITY_ROOT="${GC_CITY_ROOT:-}"
    if [ -z "$CITY_ROOT" ]; then
        # Walk up from $PWD looking for .gc/events.jsonl.
        d="$PWD"
        while [ "$d" != "/" ] && [ -n "$d" ]; do
            if [ -f "$d/.gc/events.jsonl" ]; then
                CITY_ROOT="$d"
                break
            fi
            d=$(dirname "$d")
        done
    fi
    if [ -z "$CITY_ROOT" ]; then
        CITY_ROOT=$("$GC_BIN" cities 2>/dev/null | awk 'NR>1 {print $2; exit}')
    fi
    if [ -z "$CITY_ROOT" ] || [ ! -f "$CITY_ROOT/.gc/events.jsonl" ]; then
        echo "sdlc-alive-idle-detector: cannot resolve events.jsonl path (GC_CITY_ROOT='${GC_CITY_ROOT:-}' PWD='$PWD'); aborting" >&2
        exit 2
    fi
    EVENTS_PATH="$CITY_ROOT/.gc/events.jsonl"
fi

# Stage A: fetch in_progress beads and the session list.
BEADS_JSON=$("$GC_BIN" bd list --status=in_progress --json 2>/dev/null || echo "[]")
SESSIONS_JSON=$("$GC_BIN" session list --json 2>/dev/null || echo "[]")

# Stage B (Python): for each bead with an assignee, decide whether stage-1
# (event-age) AND rate-limit pass. Emit one line per candidate that we need
# to pane-check, in the form: BEAD_ID<TAB>SESSION_ID<TAB>TMUX_PANE<TAB>TMUX_SOCKET
CANDIDATES=$(BEADS_JSON="$BEADS_JSON" \
             SESSIONS_JSON="$SESSIONS_JSON" \
             STATE_FILE="$STATE_FILE" \
             EVENTS_PATH="$EVENTS_PATH" \
             THRESHOLD_MINUTES="$THRESHOLD_MINUTES" \
             COOLDOWN_MINUTES="$COOLDOWN_MINUTES" \
             python3 - <<'PY'
import json
import os
import re
import sys
import time
from pathlib import Path

beads = json.loads(os.environ.get("BEADS_JSON", "[]") or "[]")
sessions = json.loads(os.environ.get("SESSIONS_JSON", "[]") or "[]")
state_file = os.environ.get("STATE_FILE", "")
events_path = os.environ.get("EVENTS_PATH", "")
threshold_minutes = int(os.environ.get("THRESHOLD_MINUTES", "20"))
cooldown_minutes = int(os.environ.get("COOLDOWN_MINUTES", "10"))
now_epoch = int(time.time())

# Load state file (per-bead last-nudge timestamps). Tolerate missing/malformed.
state = {}
if state_file:
    p = Path(state_file)
    if p.exists():
        try:
            state = json.loads(p.read_text() or "{}")
        except (json.JSONDecodeError, OSError):
            state = {}

# Index sessions by id for O(1) lookup; record state + pane info.
session_index = {}
for s in sessions:
    sid = s.get("id") or s.get("metadata", {}).get("session_name")
    if not sid:
        continue
    session_index[sid] = s

# Pre-parse events.jsonl once: dict[bead_id] -> latest ts (seconds since epoch).
# Each line is JSON with ts (ISO local-time string) and message containing the
# bead id as a leading token. We extract the bead id from the message.
latest_update = {}
if events_path:
    p = Path(events_path)
    if p.exists():
        for line in p.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") != "bead.updated":
                continue
            msg = ev.get("message", "") or ""
            # Extract bead id — first token before ':' or ' '.
            m = re.match(r"\s*([A-Za-z0-9_\-]+)", msg)
            if not m:
                continue
            bead_id = m.group(1)
            ts_str = ev.get("ts", "")
            try:
                # ISO local time without timezone — parse and convert to epoch.
                # Format: YYYY-MM-DDTHH:MM:SS
                parsed = time.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
                ts_epoch = int(time.mktime(parsed))
            except (ValueError, TypeError):
                continue
            cur = latest_update.get(bead_id, 0)
            if ts_epoch > cur:
                latest_update[bead_id] = ts_epoch

# Walk beads; emit one line per candidate that passes stage 1 + cooldown.
for b in beads:
    bead_id = b.get("id")
    assignee = b.get("assignee") or b.get("metadata", {}).get("worker", {}).get("session_id")
    if not bead_id or not assignee:
        continue
    # Stage 1 — event age.
    last_ts = latest_update.get(bead_id)
    if last_ts is not None:
        age_seconds = now_epoch - last_ts
        if age_seconds < threshold_minutes * 60:
            continue
    # else: no event found for this bead → treat as stale enough to proceed.
    # Cooldown — skip if we nudged recently.
    last_nudge = state.get(bead_id)
    if last_nudge is not None:
        nudge_age = now_epoch - int(last_nudge)
        if nudge_age < cooldown_minutes * 60:
            continue
    # Resolve session for pane info.
    s = session_index.get(assignee, {})
    meta = s.get("metadata", {}) or {}
    pane = meta.get("tmux_pane", "")
    sock = meta.get("tmux_socket", "")
    # State of session — only proceed if explicitly "active". Defensive: any
    # session not classified active gets skipped (creating/suspended/closing).
    if s.get("state", "active") != "active":
        continue
    # If we have no pane name, we can't capture — skip (would be a multi-rig
    # follow-up to derive it from session_name).
    if not pane:
        # Fall back to session_id as pane name (matches how tmux names panes
        # in the bright-lights tmux socket).
        pane = assignee
    print(f"{bead_id}\t{assignee}\t{pane}\t{sock}")
PY
)

# If no candidates, we're done — exit clean.
if [ -z "$CANDIDATES" ]; then
    exit 0
fi

EXIT_CODE=0
NUDGED_BEADS=()

while IFS=$'\t' read -r BEAD_ID SESSION_ID TMUX_PANE TMUX_SOCKET; do
    [ -z "$BEAD_ID" ] && continue

    # Stage 2 — pane signature. Build tmux invocation honoring socket override.
    if [ -n "$TMUX_SOCKET" ]; then
        PANE_CONTENT=$("$TMUX_BIN" -S "$TMUX_SOCKET" capture-pane -t "$TMUX_PANE" -p 2>/dev/null || true)
    else
        PANE_CONTENT=$("$TMUX_BIN" capture-pane -t "$TMUX_PANE" -p 2>/dev/null || true)
    fi

    # Reject if any live busy marker is present. 'esc to interrupt' is the
    # canonical signal — gascity's paneContainsBusyIndicator uses the same
    # string. 'Implementing…' and 'Crafting…' are belt-and-suspenders:
    # they appear with the ✽ live-activity glyph and would only show during
    # the model's working phase. Past-tense markers like 'Brewed for X' and
    # 'Cooked for X' are NOT busy markers — they persist into the at-prompt
    # idle state showing the last turn's duration.
    if echo "$PANE_CONTENT" | grep -qE 'esc to interrupt|Implementing…|Crafting…'; then
        continue
    fi
    # Require both the prompt glyph and the 'new task?' footer hint.
    if ! echo "$PANE_CONTENT" | grep -q '❯'; then
        continue
    fi
    if ! echo "$PANE_CONTENT" | grep -q 'new task?'; then
        continue
    fi

    # Action — submit the synthetic user turn.
    if "$GC_BIN" session submit "$SESSION_ID" "continue" --intent default >/dev/null 2>&1; then
        NUDGED_BEADS+=("$BEAD_ID")
        "$NOTIFY_BIN" --subject "alive-idle nudge fired" \
                      --body "Nudged stalled worker session $SESSION_ID (bead $BEAD_ID)" \
                      >/dev/null 2>&1 || true
    else
        EXIT_CODE=1
        "$NOTIFY_BIN" --subject "alive-idle nudge FAILED" \
                      --body "gc session submit failed for $SESSION_ID (bead $BEAD_ID)" \
                      >/dev/null 2>&1 || true
    fi
done <<< "$CANDIDATES"

# Update state file with successful nudges.
if [ ${#NUDGED_BEADS[@]} -gt 0 ]; then
    NUDGED_LIST=$(printf '%s\n' "${NUDGED_BEADS[@]}")
    STATE_FILE="$STATE_FILE" NUDGED_LIST="$NUDGED_LIST" python3 - <<'PY'
import json, os, time
from pathlib import Path

state_file = os.environ["STATE_FILE"]
nudged = [b for b in os.environ.get("NUDGED_LIST", "").splitlines() if b.strip()]
now_epoch = int(time.time())

p = Path(state_file)
state = {}
if p.exists():
    try:
        state = json.loads(p.read_text() or "{}")
    except (json.JSONDecodeError, OSError):
        state = {}

for b in nudged:
    state[b] = now_epoch

try:
    p.write_text(json.dumps(state, sort_keys=True))
except OSError as e:
    print(f"state-file write failed: {e}", file=__import__("sys").stderr)
PY
fi

exit "$EXIT_CODE"
