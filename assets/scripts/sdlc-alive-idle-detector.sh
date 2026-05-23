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
DAILY_LIMIT="${SDLC_ALIVE_IDLE_DAILY_LIMIT:-5}"
ENABLED="${SDLC_ALIVE_IDLE_DETECTOR_ENABLED:-false}"

if [ "$ENABLED" != "true" ]; then
    exit 0
fi

mkdir -p "$STATE_DIR" 2>/dev/null || { echo "state dir create failed" >&2; exit 2; }
STATE_FILE="$STATE_DIR/alive-idle-nudges.json"

# Resolve events.jsonl path. Production sources, in order:
#   1. SDLC_ALIVE_IDLE_EVENTS_PATH explicit override (used by tests)
#   2. shared library `lib/sdlc-find-city-root.sh` with `.gc/events.jsonl`
#      as the walk-up marker — resolves $GC_CITY_ROOT, then walk-up
#      from $PWD, then `gc cities` first-row fallback
# Fail-closed: if we can't resolve, log and exit 2 — refusing to act is the
# right behavior, since without event-age we'd false-positive on every bead.
if [ -z "$EVENTS_PATH" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    CITY_ROOT=$(SDLC_FIND_CITY_GC="$GC_BIN" \
        bash "$SCRIPT_DIR/lib/sdlc-find-city-root.sh" .gc/events.jsonl \
        2>/dev/null) || CITY_ROOT=""
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
             DAILY_LIMIT="$DAILY_LIMIT" \
             python3 - <<'PY'
import json
import os
import re
import sys
import time
from pathlib import Path

beads_raw = json.loads(os.environ.get("BEADS_JSON", "[]") or "[]")
sessions_raw = json.loads(os.environ.get("SESSIONS_JSON", "[]") or "[]")

# Tolerate both shapes: bare array (`[]`) or object-with-list-key (`{sessions: [...]}`).
# Real gc CLI: `gc bd list --json` returns a bare array; `gc session list --json`
# returns `{filters, ok, sessions, summary}`. Tests historically passed bare
# arrays — production exposed the discrepancy on first smoke.
def _as_list(value, list_key):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        inner = value.get(list_key)
        if isinstance(inner, list):
            return inner
    return []

beads = _as_list(beads_raw, "beads")
sessions = _as_list(sessions_raw, "sessions")
state_file = os.environ.get("STATE_FILE", "")
events_path = os.environ.get("EVENTS_PATH", "")
threshold_minutes = int(os.environ.get("THRESHOLD_MINUTES", "20"))
cooldown_minutes = int(os.environ.get("COOLDOWN_MINUTES", "10"))
daily_limit = int(os.environ.get("DAILY_LIMIT", "5"))
now_epoch = int(time.time())

# Load state file. Two shapes supported for backward compatibility:
#   - Bare dict {bead_id: ts}    — original v1 format
#   - Envelope {by_bead: {...}, recent: [ts, ...]}   — v2 format (with rate-limit window)
state_raw = {}
if state_file:
    p = Path(state_file)
    if p.exists():
        try:
            state_raw = json.loads(p.read_text() or "{}")
        except (json.JSONDecodeError, OSError):
            state_raw = {}

if "by_bead" in state_raw or "recent" in state_raw:
    by_bead = state_raw.get("by_bead", {}) or {}
    recent = state_raw.get("recent", []) or []
else:
    # Old v1 format — treat top-level as by_bead, no rate-limit history.
    by_bead = state_raw
    recent = []

# Count nudges within the last 24h for the rate-limit check.
nudges_last_24h = sum(1 for t in recent if (now_epoch - int(t)) < 86400)

# Index sessions by EVERY identifier a bead's assignee field might reference.
# Real `gc session list --json` populates `id`, `session_name`, `name`, and
# sometimes `alias` at the top level. Pool worker beads carry the long-form
# session_name (e.g., `sdlc-discipline__worker-bl-d5vmvea`) as the assignee,
# not the short `id`. Indexing all keys lets the lookup work regardless of
# which form was written to the bead.
session_index = {}
for s in sessions:
    for key in ("id", "session_name", "name", "alias"):
        v = s.get(key)
        if v:
            session_index.setdefault(v, s)

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

# Walk beads. Track counters along the way for the post-run summary line.
counters = {
    "in_progress_total": len(beads),
    "with_assignee": 0,
    "stage1_pass": 0,
    "cooldown_skip": 0,
    "rate_limited": 0,
    "nudges_last_24h_at_start": nudges_last_24h,
}

for b in beads:
    bead_id = b.get("id")
    assignee = b.get("assignee") or b.get("metadata", {}).get("worker", {}).get("session_id")
    if not bead_id or not assignee:
        continue
    counters["with_assignee"] += 1
    # Stage 1 — event age.
    last_ts = latest_update.get(bead_id)
    if last_ts is not None:
        age_seconds = now_epoch - last_ts
        if age_seconds < threshold_minutes * 60:
            continue
    # else: no event found for this bead → treat as stale enough to proceed.
    counters["stage1_pass"] += 1
    # Cooldown — skip if we nudged this bead recently.
    last_nudge = by_bead.get(bead_id)
    if last_nudge is not None:
        nudge_age = now_epoch - int(last_nudge)
        if nudge_age < cooldown_minutes * 60:
            counters["cooldown_skip"] += 1
            continue
    # Rate limit — skip if we've hit the daily cap. Stops false-positive storms
    # from amplifying across many beads.
    if nudges_last_24h >= daily_limit:
        counters["rate_limited"] += 1
        continue
    # Resolve session. We need the canonical id-or-alias for `gc session peek`.
    s = session_index.get(assignee, {})
    # State of session — only proceed if explicitly "active". Defensive: any
    # session not classified active gets skipped (creating/suspended/closing).
    if s.get("state", "active") != "active":
        continue
    # Prefer alias > session_name > id for the peek/submit target — they all
    # resolve, but the alias is the most stable across session lifecycles.
    target = s.get("alias") or s.get("session_name") or s.get("id") or assignee
    print(f"CANDIDATE\t{bead_id}\t{target}")

# Emit summary line. Bash parses this and merges with stage-2 / nudge counters.
print(f"SUMMARY\t{json.dumps(counters)}")
PY
)

