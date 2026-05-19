#!/usr/bin/env bash
# sdlc-supervisor-start.sh — start the gc supervisor with the same PATH
# the operator's interactive shell sees, regardless of invocation context.
#
# Replaces `gc supervisor start` as the operator's recommended invocation.
# Source-of-truth for the supervisor's PATH lives here, in one place, so the
# resolution is identical whether:
#
#   - the operator invokes it from an interactive shell today, or
#   - a systemd unit invokes it via ExecStart= when the host moves to
#     systemd-managed supervisor (per EL-100 / future release-deployment
#     posture)
#
# Why this exists: by default, a non-interactive child process (script,
# systemd unit, supervisor-spawned chain phase) inherits a minimal PATH
# that omits user-installed binaries under `~/.local/bin`. On T7920 that
# means `uv`, `bd`, `gh` and other tools the chain reaches for aren't
# visible. Sourcing the user's .profile here yields the same PATH the
# operator's interactive shell carries; the supervisor inherits that PATH
# and so do all its children (pool agents, reviewer phases, etc.).
#
# Usage:
#   sdlc-supervisor-start.sh [--check] [extra args forwarded to gc]
#
# Modes:
#   --check       Print resolved PATH plus the resolved paths to gc / uv /
#                 bd / gh and exit 0. No supervisor is started. Use this to
#                 verify env resolution before bouncing the supervisor.
#
# Override the gc binary via SDLC_SUPERVISOR_GC (default: `gc`). Tests use
# this to substitute a stub binary.
#
# Exit codes:
#   0   supervisor exec'd, or --check completed
#   2   gc binary not on PATH after profile resolution
#   3   argument parse error

set -u

CHECK_ONLY=0
PASS_ARGS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --check)
            CHECK_ONLY=1
            shift
            ;;
        --help|-h)
            sed -n '2,38p' "$0"
            exit 0
            ;;
        *)
            PASS_ARGS+=("$1")
            shift
            ;;
    esac
done

# Source .profile to inherit operator's shell PATH. The Debian-default
# .profile sources .bashrc but .bashrc's standard `case $- in *i*) ;; *)
# return;; esac` guard early-exits for non-interactive shells — that's
# expected; .profile continues past the bashrc-source attempt and sets
# PATH itself. We swallow errors so a malformed .profile doesn't kill the
# supervisor startup.
if [ -f "$HOME/.profile" ]; then
    # shellcheck source=/dev/null
    . "$HOME/.profile" >/dev/null 2>&1 || true
fi

# Belt and suspenders: ensure ~/.local/bin is on PATH regardless of what
# .profile did (it might be missing, customized, or skipped on a host
# where the operator's setup looks different).
case ":${PATH:-}:" in
    *":$HOME/.local/bin:"*) ;;
    *) PATH="$HOME/.local/bin:${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}" ;;
esac
export PATH

if [ "$CHECK_ONLY" -eq 1 ]; then
    echo "sdlc-supervisor-start: env resolution check"
    echo "  PATH=$PATH"
    for tool in gc uv bd gh; do
        resolved="$(command -v "$tool" 2>/dev/null || echo '<not found>')"
        echo "  $tool: $resolved"
    done
    exit 0
fi

GC_BIN="${SDLC_SUPERVISOR_GC:-gc}"
if ! command -v "$GC_BIN" >/dev/null 2>&1; then
    echo "sdlc-supervisor-start: '$GC_BIN' not found on PATH after profile resolution" >&2
    echo "  PATH=$PATH" >&2
    echo "  HOME=$HOME" >&2
    echo "  hint: ensure gc is installed and either on PATH or set SDLC_SUPERVISOR_GC=/path/to/gc" >&2
    exit 2
fi

echo "sdlc-supervisor-start: PATH=$PATH" >&2
echo "sdlc-supervisor-start: exec $GC_BIN supervisor start ${PASS_ARGS[*]:-}" >&2
exec "$GC_BIN" supervisor start "${PASS_ARGS[@]}"
