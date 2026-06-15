#!/usr/bin/env bash
# sdlc-slop-armc.sh — Arm C of the slop-reviewer bake-off: a deterministic
# orchestrator launched by gc as the slop-reviewer's provider command. It runs
# the extracted, tested orchestration scripts (steps 1-5,7-8) and makes ONE
# `claude -p` call for the generative 16-category rubric (step 6), replacing the
# open-ended Claude session with scripted control flow plus one model call.
#
# gc launches this with STORY_ID in the env (the routed bead) and the would-be
# claude argv as "$@" (ignored here — we drive the steps). GC_RIG and
# GC_SESSION_NAME are in the env for the scripts that read them.
#
# Exit codes (the gc provider-command contract, matching sdlc-claude-with-retry.sh):
#   0  — phase complete, bead handed off to the documenter.
#   75 — transient failure (EX_TEMPFAIL); the supervisor may retry.
#   1  — hard error.
#
# Shadow mode: the slop pass is annotate-only and non-blocking, so a failed or
# malformed `claude -p` does NOT wedge the chain — it emits a found:0 trailer
# that records the error and hands off, so the story still advances.
set -euo pipefail

SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
RUBRIC="$SCRIPTS/../slop-rubric.prompt.md"

# Passthrough: no STORY_ID means this is not a pool work-spawn (mayor / freelance);
# there is nothing for the slop-reviewer to do.
if [ -z "${STORY_ID:-}" ]; then
    exit 0
fi

# Step 1: claim the routed bead (mark it claimed so the reconciler does not
# double-spawn while we work). Best-effort: gc already routed it here.
gc bd update "$STORY_ID" --claim >/dev/null 2>&1 || true

# Step 2: record start metadata.
"$SCRIPTS/sdlc-slop-metadata.sh" "$STORY_ID"

# Step 3: check out the story's branch; capture BRANCH and TARGET without eval
# (branch names come from bead metadata and are untrusted).
if ! CHECKOUT_OUT="$("$SCRIPTS/sdlc-slop-checkout.sh" "$STORY_ID")"; then
    echo "sdlc-slop-armc.sh: checkout failed for $STORY_ID" >&2
    exit 75
fi
BRANCH="$(printf '%s\n' "$CHECKOUT_OUT" | sed -n 's/^BRANCH=//p')"
TARGET="$(printf '%s\n' "$CHECKOUT_OUT" | sed -n 's/^TARGET=//p')"
if [ -z "$BRANCH" ] || [ -z "$TARGET" ]; then
    echo "sdlc-slop-armc.sh: could not resolve BRANCH/TARGET for $STORY_ID" >&2
    exit 1
fi

# Step 4: skip the slop pass for a mechanically-trivial diff.
if "$SCRIPTS/sdlc-slop-skip-trivial.sh" --target "$TARGET"; then
    "$SCRIPTS/sdlc-slop-skip-emit.sh" "$STORY_ID" "$BRANCH"
    exit 0
fi

# Step 5: assemble the audit inputs for the generative call.
INPUTS_FILE="$(mktemp)"
TRAILER_FILE="$(mktemp)"
trap 'rm -f "$INPUTS_FILE" "$TRAILER_FILE"' EXIT
"$SCRIPTS/sdlc-slop-inputs.sh" "$STORY_ID" "$TARGET" > "$INPUTS_FILE"

# Step 6: the generative rubric — one claude -p call. The rubric prompt is the
# arg; the audit inputs ride in on stdin; the model is asked for the bare JSON
# slop_trailer. Validate it; on anything malformed, fall back (shadow mode).
RAW="$(claude -p "$(cat "$RUBRIC")" < "$INPUTS_FILE" 2>/dev/null || true)"
if printf '%s' "$RAW" | jq -e . >/dev/null 2>&1; then
    printf '%s' "$RAW" > "$TRAILER_FILE"
else
    printf '%s' '{"skipped": false, "model": "claude-opus-4-8", "found": 0, "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0}, "findings": [], "error": "slop pass produced no valid trailer (shadow-mode fallback)"}' > "$TRAILER_FILE"
fi
N="$(jq -r '.found // 0' "$TRAILER_FILE" 2>/dev/null || echo 0)"

# Steps 7-8: append the trailer to the review file, commit/push, route to the
# documenter, and drain.
"$SCRIPTS/sdlc-slop-finish.sh" "$STORY_ID" "$BRANCH" "$TRAILER_FILE" "$N"
exit 0