# Parse out the SUMMARY line (last line emitted by the Python heredoc) for
# upstream counters; keep CANDIDATE lines for the per-bead pane check.
SUMMARY_JSON=$(echo "$CANDIDATES" | awk -F'\t' '$1 == "SUMMARY" { print $2; exit }')
CANDIDATE_LINES=$(echo "$CANDIDATES" | awk -F'\t' '$1 == "CANDIDATE"')

EXIT_CODE=0
NUDGED_BEADS=()
STAGE2_PASS=0
NUDGED=0
SUBMIT_FAILED=0

while IFS=$'\t' read -r _TAG BEAD_ID TARGET; do
    [ -z "$BEAD_ID" ] && continue

    # Stage 2 — pane signature via `gc session peek`. The CLI handles tmux
    # socket discovery internally and works against the same surface that
    # `gc session submit` would write to. Capture more lines than the default
    # 50 to be sure we see the prompt and footer.
    PANE_CONTENT=$("$GC_BIN" session peek "$TARGET" --lines 100 2>/dev/null || true)

    # Reject if any live busy marker is present. 'esc to interrupt' is the
    # canonical signal — gascity's paneContainsBusyIndicator uses the same
    # string. 'Implementing…' / 'Crafting…' / 'Baking…' are belt-and-suspenders:
    # they appear with the ✽ / ✢ live-activity glyphs and would only show
    # during the model's working phase. Past-tense markers like 'Brewed for X',
    # 'Cooked for X' are NOT busy markers — they persist into the at-prompt
    # idle state showing the last turn's duration.
    #
    # Also reject if an interactive menu prompt is open ('Enter to select',
    # '↑/↓ to navigate', 'Esc to cancel' — these are mayor-style approval
    # dialogs where any user-message injection would be interpreted as
    # menu input or get swallowed by the dialog). Verified live during
    # validation against mayor on 2026-05-22.
    if echo "$PANE_CONTENT" | grep -qE 'esc to interrupt|Implementing…|Crafting…|Baking…'; then
        continue
    fi
    if echo "$PANE_CONTENT" | grep -qE 'Enter to select|to navigate|Esc to cancel'; then
        continue
    fi
    # Require the prompt glyph. The '❯' is the only universal at-prompt indicator.
    # ('new task?' was tried initially but only appears when context approaches
    # the per-turn cap and '/clear to save Xk tokens' shows — not universal.)
    if ! echo "$PANE_CONTENT" | grep -q '❯'; then
        continue
    fi

    STAGE2_PASS=$((STAGE2_PASS + 1))

    # Action — submit the synthetic user turn.
    if "$GC_BIN" session submit "$TARGET" "continue" --intent default >/dev/null 2>&1; then
        NUDGED_BEADS+=("$BEAD_ID")
        NUDGED=$((NUDGED + 1))
        # Audit-trail improvement (pack #105) — per-bead nudge counter
        # + last-nudge timestamp on the bead's metadata. Lets operators
        # grep `bd show <id> --json` for beads ever nudged by this
        # detector, and how many times. Read-then-increment-then-write;
        # best-effort, never blocks the detector loop.
        PREV_NUDGE_COUNT=$("$GC_BIN" bd show "$BEAD_ID" --json 2>/dev/null \
            | jq -r '.[0].metadata.alive_idle_nudge_count // "0"' 2>/dev/null)
        PREV_NUDGE_COUNT="${PREV_NUDGE_COUNT:-0}"
        [ "$PREV_NUDGE_COUNT" = "null" ] && PREV_NUDGE_COUNT=0
        NEW_NUDGE_COUNT=$((PREV_NUDGE_COUNT + 1))
        "$GC_BIN" bd update "$BEAD_ID" \
            --set-metadata "alive_idle_last_nudge_at=$(date -Iseconds)" \
            --set-metadata "alive_idle_nudge_count=$NEW_NUDGE_COUNT" \
            >/dev/null 2>&1 || true
        "$NOTIFY_BIN" --subject "alive-idle nudge fired" \
                      --body "Nudged stalled worker session $TARGET (bead $BEAD_ID)" \
                      >/dev/null 2>&1 || true
    else
        EXIT_CODE=1
        SUBMIT_FAILED=$((SUBMIT_FAILED + 1))
        "$NOTIFY_BIN" --subject "alive-idle nudge FAILED" \
                      --body "gc session submit failed for $TARGET (bead $BEAD_ID)" \
                      >/dev/null 2>&1 || true
    fi
