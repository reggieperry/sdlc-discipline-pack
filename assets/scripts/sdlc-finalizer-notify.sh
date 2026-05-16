#!/bin/bash
# SDLC finalizer notification wrapper (pack #44 sub-story 2).
#
# Invoked by the finalizer template when a PR is parked at
# `final_state=pr_open_for_human`. Composes a human-readable email subject
# and body, then pipes through `sdlc-notify.sh`. Story title is fetched
# from bd at the call site so the finalizer prompt doesn't have to thread
# it manually.
#
# Walking-skeleton scope: cycle 6 — subject template. Body composition
# and the call to sdlc-notify ship in cycle 7.

set -u

RIG=""
STORY_ID=""
PR_URL=""
RECOMMENDATION=""
SIGNALS=""

while [ $# -gt 0 ]; do
    case "$1" in
        --rig) RIG="$2"; shift 2 ;;
        --story-id) STORY_ID="$2"; shift 2 ;;
        --pr-url) PR_URL="$2"; shift 2 ;;
        --recommendation) RECOMMENDATION="$2"; shift 2 ;;
        --signals) SIGNALS="$2"; shift 2 ;;
        *) shift ;;
    esac
done

PR_NUMBER="${PR_URL##*/}"
TITLE=$(bd show "$STORY_ID" --json | python3 -c 'import json, sys; print(json.loads(sys.stdin.read())[0]["title"])')

SUBJECT="[${RIG}] PR ${PR_NUMBER} open for review: ${TITLE}"

HERE_DIR="$(cd "$(dirname "$0")" && pwd)"
"${HERE_DIR}/sdlc-notify.sh" --subject "$SUBJECT" <<EOF
PR: ${PR_URL}
Recommendation: ${RECOMMENDATION}
Signals fired: ${SIGNALS:-none}
Story: ${STORY_ID} — ${TITLE}
EOF
