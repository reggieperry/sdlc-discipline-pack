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
#   --type <kind>            notification kind; one of:
#                              pr_open_for_human (default) — PR parked
#                                for human review; subject reads
#                                `open for review`
#                              merged — PR auto-merged; subject reads
#                                `auto-merged`. Used by sub-story 3's
#                                SDLC_NOTIFY_ALL_CLOSES path.
#                            Unknown values exit 2.
#
# Subject format: `[<rig>] PR <#> <verb>: <story-title>`
#   where <verb> is "open for review" or "auto-merged" per --type.
# Body: PR URL, recommendation, signals, story ID, story title.

set -u

RIG=""
STORY_ID=""
PR_URL=""
RECOMMENDATION=""
SIGNALS=""
NOTIFY_TYPE="pr_open_for_human"

while [ $# -gt 0 ]; do
    case "$1" in
        --rig) RIG="$2"; shift 2 ;;
        --story-id) STORY_ID="$2"; shift 2 ;;
        --pr-url) PR_URL="$2"; shift 2 ;;
        --recommendation) RECOMMENDATION="$2"; shift 2 ;;
        --signals) SIGNALS="$2"; shift 2 ;;
        --type) NOTIFY_TYPE="$2"; shift 2 ;;
        *) shift ;;
    esac
done

case "$NOTIFY_TYPE" in
    pr_open_for_human) SUBJECT_VERB="open for review" ;;
    merged) SUBJECT_VERB="auto-merged" ;;
    *)
        echo "sdlc-finalizer-notify: unknown --type '${NOTIFY_TYPE}' (expected: pr_open_for_human, merged)" >&2
        exit 2
        ;;
esac

PR_NUMBER="${PR_URL##*/}"
TITLE=$(bd show "$STORY_ID" --json | jq -r '.[0].title')
SUBJECT="[${RIG}] PR ${PR_NUMBER} ${SUBJECT_VERB}: ${TITLE}"

HERE_DIR="$(cd "$(dirname "$0")" && pwd)"
"${HERE_DIR}/sdlc-notify.sh" --subject "$SUBJECT" <<EOF
PR: ${PR_URL}
Recommendation: ${RECOMMENDATION}
Signals fired: ${SIGNALS:-none}
Story: ${STORY_ID} — ${TITLE}
EOF