done <<< "$CANDIDATE_LINES"

# Update state file with successful nudges. Migrates to the envelope format
# {by_bead: {...}, recent: [ts, ...]} on every write, prunes recent[] to last 25h.
if [ ${#NUDGED_BEADS[@]} -gt 0 ]; then
    NUDGED_LIST=$(printf '%s\n' "${NUDGED_BEADS[@]}")
    STATE_FILE="$STATE_FILE" NUDGED_LIST="$NUDGED_LIST" python3 - <<'PY'
import json, os, time
from pathlib import Path

state_file = os.environ["STATE_FILE"]
nudged = [b for b in os.environ.get("NUDGED_LIST", "").splitlines() if b.strip()]
now_epoch = int(time.time())

p = Path(state_file)
state_raw = {}
if p.exists():
    try:
        state_raw = json.loads(p.read_text() or "{}")
    except (json.JSONDecodeError, OSError):
        state_raw = {}

# Migrate old format if needed.
if "by_bead" in state_raw or "recent" in state_raw:
    by_bead = state_raw.get("by_bead", {}) or {}
    recent = state_raw.get("recent", []) or []
else:
    by_bead = state_raw
    recent = []

for b in nudged:
    by_bead[b] = now_epoch
    recent.append(now_epoch)

# Prune anything older than 25h from recent[] (keeps a 1h buffer above the 24h rate-limit window).
cutoff = now_epoch - (25 * 3600)
recent = [int(t) for t in recent if int(t) >= cutoff]

envelope = {"by_bead": by_bead, "recent": sorted(recent)}
try:
    p.write_text(json.dumps(envelope, sort_keys=True))
except OSError as e:
    print(f"state-file write failed: {e}", file=__import__("sys").stderr)
PY
fi

# Per-run log line for observability. Goes to stdout where the order tick
# captures it. Format mirrors the supervisor's structured fields so an
# operator can grep events.jsonl for `sdlc-alive-idle-detector:` to
# reconstruct what was decided each run.
RUN_TS=$(date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S%z)
if [ -n "$SUMMARY_JSON" ]; then
    IN_PROGRESS=$(echo "$SUMMARY_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('in_progress_total',0))")
    WITH_ASSIGNEE=$(echo "$SUMMARY_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('with_assignee',0))")
    STAGE1_PASS=$(echo "$SUMMARY_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('stage1_pass',0))")
    COOLDOWN_SKIP=$(echo "$SUMMARY_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('cooldown_skip',0))")
    RATE_LIMITED=$(echo "$SUMMARY_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('rate_limited',0))")
    NUDGES_24H=$(echo "$SUMMARY_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('nudges_last_24h_at_start',0))")
else
    IN_PROGRESS=0; WITH_ASSIGNEE=0; STAGE1_PASS=0; COOLDOWN_SKIP=0; RATE_LIMITED=0; NUDGES_24H=0
fi
echo "sdlc-alive-idle-detector: ran ts=$RUN_TS in_progress=$IN_PROGRESS with_assignee=$WITH_ASSIGNEE stage1_pass=$STAGE1_PASS cooldown_skip=$COOLDOWN_SKIP rate_limited=$RATE_LIMITED stage2_pass=$STAGE2_PASS nudged=$NUDGED submit_failed=$SUBMIT_FAILED nudges_24h_at_start=$NUDGES_24H daily_limit=$DAILY_LIMIT"

# If we hit the rate limit on this run, emit a single notify so the operator
# knows the detector is firing more often than its threshold permits.
if [ -n "$SUMMARY_JSON" ] && [ "$RATE_LIMITED" -gt 0 ]; then
    "$NOTIFY_BIN" --subject "alive-idle detector rate-limited" \
                  --body "Daily nudge limit ($DAILY_LIMIT) reached; $RATE_LIMITED bead(s) skipped this run. Investigate why so many stalls." \
                  >/dev/null 2>&1 || true
fi

exit "$EXIT_CODE"
