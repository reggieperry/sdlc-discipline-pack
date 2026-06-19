#!/usr/bin/env bash
# sdlc-zombie-reconciler.sh — periodic story-spec drift reconciliation
#
# Fired on a 5m cooldown via orders/sdlc-zombie-reconciler.toml. For
# each registered non-HQ rig, walks stories/EL-*.md against merged PR
# + bead state; HIGH-confidence zombies (specs whose work shipped on
# main but whose frontmatter says otherwise) are auto-archived via
# stories.py archive.
#
# HIGH-confidence detection (two signals, OR-combined):
#
#   1. A closed bead exists with metadata.story_id == spec.story_id
#      and final_state in {merged, branch_ready_no_pr}. The PR URL +
#      SHA come from the bead's final_merged_at / final_merged_sha
#      (sweeper-reconciled v2.22.0) or from metadata.pr_url.
#
#   2. A merged PR exists whose branch is feature/<filed_as_bead> when
#      the spec's filed_as_bead is non-empty, OR whose title contains
#      the literal story_id at the start (e.g., "EL-134:" or
#      "EL-134 — ...").
#
# v1 ships HIGH-only; MEDIUM/LOW confidence handling (fuzzy title
# match, multi-PR ambiguity) is deferred to v1.1. Fail-open on weak
# signals — better to leave a zombie alive than to archive the wrong
# spec.
#
# Feature gate: SDLC_ZOMBIE_RECONCILER_ENABLED (default "false"). When
# unset or "false", exits at the top without scanning.
#
# Idempotent: a spec whose status is in {in-flight, closed} is skipped —
# those are the canonical non-zombie states. status=filed is processed
# through the detection ladder (pack #170): if a merged-PR signal hits,
# the spec is archived and the bead's final_state is advanced via
# `bd update --set-metadata final_state=merged`; if no signal hits, the
# spec is left alone (still conservative on weak signal). A spec already
# at status: closed (e.g., from a previous reconciler pass that
# succeeded) is left alone.

set -uo pipefail

if [ "${SDLC_ZOMBIE_RECONCILER_ENABLED:-false}" != "true" ]; then
    exit 0
fi

# Resolve the city root via the shared resolver — gascity retired
# GC_CITY_ROOT from the order-exec env (issue #204); it now emits GC_CITY.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CITY_ROOT=$(bash "$SCRIPT_DIR/lib/sdlc-find-city-root.sh" 2>/dev/null) || CITY_ROOT=""
if [ -z "$CITY_ROOT" ] || [ ! -d "$CITY_ROOT" ]; then
    echo "zombie-reconciler: cannot resolve city root (GC_CITY_ROOT='${GC_CITY_ROOT:-}' GC_CITY='${GC_CITY:-}' PWD='$PWD'); cannot enumerate rigs" >&2
    exit 0
fi

# PACK_DIR is set by gascity when invoking the order's exec. Fall
# back to walking up from the script's own location for direct
# invocations (tests, manual runs).
if [ -z "${PACK_DIR:-}" ]; then
    PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

NOTIFY="$PACK_DIR/assets/scripts/sdlc-notify.sh"
STORIES_PY="$PACK_DIR/overlay/per-provider/claude/.claude/sdlc-discipline/stories.py"

if [ ! -f "$STORIES_PY" ]; then
    echo "zombie-reconciler: stories.py bridge not found at $STORIES_PY" >&2
    exit 0
fi

# Locate the shared rig-enumeration library relative to this script.
# Tests override PACK_DIR to point at a fake pack root; the library
# always ships next to this script so prefer the script-relative path.
RIG_LISTER="$SCRIPT_DIR/lib/sdlc-list-rigs.sh"

