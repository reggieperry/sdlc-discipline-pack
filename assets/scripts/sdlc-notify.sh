#!/bin/bash
# SDLC notification helper (pack #44 sub-story 1).
#
# Pipes a one-line subject + multi-line body through local `msmtp`.
#
# Inputs:
#   --subject <text>            required; the email's Subject header value
#   stdin                       email body (multi-line ok)
#   SDLC_NOTIFY_RECIPIENT env   required; recipient address
#   SDLC_NOTIFY_MSMTP env       optional; path to the msmtp binary
#                               (default "msmtp", looked up via PATH).
#                               Tests use this to exercise the
#                               msmtp-absent fallback without disturbing
#                               the host's real install.
#
# msmtp itself carries the sender configuration (see ~/.msmtprc).
#
# Exit codes:
#   0   email sent, OR msmtp unavailable (notification gap logged to
#       stderr; must never fail a chain)
#   2   --subject missing
#   non-zero from msmtp on transport failure (caller decides how to react;
#       the finalizer wraps this call in `|| true`)

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
