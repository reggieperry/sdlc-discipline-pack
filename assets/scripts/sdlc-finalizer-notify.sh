#!/bin/bash
# SDLC finalizer notification wrapper (pack #44 sub-story 2).
#
# Invoked by the finalizer template when a PR is parked at
# `final_state=pr_open_for_human`. Composes a human-readable email subject
# and body, then pipes through `sdlc-notify.sh`. Story title is fetched
# from bd at the call site so the finalizer prompt doesn't have to thread
# it manually.
#
# Inputs:
#   --rig <name>             rig name shown in the subject prefix
#   --story-id <bead-id>     bead ID; bd is queried for the title
#   --pr-url <url>           PR URL; the trailing path component is the
#                            PR number, included in the subject
#   --recommendation <tier>  reviewer recommendation tier
#                            (glance_merge / review_encouraged /
#                            human_required), included in the body
#   --signals <csv>          architectural signals fired, included in
#                            the body; empty becomes "none"
#
# Subject format: `[<rig>] PR <#> open for review: <story-title>`
# Body: PR URL, recommendation, signals, story ID, story title.

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
TITLE=$(bd show "$STORY_ID" --json | jq -r '.[0].title')

SUBJECT="[${RIG}] PR ${PR_NUMBER} open for review: ${TITLE}"

HERE_DIR="$(cd "$(dirname "$0")" && pwd)"
"${HERE_DIR}/sdlc-notify.sh" --subject "$SUBJECT" <<EOF
PR: ${PR_URL}
Recommendation: ${RECOMMENDATION}
Signals fired: ${SIGNALS:-none}
Story: ${STORY_ID} — ${TITLE}
EOF
