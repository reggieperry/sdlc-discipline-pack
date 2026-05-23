#!/usr/bin/env bash
# sdlc-zombie-reconciler.sh — periodic story-spec drift reconciliation
#
# Fired on a 24h cooldown via orders/sdlc-zombie-reconciler.toml. For
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
# Idempotent: a spec whose status is already in {filed, in-flight,
# closed} is skipped — those are the canonical non-zombie states. A
# spec already at status: closed (e.g., from a previous reconciler
# pass that succeeded) is left alone.

set -uo pipefail

if [ "${SDLC_ZOMBIE_RECONCILER_ENABLED:-false}" != "true" ]; then
    exit 0
fi

CITY_ROOT="${GC_CITY_ROOT:-}"
if [ -z "$CITY_ROOT" ] || [ ! -d "$CITY_ROOT" ]; then
    echo "zombie-reconciler: GC_CITY_ROOT not set or missing; cannot enumerate rigs" >&2
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

RIGS_JSON=$(cd "$CITY_ROOT" && gc rig list --json 2>/dev/null || echo "")
if [ -z "$RIGS_JSON" ]; then
    echo "zombie-reconciler: gc rig list returned nothing from $CITY_ROOT" >&2
    exit 0
fi

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
        python3 - "$rig_root" "$rig" <<'PYEOF'
"""Per-rig zombie detector. Outputs one JSON line per HIGH-confidence
action; stdout is captured by the calling bash."""

import json
import re
import subprocess
import sys
from pathlib import Path

RIG_ROOT = Path(sys.argv[1])
RIG_NAME = sys.argv[2]

STORIES_DIR = RIG_ROOT / "stories"
TERMINAL_STATUSES = {"filed", "in-flight", "closed"}

# Hand-parse frontmatter rather than depend on PyYAML — the pack
# tries to stay stdlib-only.
def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    rest = text[3:]
    end = rest.find("\n---")
    if end == -1:
        return {}
    block = rest[:end]
    out: dict[str, str] = {}
    for line in block.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        # Quoted string values: strip outer quotes.
        if val.startswith(('"', "'")) and val.endswith(('"', "'")) and len(val) >= 2:
            val = val[1:-1]
        out[key] = val
    return out


def query_bd_for_story(story_id: str) -> dict | None:
    """Return the most recent closed bead with metadata.story_id == story_id,
    or None. Filters in jq via the subprocess; expects bd's --json output."""
    try:
        proc = subprocess.run(
            ["bd", "-C", str(RIG_ROOT), "list", "--status=closed", "--limit", "5000", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return None
        beads = json.loads(proc.stdout or "[]")
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return None
    for bead in beads:
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
        try:
            text = spec_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = parse_frontmatter(text)
        story_id = fm.get("story_id")
        status = fm.get("status", "")
        filed_as_bead = fm.get("filed_as_bead", "")
        if not story_id or status in TERMINAL_STATUSES:
            continue

        # Signal 1: bd-metadata match (most reliable).
        bead = query_bd_for_story(story_id)
        pr_url = ""
        pr_sha = ""
        signal = ""
        if bead is not None:
            meta = bead.get("metadata") or {}
            pr_url = meta.get("pr_url") or ""
            pr_sha = meta.get("final_merged_sha") or ""
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
    while IFS= read -r action_json; do
        [ -z "$action_json" ] && continue
        local story_id pr_url pr_sha
        story_id=$(echo "$action_json" | jq -r '.story_id // empty')
        pr_url=$(echo "$action_json" | jq -r '.pr_url // empty')
        pr_sha=$(echo "$action_json" | jq -r '.pr_sha // empty')
        [ -z "$story_id" ] && continue

        local cmd=("python3" "$STORIES_PY" "archive" "$story_id")
        [ -n "$pr_url" ] && cmd+=("--pr" "$pr_url")
        [ -n "$pr_sha" ] && cmd+=("--sha" "$pr_sha")

        if (cd "$rig_root" && "${cmd[@]}" >/dev/null 2>&1); then
            archived=$((archived + 1))
            echo "zombie-reconciler: rig=$rig archived $story_id (pr=$pr_url)" >&2
        else
            archive_failed=$((archive_failed + 1))
            echo "zombie-reconciler: rig=$rig FAILED to archive $story_id" >&2
        fi
    done <<< "$actions_out"

    if [ "$archived" -gt 0 ] || [ "$archive_failed" -gt 0 ]; then
        if [ -x "$NOTIFY" ]; then
            "$NOTIFY" \
                --subject "[zombie-reconciler] rig=$rig — $archived archived, $archive_failed failed" \
                --body "Zombie reconciler ran against rig $rig and archived $archived HIGH-confidence zombie spec(s); $archive_failed archive attempts failed. Inspect the rig's stories/_archive/ and pack-reconciler stderr for details." \
                2>/dev/null || true
        fi
    fi
}

FILTERED_RIGS=$(echo "$RIGS_JSON" | jq -c '.rigs[] | select(.hq == false and .suspended == false)' 2>/dev/null || true)
echo "$FILTERED_RIGS" | while IFS= read -r rig_json; do
    [ -z "$rig_json" ] && continue
    rig_name=$(echo "$rig_json" | jq -r '.name')
    rig_path=$(echo "$rig_json" | jq -r '.path')
    reconcile_rig "$rig_name" "$rig_path"
done

exit 0
