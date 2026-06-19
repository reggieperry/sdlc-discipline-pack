#!/usr/bin/env bash
# sdlc-stuck-alert.sh — pack #212. Email the operator when the Brooklyn
# pipeline is stranded awaiting a human. Two triggers, one digest:
#
#   1. bounce-exhausted PR: status=blocked + refresh_status=conflict —
#      the finalizer's at-cap branch (the rebase-bounce loop gave up).
#      Dedup via metadata.stuck_alerted_at. (Re-keyed from status=escalated
#      in issue #243 — bd rejects escalated atomically, so the park lands as
#      blocked.)
#   2. blocked-for-decision: status=blocked + a non-empty
#      metadata.human_decision_reason — a worker escalated a spec/architecture
#      call only the operator can make. Dedup via metadata.blocked_alerted_at.
#
# One email per rig per tick names every freshly-stranded bead; the body
# carries the actionable detail (bead id, story, the human_decision_reason
# or the collision files). Each bead alerts at most once — the dedup stamp
# is written after it enters the digest, so a later tick skips it. Both are
# stable terminal states (the worker/finalizer set them and exit), so there
# is no debounce problem: fire on first observation.
#
# Detection lives in lib/sdlc-stuck-alert-scan.py, shared with the
# --self-test canary so the self-test exercises the REAL detector (not a
# send-only check — the failure mode #212 explicitly guards against).
#
# Modes:
#   sdlc-stuck-alert.sh              normal per-rig scan + digest email
#   sdlc-stuck-alert.sh --self-test  run the detector against synthetic
#                                    stranded beads; if it fails to flag
#                                    them the detector is blind — notify the
#                                    operator and exit non-zero (fail loud).
#                                    Meant to run daily / on supervisor
#                                    restart. Order-firing liveness is the
#                                    existing sdlc-order-stall-detector's job.
#
# On a real notify transport failure (NOT the msmtp-absent fallback, which
# exits 0) the digest falls back to `gc mail send <rig>/witness` so an alert
# is never silently lost.
#
# Env knobs:
#   SDLC_STUCK_ALERT_ENABLED   default "false" (opt-in)
#   SDLC_NOTIFY_RECIPIENT      required for email (read by sdlc-notify.sh)
#
# Maps to Brooklyn-autonomy-map tripwire #3 (auto-recovery exhausted) and
# tripwire #5 (spec-blocker requiring amendment). Closes pack #212.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCAN="$SCRIPT_DIR/lib/sdlc-stuck-alert-scan.py"

if [ -z "${PACK_DIR:-}" ]; then
    PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
NOTIFY="$PACK_DIR/assets/scripts/sdlc-notify.sh"

# Opt-in gate — applies to both --self-test and the normal scan. A disabled
# feature runs neither (no point health-checking a detector nobody trusts).
if [ "${SDLC_STUCK_ALERT_ENABLED:-false}" != "true" ]; then
    exit 0
fi

# --- self-test canary -------------------------------------------------------
# Runs the real detector against a synthetic stranded-bead set. A detector
# that has gone blind (schema drift, logic regression) stops flagging them;
# we then alert the operator that the alerter itself is broken and exit 1.
if [ "${1:-}" = "--self-test" ]; then
    out=$(STUCK_ALERT_SELF_TEST=1 python3 "$SCAN" 2>/dev/null)
    if printf '%s' "$out" | grep -q "selftest-blocked" \
        && printf '%s' "$out" | grep -q "selftest-bounce"; then
        echo "stuck-alert self-test: OK — detector flagged both synthetic triggers"
        exit 0
    fi
    echo "stuck-alert self-test: FAILED — detector did not flag the synthetic stranded beads; it is blind." >&2
    printf 'scan output was:\n%s\n' "$out" >&2
    if [ -x "$NOTIFY" ]; then
        printf '%s\n' "The sdlc-stuck-alert detector failed its self-test: it did not flag the synthetic stranded beads, so it would NOT alert you about real stranded beads either. The alerter is blind — investigate sdlc-stuck-alert-scan.py before trusting the pipeline-stall alerts. Synthetic scan output: ${out:-<empty>}" \
            | "$NOTIFY" --subject "[stuck-alert] DETECTOR SELF-TEST FAILED — alerter is blind" 2>/dev/null || true
    fi
    exit 1
fi

