# SDLC exit-history append helper (pack #182).
#
# Shared by `sdlc-claude-with-retry.sh` (wrapper-side per-attempt history)
# and `sdlc-exhausted-bead-retry.sh` (watcher-side resling + give-up
# history). Both write to the same `<template>.exit_history` metadata
# field on the bead so the operator can read a single field to see the
# full retry lifecycle.
#
# Schema: `<template>.exit_history` is a pipe-delimited list of entries.
# Each entry is `<ISO-ts>~<kind>~<cause>`. Kinds in use:
#
#   retry            — wrapper attempted again with --resume (transient failure)
#   exhausted        — wrapper hit max-attempts; gave up to the watcher
#   watcher_resling  — watcher re-slung the bead after the backoff window
#   watcher_gave_up  — watcher hit the retry-count cap and stopped retrying
#
# The function is idempotent in the bd sense: re-running with the same
# args appends a duplicate entry rather than corrupting state, which is
# acceptable because the timestamp distinguishes them and the operator
# reads the history as a log, not a set.
#
# Callers must ensure `bd` is on PATH and that the cwd is the rig root
# (or that `bd` is invoked with --rig). The wrapper runs in a single-rig
# context; the watcher invokes via `(cd "$rig_root" && ...)` subshell.

# Append one entry to a bead's `<template>.exit_history` metadata field.
#
# Args:
#   $1 bead_id   — the bead's id
#   $2 template  — the SDLC template name (worker / tester / reviewer / ...)
#   $3 kind      — entry kind (retry / exhausted / watcher_resling / watcher_gave_up)
#   $4 cause     — the cause string (e.g. "api_529", "turn_cap", "exit_124")
#
# Side effects:
#   Reads the existing exit_history via `bd show`; writes the appended
#   value via `bd update`. Both failures are silently swallowed (best-
#   effort audit trail; never block the caller).
sdlc_append_exit_history() {
    local bead_id="$1"
    local template="$2"
    local kind="$3"
    local cause="$4"
    local ts new_entry prior new_history
    ts=$(date -Iseconds)
    new_entry="${ts}~${kind}~${cause}"
    prior=$(bd show "$bead_id" --json 2>/dev/null \
        | jq -r ".[0].metadata.\"${template}.exit_history\" // \"\"" 2>/dev/null)
    if [ -z "$prior" ] || [ "$prior" = "null" ]; then
        new_history="$new_entry"
    else
        new_history="${prior}|${new_entry}"
    fi
    bd update "$bead_id" --set-metadata "${template}.exit_history=${new_history}" \
        >/dev/null 2>&1 || true
}