# Per-rig: invoke a python heredoc that walks stories/, classifies,
# and emits one JSON action per HIGH-confidence zombie. Bash dispatches
# each action; bash also handles the notification call.
reconcile_rig() {
    local rig="$1"
    local rig_root="$2"

    if [ ! -d "$rig_root/stories" ]; then
        return
    fi

    local actions_out
    actions_out=$(cd "$rig_root" && SDLC_RIG_ROOT="$rig_root" SDLC_RIG_NAME="$rig" \
        STORIES_PY_DIR="$(dirname "$STORIES_PY")" \
        python3 - "$rig_root" "$rig" <<'PYEOF'
"""Per-rig zombie detector. Outputs one JSON line per HIGH-confidence
action; stdout is captured by the calling bash."""

import json
import os
import subprocess
import sys
from pathlib import Path

RIG_ROOT = Path(sys.argv[1])
RIG_NAME = sys.argv[2]

STORIES_DIR = RIG_ROOT / "stories"
# pack #170: status=filed is NOT skipped — when a chain's finalizer fails
# to write back merged_pr after a human-merged PR, the spec is stuck at
# status=filed even though the PR is merged. The reconciler picks up the
# slack via the existing HIGH-confidence detection paths.
TERMINAL_STATUSES = {"in-flight", "closed"}

# v2.29.5: import the canonical frontmatter parser from stories.py rather
# than hand-rolling a divergent reimplementation. The two parsers used to
# differ in strictness (the inline accepted `---` without a trailing
# newline; the canonical requires `---\n`) and in the YAML subset they
# supported (the inline did flat key:value only; the canonical handles
# lists too). Story specs always satisfy the canonical's stricter shape
# because `stories.py validate` (CI gate) enforces it.
sys.path.insert(0, os.environ["STORIES_PY_DIR"])
from stories import parse_frontmatter as _canonical_parse_frontmatter  # noqa: E402


def parse_frontmatter(spec_path: Path) -> dict:
    """Read + parse a story spec's frontmatter. Returns {} on any failure.

    The reconciler's downstream code reads `.get()` on the result and
    skips specs whose required fields are missing, so returning an empty
    dict on parse failure preserves the original fail-open contract — a
    malformed spec is skipped, not a fatal error.
    """
    try:
        fm, _body = _canonical_parse_frontmatter(spec_path)
    except (ValueError, OSError, UnicodeDecodeError):
        return {}
    return fm


_closed_beads_cache: list[dict] | None = None


def _all_closed_beads() -> list[dict]:
    """The rig's closed beads, fetched ONCE per run and memoized (#210).

    Previously this query ran per-spec (~9s each on ~20k closed beads), so
    ~57 specs serialized to ~500s and blew the 5m order deadline. The closed
    set is identical across specs in one run, so one fetch serves them all.
    The limit is raised to 50000 so a large closed-bead table is not silently
    truncated (the old 5000 cap dropped any zombie sorting past position 5000).
    """
    global _closed_beads_cache
    if _closed_beads_cache is not None:
        return _closed_beads_cache
    try:
        proc = subprocess.run(
            ["bd", "-C", str(RIG_ROOT), "list", "--status=closed", "--limit", "50000", "--json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        _closed_beads_cache = (
            json.loads(proc.stdout or "[]") if proc.returncode == 0 else []
        )
    except (subprocess.SubprocessError, json.JSONDecodeError):
        _closed_beads_cache = []
    return _closed_beads_cache


def query_bd_for_story(story_id: str) -> dict | None:
    """Return the most recent closed bead with metadata.story_id == story_id,
    or None. Reads from the per-run closed-bead cache (#210)."""
    for bead in _all_closed_beads():
        meta = bead.get("metadata") or {}
        if meta.get("story_id") == story_id:
            final_state = meta.get("final_state")
            if final_state in ("merged", "branch_ready_no_pr"):
                return bead
    return None


def query_merged_prs() -> list[dict]:
    """Return all merged PRs in the rig's gh repo. Cached per invocation."""
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--state", "merged", "--limit", "500", "--json",
             "number,title,headRefName,mergeCommit,url,mergedAt"],
            cwd=RIG_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            return []
        return json.loads(proc.stdout or "[]")
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return []


def find_high_confidence_pr(
    story_id: str, filed_as_bead: str, merged_prs: list[dict]
) -> dict | None:
    """Return the first merged PR that matches a HIGH-confidence signal.

    Signal 1: branch name == feature/<filed_as_bead> (only when filed_as_bead non-empty).
    Signal 2: title starts with "<story_id>:" or "<story_id> ".
    """
    if filed_as_bead:
        target_branch = f"feature/{filed_as_bead}"
        for pr in merged_prs:
            if pr.get("headRefName") == target_branch:
                return pr
    title_prefixes = (f"{story_id}:", f"{story_id} ")
    for pr in merged_prs:
        title = pr.get("title") or ""
        if title.startswith(title_prefixes):
            return pr
    return None


def main() -> None:
    if not STORIES_DIR.is_dir():
        return
    spec_files = sorted(STORIES_DIR.glob("EL-*.md"))
    if not spec_files:
        return

    merged_prs: list[dict] | None = None  # lazy

    for spec_path in spec_files:
        fm = parse_frontmatter(spec_path)
        # Empty `fm` covers two cases the wrapper conflates: parse failure
        # (ValueError / OSError / UnicodeDecodeError → {}) and legitimately
        # empty frontmatter (`---\n---\n`). Both are skipped — a spec with
        # no `story_id` field can't be reconciled either way, so downstream
        # `.get()` would skip it regardless.
        if not fm:
            continue
        story_id = fm.get("story_id")
        status = fm.get("status", "")
        filed_as_bead = fm.get("filed_as_bead", "")
        if not story_id or status in TERMINAL_STATUSES:
            continue

        # Signal 1: bd-metadata match (most reliable).
        bead = query_bd_for_story(story_id)
        pr_url = ""
        pr_sha = ""
        bead_id = ""
        signal = ""
        if bead is not None:
            meta = bead.get("metadata") or {}
            # #210: keep the PR link when a bead carries merged_pr but no
            # pr_url (e.g. a manually reconciled bead).
            pr_url = meta.get("pr_url") or (
                f"#{meta['merged_pr']}" if meta.get("merged_pr") else ""
            )
            pr_sha = meta.get("final_merged_sha") or ""
            bead_id = bead.get("id") or ""
            signal = "bead-metadata"

        if not signal:
            # Signal 2/3: PR title-prefix or branch-name match.
            if merged_prs is None:
                merged_prs = query_merged_prs()
            pr = find_high_confidence_pr(story_id, filed_as_bead, merged_prs)
            if pr is not None:
                pr_url = pr.get("url") or ""
                merge_commit = pr.get("mergeCommit") or {}
                pr_sha = (merge_commit.get("oid") if isinstance(merge_commit, dict) else "") or ""
                # pack #170: for Signal 2/3, the bead-id is the spec's
                # filed_as_bead — the bash wrapper needs it to advance the
                # bead's final_state to "merged" after archiving the spec.
                bead_id = filed_as_bead
                signal = "pr-title-or-branch"

        if not signal:
            continue

        action = {
            "rig": RIG_NAME,
            "story_id": story_id,
            "spec_path": str(spec_path.relative_to(RIG_ROOT)),
            "signal": signal,
            "pr_url": pr_url,
            "pr_sha": pr_sha,
            "bead_id": bead_id,
        }
        print(json.dumps(action))


main()
PYEOF
        )

    if [ -z "$actions_out" ]; then
        return
    fi

    local archived=0
    local archive_failed=0
    local archived_ids=()
    while IFS= read -r action_json; do
        [ -z "$action_json" ] && continue
        local story_id pr_url pr_sha bead_id
        story_id=$(echo "$action_json" | jq -r '.story_id // empty')
        pr_url=$(echo "$action_json" | jq -r '.pr_url // empty')
        pr_sha=$(echo "$action_json" | jq -r '.pr_sha // empty')
        bead_id=$(echo "$action_json" | jq -r '.bead_id // empty')
        [ -z "$story_id" ] && continue

        local cmd=("python3" "$STORIES_PY" "archive" "$story_id")
        [ -n "$pr_url" ] && cmd+=("--pr" "$pr_url")
        [ -n "$pr_sha" ] && cmd+=("--sha" "$pr_sha")

        if (cd "$rig_root" && "${cmd[@]}" >/dev/null 2>&1); then
            archived=$((archived + 1))
            archived_ids+=("$story_id")
            echo "zombie-reconciler: rig=$rig archived $story_id (pr=$pr_url)" >&2
            # pack #170: advance the predecessor bead's final_state to
            # "merged" so the cross-batch dep watcher (v2.32.0) clears
            # downstream defers on its next tick. Idempotent at the bd
            # layer — calling with the same value is a no-op. Best-effort:
            # a failure here does not roll back the spec archive, which is
            # the load-bearing operation.
            if [ -n "$bead_id" ]; then
                bd -C "$rig_root" update "$bead_id" --set-metadata final_state=merged \
                    >/dev/null 2>&1 || \
                    echo "zombie-reconciler: rig=$rig bd update $bead_id final_state=merged FAILED (non-fatal)" >&2

                # issue #243: close the merged-but-blocked zombie. A bead whose
                # PR merged but whose status is still `blocked` is unambiguously
                # done — it was parked (requires_human_decision) and the merge IS
                # the human decision, but nothing flips it to closed, so it gates
                # its downstream deps indefinitely (el-az1chd / EL-274 sat blocked
                # 17h after PR #738 merged). Close it ONLY when the live status is
                # `blocked`: an `in_progress` bead is an active re-walk and an
                # `open` bead may be queued for re-walk — closing either would
                # interrupt live work. Guard on `blocked` alone (NOT on
                # requires_human_decision): the headline zombie carries
                # requires_human_decision=true, so requiring it unset would skip
                # the very bead this closes. Best-effort — a failure here does not
                # roll back the archive.
                local cur_status
                cur_status=$(bd -C "$rig_root" show "$bead_id" --json 2>/dev/null \
                    | jq -r '.[0].status // empty' 2>/dev/null || echo "")
                if [ "$cur_status" = "blocked" ]; then
                    if bd -C "$rig_root" update "$bead_id" --status=closed >/dev/null 2>&1; then
                        echo "zombie-reconciler: rig=$rig closed merged-but-blocked zombie $bead_id (pr=$pr_url)" >&2
                    else
                        echo "zombie-reconciler: rig=$rig bd update $bead_id --status=closed FAILED (non-fatal)" >&2
                    fi
                fi
            fi
        else
            archive_failed=$((archive_failed + 1))
            echo "zombie-reconciler: rig=$rig FAILED to archive $story_id" >&2
        fi
    done <<< "$actions_out"

    # pack #219: commit + push the archive moves. stories.py archive moves the
    # spec active -> _archive on disk but does NOT git-commit, so without this
    # the rig is left dirty and the archive never reaches origin (every later
    # run re-detects siblings and the rig accumulates uncommitted renames).
    # Stage ONLY the specs we archived (never `git add -A` — the rig may carry
    # unrelated uncommitted state). Commit only on main; push is best-effort.
    if [ "$archived" -gt 0 ]; then
        local branch
        branch=$(git -C "$rig_root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
        if [ "$branch" != "main" ]; then
            echo "zombie-reconciler: rig=$rig on branch '$branch' (not main); leaving $archived archive move(s) uncommitted for the operator" >&2
            # pack #223: notify loudly rather than only logging to stderr — a rig
            # root left off the default branch breaks `git pull` and silently
            # strands archives until the operator restores it to the default branch.
            if [ -x "$NOTIFY" ]; then
                "$NOTIFY" \
                    --subject "[zombie-reconciler] rig=$rig is off the default branch" \
                    --body "The rig $rig checkout is on '$branch' (not the default branch), so $archived archive move(s) were left uncommitted. A pull/reconcile will fail until the rig root is restored to the default branch (e.g. git checkout main). See pack #223." \
                    2>/dev/null || true
            fi
        else
            local sid
            for sid in "${archived_ids[@]}"; do
                git -C "$rig_root" add -A -- "stories/${sid}-*.md" "stories/_archive/${sid}-*.md" 2>/dev/null || true
            done
            if git -C "$rig_root" commit -q -m "docs(stories): zombie-reconciler archived ${archived_ids[*]}" 2>/dev/null; then
                if ! git -C "$rig_root" push -q origin main 2>/dev/null; then
                    echo "zombie-reconciler: rig=$rig archive committed locally but PUSH FAILED (non-fatal)" >&2
                fi
            else
                echo "zombie-reconciler: rig=$rig git commit of archive moves FAILED (non-fatal)" >&2
            fi
        fi
    fi

    if [ "$archived" -gt 0 ] || [ "$archive_failed" -gt 0 ]; then
        if [ -x "$NOTIFY" ]; then
            "$NOTIFY" \
                --subject "[zombie-reconciler] rig=$rig — $archived archived, $archive_failed failed" \
                --body "Zombie reconciler ran against rig $rig and archived $archived HIGH-confidence zombie spec(s); $archive_failed archive attempts failed. Inspect the rig's stories/_archive/ and pack-reconciler stderr for details." \
                2>/dev/null || true
        fi
    fi
}

while IFS=$'\t' read -r rig_name rig_path; do
    [ -z "$rig_name" ] && continue
    reconcile_rig "$rig_name" "$rig_path"
done < <(bash "$RIG_LISTER" "$CITY_ROOT")

exit 0