# --- normal scan ------------------------------------------------------------
# Resolve the city root via the shared resolver — gascity retired
# GC_CITY_ROOT from the order-exec env (issue #204); it now emits GC_CITY.
CITY_ROOT=$(bash "$SCRIPT_DIR/lib/sdlc-find-city-root.sh" 2>/dev/null) || CITY_ROOT=""
if [ -z "$CITY_ROOT" ] || [ ! -d "$CITY_ROOT" ]; then
    echo "stuck-alert: cannot resolve city root (GC_CITY_ROOT='${GC_CITY_ROOT:-}' GC_CITY='${GC_CITY:-}' PWD='$PWD')" >&2
    exit 0
fi

RIG_LISTER="$SCRIPT_DIR/lib/sdlc-list-rigs.sh"

# Per-rig: the scan emits one JSON action line per freshly-stranded bead
# (already-alerted beads are filtered out in the scan, so re-runs are
# idempotent). bash dispatches: stamp the dedup marker, accumulate the
# digest, send one email.
reconcile_rig() {
    local rig="$1"
    local rig_root="$2"
    [ -d "$rig_root" ] || return

    local actions_out
    actions_out=$(cd "$rig_root" && python3 "$SCAN")
    [ -z "$actions_out" ] && return

    local now digest count blocked_n bounce_n
    now="$(date -Iseconds)"
    digest=""
    count=0
    blocked_n=0
    bounce_n=0

    while IFS= read -r action_json; do
        [ -z "$action_json" ] && continue
        local bead_id trigger stamp detail story
        bead_id=$(echo "$action_json" | jq -r '.bead_id')
        trigger=$(echo "$action_json" | jq -r '.trigger')
        stamp=$(echo "$action_json" | jq -r '.stamp')
        detail=$(echo "$action_json" | jq -r '.detail')
        story=$(echo "$action_json" | jq -r '.story')

        # Stamp the dedup marker first, so even if the email send fails the
        # bead is not re-alerted next tick (the failure path falls back to
        # gc mail below — a double-send is worse than a single stamp).
        (cd "$rig_root" && bd update "$bead_id" --set-metadata "${stamp}=${now}" >/dev/null 2>&1) || true

        if [ "$trigger" = "blocked" ]; then
            blocked_n=$((blocked_n + 1))
            digest="${digest}
- [blocked-for-decision] ${bead_id}${story:+ (${story})}
    decision needed: ${detail}"
        else
            bounce_n=$((bounce_n + 1))
            digest="${digest}
- [bounce-exhausted] ${bead_id}${story:+ (${story})}
    ${detail}"
        fi
        count=$((count + 1))
    done <<< "$actions_out"

    [ "$count" -eq 0 ] && return

    local subject body
    subject="[stuck-alert] rig=$rig — $count bead(s) stranded awaiting you ($blocked_n blocked-for-decision, $bounce_n bounce-exhausted)"
    body="The Brooklyn pipeline has $count bead(s) in rig '$rig' that cannot progress without you — they sit idle until you act:
${digest}

Each bead is listed once; a later tick will not re-alert (deduped via the alert stamp). For the blocked-for-decision beads see the gc-ops 'recover a blocked-for-decision bead' procedure; for the bounce-exhausted PRs, rebase + merge the named collision files."

    # Send the digest and capture the notify exit. sdlc-notify.sh exits 0
    # when msmtp is merely absent (logs to stderr); a non-zero exit means a
    # real transport failure — fall back to gc mail to the in-city witness
    # so a stranded-pipeline alert is never silently dropped.
    local notify_rc=0
    if [ -x "$NOTIFY" ]; then
        printf '%s\n' "$body" | "$NOTIFY" --subject "$subject"
        notify_rc=$?
    else
        notify_rc=127
    fi
    if [ "$notify_rc" -ne 0 ]; then
        echo "stuck-alert: sdlc-notify exit=$notify_rc; falling back to gc mail (rig=$rig count=$count)" >&2
        gc mail send "$rig/witness" -s "$subject" -m "$body" >/dev/null 2>&1 \
            || echo "stuck-alert: gc mail fallback ALSO failed — alert for rig=$rig is UNDELIVERED" >&2
    fi
}

while IFS=$'\t' read -r rig_name rig_path; do
    [ -z "$rig_name" ] && continue
    reconcile_rig "$rig_name" "$rig_path"
done < <(bash "$RIG_LISTER" "$CITY_ROOT")

exit 0
