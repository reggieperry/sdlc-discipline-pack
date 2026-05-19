#!/bin/bash
# sdlc-mode-classify.sh — classify a stalled chain session's failure mode.
#
# Reads a Claude Code session JSONL file and outputs one of:
#   mode_a       Mode A — API overload / 529 storm. Claude API returned
#                repeated 529 / overloaded responses; the session exhausted
#                its retry loop or the wrapper gave up.
#   mode_b       Mode B — per-turn cap exhausted. The session hit Claude's
#                turn limit without reaching a natural exit; the worker's
#                drain-ack never fired.
#   uncertain    Neither signature dominates; manual log inspection needed.
#
# Why this exists: chain stalls present two distinct recovery shapes (per
# the `chain_failure_modes` operator memory). Mode A wants retry / wrapper
# escalation; Mode B wants commit-WIP + clear-assignee + kill-session +
# reload-supervisor. Misclassifying one as the other wastes operator time.
# The signatures are mechanical enough that a grep-based classifier is
# more reliable than reading the JSONL by eye at 11 PM.
#
# Usage:
#   sdlc-mode-classify.sh --session /path/to/session.jsonl
#   sdlc-mode-classify.sh --session - < some.jsonl    # read from stdin
#
# Exit codes:
#   0   classification emitted (mode_a / mode_b / uncertain)
#   2   --session path missing or unreadable
#   3   argument parse error

set -u

SESSION=""
while [ $# -gt 0 ]; do
    case "$1" in
        --session)
            SESSION="$2"
            shift 2
            ;;
        --help|-h)
            sed -n '2,28p' "$0"
            exit 0
            ;;
        *)
            echo "sdlc-mode-classify: unknown arg '$1'" >&2
            exit 3
            ;;
    esac
done

if [ -z "$SESSION" ]; then
    echo "sdlc-mode-classify: --session <path> is required" >&2
    exit 3
fi

# Read input — file path or "-" for stdin.
if [ "$SESSION" = "-" ]; then
    INPUT=$(cat)
elif [ -r "$SESSION" ]; then
    INPUT=$(cat "$SESSION")
else
    echo "sdlc-mode-classify: cannot read '$SESSION'" >&2
    exit 2
fi

# Mode A signature: 529 status from Claude API, or "Overloaded" / "overloaded_error"
# in an API error response. The wrapper logs these as it retries.
MODE_A_HITS=$(printf '%s\n' "$INPUT" | grep -cE '"status":\s*529|overloaded_error|Overloaded|"type":\s*"overloaded"' || true)

# Mode B signature: per-turn cap exhausted. Claude Code marks this with
# "max_turns" / "turn_limit" / "stop_reason":"end_turn" near the tail without
# a natural completion, or the wrapper logs "per-turn cap exhausted".
MODE_B_HITS=$(printf '%s\n' "$INPUT" | grep -cE 'max_turns|per-turn cap|turn[_-]?limit|"stop_reason":\s*"max_turns"' || true)

# Tiebreak: if the session ends mid-tool-call (last line is a tool_use without
# a tool_result), that's a strong Mode B signal. If the session ends with an
# explicit error response, that's a strong Mode A signal.
TAIL=$(printf '%s\n' "$INPUT" | tail -3)
TAIL_ERROR=$(printf '%s\n' "$TAIL" | grep -cE '"type":\s*"error"|"error":\s*{' || true)
TAIL_TOOL_USE=$(printf '%s\n' "$TAIL" | grep -cE '"type":\s*"tool_use"' || true)

# Decision rules. Thresholds are conservative; uncertain is the safe default
# because misclassification wastes recovery time.
if [ "$MODE_A_HITS" -ge 3 ] || { [ "$TAIL_ERROR" -ge 1 ] && [ "$MODE_A_HITS" -ge 1 ]; }; then
    VERDICT="mode_a"
    REASON="$MODE_A_HITS Mode-A signatures (529 / overloaded); tail error frames=$TAIL_ERROR"
elif [ "$MODE_B_HITS" -ge 1 ] || [ "$TAIL_TOOL_USE" -ge 1 ]; then
    VERDICT="mode_b"
    REASON="$MODE_B_HITS Mode-B signatures (turn-cap); tail tool_use frames=$TAIL_TOOL_USE"
else
    VERDICT="uncertain"
    REASON="$MODE_A_HITS Mode-A hits, $MODE_B_HITS Mode-B hits, tail error=$TAIL_ERROR tool_use=$TAIL_TOOL_USE"
fi

printf '%s\n' "$VERDICT"
printf '  reason: %s\n' "$REASON" >&2
