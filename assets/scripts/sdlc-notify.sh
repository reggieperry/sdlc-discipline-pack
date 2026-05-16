#!/bin/bash
# SDLC notification helper (pack #44 sub-story 1).
#
# Pipes a one-line subject + multi-line body through local `msmtp`. The
# recipient is read from SDLC_NOTIFY_RECIPIENT (env var). msmtp itself
# carries the sender configuration (see ~/.msmtprc). When `msmtp` is not
# on PATH the helper logs to stderr and exits 0 — a missing notification
# substrate must never fail a chain.
#
# Walking-skeleton scope: cycle 1 only — happy path through msmtp.
# Recipient-from-env, graceful fallback, and arg-validation cycles
# follow.

set -u

SUBJECT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --subject)
            SUBJECT="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

if [ -z "$SUBJECT" ]; then
    echo "sdlc-notify: --subject <text> is required" >&2
    exit 2
fi

RECIPIENT="$SDLC_NOTIFY_RECIPIENT"

# SDLC_NOTIFY_MSMTP lets tests substitute a nonexistent path to exercise
# the absent-msmtp fallback without disturbing the host's real install.
# Default: "msmtp" (looked up via PATH).
MSMTP_BIN="${SDLC_NOTIFY_MSMTP:-msmtp}"

if ! command -v "$MSMTP_BIN" >/dev/null 2>&1; then
    # Fallback: msmtp absent. Log the would-have-sent notification to
    # stderr (captured by the supervisor's logs) and exit 0 so a missing
    # notification substrate never fails a chain.
    echo "sdlc-notify: msmtp not available; skipping notification (subject='$SUBJECT' recipient='$RECIPIENT')" >&2
    exit 0
fi

{
    printf 'Subject: %s\n\n' "$SUBJECT"
    cat
} | "$MSMTP_BIN" "$RECIPIENT"
