#!/usr/bin/env bash
# sdlc-slop-claim.sh — claim a bead for this slop-reviewer session
# (G0 Arm-C: extracted from slop-reviewer prompt step 1, "How you receive work").
#
# Finds the first bead this session may claim, claims it, and echoes the
# claimed story/bead id to stdout (so the formula captures it). Two tiers,
# in order, mirroring the prompt's startup block and Gas City's default
# work query:
#
#   1. in_progress work already assigned to this session
#      (gc bd list --assignee="$GC_SESSION_NAME" --status=in_progress) —
#      crash recovery; a bead already claimed by this session.
#   2. ready, unassigned work routed to this template
#      (gc bd list --status=open --assignee="" \
#         --metadata-field gc.routed_to=<rig>/sdlc-discipline.slop-reviewer).
#
# The first bead found is claimed with `gc bd update <bead-id> --claim`
# and its id echoed on stdout.
#
# Usage:
#   sdlc-slop-claim.sh
#
# Environment (provided by the formula):
#   GC_SESSION_NAME  required — this session's name (scopes the assignee query)
#   GC_RIG           rig name; defaults to "unknown" (scopes the routed-to query)
#
# Exit behavior (fail-closed):
#   0  — a bead was found, claimed, and its id echoed to stdout.
#   2  — GC_SESSION_NAME is missing/empty (cannot scope the query).
#   1  — no claimable work found; nothing echoed (the caller drains and exits).
set -euo pipefail

PHASE="slop-reviewer"

SESSION_NAME="${GC_SESSION_NAME:-}"
if [ -z "$SESSION_NAME" ]; then
    echo "sdlc-slop-claim.sh: missing GC_SESSION_NAME" >&2
    exit 2
fi

RIG="${GC_RIG:-unknown}"
ROUTED_TO="${RIG}/sdlc-discipline.${PHASE}"

# Tier 1: in_progress work already assigned to this session (crash recovery).
BEAD_ID=$(gc bd list --assignee="$SESSION_NAME" --status=in_progress --json 2>/dev/null \
    | jq -r '.[0].id // empty')

# Tier 2: ready, unassigned work routed to this template.
if [ -z "$BEAD_ID" ]; then
    BEAD_ID=$(gc bd list --status=open --assignee="" \
        --metadata-field "gc.routed_to=$ROUTED_TO" --json 2>/dev/null \
        | jq -r '.[0].id // empty')
fi

if [ -z "$BEAD_ID" ]; then
    # No claimable work; the caller drains and exits cleanly.
    exit 1
fi

gc bd update "$BEAD_ID" --claim
echo "$BEAD_ID"
