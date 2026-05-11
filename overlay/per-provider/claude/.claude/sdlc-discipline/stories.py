#!/usr/bin/env python3
"""SDLC story-graph bridge: translate stories/*.md to bd beads and back.

Stories are the design-time artifact (markdown with YAML frontmatter under
stories/ at the rig root). bd is the runtime substrate. This bridge tool
handles the boundary: parsing frontmatter, validating the graph, calling
bd create --graph for bulk filing, and writing assigned bead IDs back into
the story files.

Subcommands
-----------
validate   Cycle check, schema check, dep resolution, sensitive-files
           consistency, status-enum validity. Runs in pre-commit and CI.
           Exit 0 on clean, non-zero with details on issues.

file       Translate stories with status=ready into a bd graph-apply JSON
           plan, run `bd create --graph`, capture assigned bead IDs,
           write `filed_as_bead` back into each story's frontmatter, flip
           status from ready to filed.

ready      Wrapper over `bd ready` that joins bd's output back to
           story-file paths for human-readable display.

archive    For a closed bead, move its story file into stories/_archive/
           and append a closing note (PR URL, merged SHA, completion
           date).

graph      Wrapper over `bd graph --html --all`. Writes HTML to a temp
           path and prints the path for the caller to open.

Stdlib-only. YAML frontmatter is hand-parsed (simple shape; no anchors,
no flow style, no nested mappings beyond list-of-strings).

Run from any directory within the rig; walks up to find stories/.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VALID_STATUSES = {"draft", "ready", "filed", "in-flight", "merged", "closed"}
STORY_FILENAME_RE = re.compile(r"^([A-Z]+-\d+)-[a-z0-9-]+\.md$")
ARCHIVE_DIRNAME = "_archive"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def find_rig_root(start: Path | None = None) -> Path:
    """Walk up from start (or cwd) to find a directory containing stories/."""
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "stories").is_dir():
            return candidate
    raise SystemExit(
        f"stories: no stories/ directory found walking up from {here}. "
        "Run from inside a rig that has stories/."
    )


def find_sensitive_files_list(rig_root: Path) -> set[str] | None:
    """Read the rig's sensitive-files list, return as a set of paths, or None."""
    candidates = [
        rig_root / ".claude" / "rules" / "project" / "sensitive-files.md",
        rig_root / ".claude" / "rules" / "sensitive-files.md",
    ]
    for path in candidates:
        if path.is_file():
            paths: set[str] = set()
            for line in path.read_text().splitlines():
                line = line.strip()
                if line.startswith("- `") and line.endswith("`"):
                    paths.add(line[3:-1])
            return paths
    return None


def get_bd_prefix(rig_root: Path) -> str:
    """Read bd's issue-prefix from .beads/config.yaml; fall back to bd query."""
    config = rig_root / ".beads" / "config.yaml"
    if config.is_file():
        for line in config.read_text().splitlines():
            line = line.strip()
            if line.startswith("issue-prefix:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("GC_BEADS_PREFIX", "el")


# ---------------------------------------------------------------------------
# Frontmatter parsing (stdlib-only YAML subset)
# ---------------------------------------------------------------------------


def parse_frontmatter(story_path: Path) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body_str). Raises ValueError on parse failure."""
    text = story_path.read_text()
    if not text.startswith("---\n"):
        raise ValueError(f"{story_path}: missing opening --- frontmatter marker")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"{story_path}: missing closing --- frontmatter marker")
    fm_text = text[4:end]
    body = text[end + 5 :]
    return _parse_yaml_subset(fm_text, story_path), body


def _parse_yaml_subset(text: str, source: Path) -> dict[str, Any]:
    """Parse a minimal YAML subset: scalar key:value, list-of-strings.

    Supports:
        key: value             # scalar string
        key:                   # empty value (None)
        key: 0                 # bare integer
        key:                   # list of strings on following indented lines
          - one
          - two
        key: []                # explicit empty list

    Does NOT support: nested mappings, multi-line strings, anchors, flow.
    """
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("  - "):
            if current_list is None:
                raise ValueError(f"{source}: list item '{raw}' without a list-typed key above")
            current_list.append(raw[4:].strip())
            continue
        if raw.startswith((" ", "\t")):
            raise ValueError(f"{source}: unexpected indentation in '{raw}'")
        # Top-level key:value line.
        if ":" not in raw:
            raise ValueError(f"{source}: malformed line '{raw}'")
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        current_key = key
        current_list = None
        if value == "":
            # Could be empty scalar or list-following. Start a list buffer; if
            # the next non-list line lands first we'll convert to None.
            current_list = []
            result[key] = current_list
        elif value == "[]":
            result[key] = []
        else:
            stripped = value.strip("\"'")
            if stripped.isdigit() or (stripped.startswith("-") and stripped[1:].isdigit()):
                result[key] = int(stripped)
            else:
                result[key] = stripped
    # Convert empty-list buffers that received no items to None.
    for k, v in list(result.items()):
        if isinstance(v, list) and not v:
            # Distinguish "empty list because [] was written" vs "key: with no
            # following items, i.e., null". The simplest heuristic: assume the
            # latter; story authors write key: [] explicitly when they mean
            # empty list. Story specs in Elder follow this convention.
            result[k] = None
    return result


def serialize_frontmatter(fm: dict[str, Any]) -> str:
    """Round-trip a frontmatter dict back to YAML subset text."""
    lines: list[str] = []
    for key, value in fm.items():
        if value is None:
            lines.append(f"{key}:")
        elif isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {item}")
        elif isinstance(value, int):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


def update_frontmatter(story_path: Path, updates: dict[str, Any]) -> None:
    """Re-write story file with frontmatter updates applied in place."""
    fm, body = parse_frontmatter(story_path)
    fm.update(updates)
    new_text = f"---\n{serialize_frontmatter(fm)}\n---\n{body}"
    story_path.write_text(new_text)


# ---------------------------------------------------------------------------
# Story loading
# ---------------------------------------------------------------------------


def load_all_stories(stories_dir: Path) -> list[dict[str, Any]]:
    """Load every EL-NNN-*.md in stories/ (skips _archive/, _TEMPLATE, README)."""
    out: list[dict[str, Any]] = []
    for path in sorted(stories_dir.glob("*.md")):
        if path.name.startswith("_") or path.name == "README.md":
            continue
        if not STORY_FILENAME_RE.match(path.name):
            continue
        fm, _ = parse_frontmatter(path)
        fm["__path"] = path
        out.append(fm)
    return out


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    rig_root = find_rig_root()
    stories_dir = rig_root / "stories"
    stories = load_all_stories(stories_dir)
    sensitive_canonical = find_sensitive_files_list(rig_root)
    by_id = {s["story_id"]: s for s in stories}
    errors: list[str] = []

    # Schema + status
    for s in stories:
        path = s["__path"]
        for required in ("story_id", "title", "phase", "status"):
            if required not in s or s.get(required) in (None, ""):
                errors.append(f"{path.name}: missing required '{required}'")
        status = s.get("status")
        if status is not None and status not in VALID_STATUSES:
            errors.append(f"{path.name}: status '{status}' not in {sorted(VALID_STATUSES)}")
        # Naming
        sid = s.get("story_id")
        if sid and not path.name.startswith(f"{sid}-"):
            errors.append(f"{path.name}: filename does not start with story_id '{sid}-'")

    # Dep resolution
    for s in stories:
        deps = s.get("deps") or []
        for dep in deps:
            if dep not in by_id:
                errors.append(f"{s['__path'].name}: dep '{dep}' does not match any story_id")

    # Sensitive-files cross-check
    if sensitive_canonical is not None:
        for s in stories:
            for sf in s.get("sensitive_files") or []:
                if sf not in sensitive_canonical:
                    errors.append(
                        f"{s['__path'].name}: sensitive_files entry '{sf}' "
                        "not in .claude/rules/project/sensitive-files.md"
                    )

    # Cycle detection (DFS-based)
    cycle = detect_cycle(stories)
    if cycle:
        errors.append("dependency cycle: " + " -> ".join(cycle))

    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        print(f"\nstories validate: {len(errors)} error(s)", file=sys.stderr)
        return 1
    print(f"stories validate: {len(stories)} stories, schema + graph clean")
    return 0


def detect_cycle(stories: list[dict[str, Any]]) -> list[str] | None:
    """Return a cycle (list of IDs forming a loop) or None."""
    graph: dict[str, list[str]] = {s["story_id"]: list(s.get("deps") or []) for s in stories}
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(graph, WHITE)
    parent: dict[str, str | None] = dict.fromkeys(graph)

    def dfs(start: str) -> list[str] | None:
        stack: list[tuple[str, int]] = [(start, 0)]
        color[start] = GRAY
        while stack:
            node, idx = stack[-1]
            neighbors = graph.get(node, [])
            if idx >= len(neighbors):
                color[node] = BLACK
                stack.pop()
                continue
            nbr = neighbors[idx]
            stack[-1] = (node, idx + 1)
            if nbr not in color:
                continue
            if color[nbr] == GRAY:
                # Reconstruct cycle from parent chain.
                cycle = [nbr, node]
                cur = parent[node]
                while cur is not None and cur != nbr:
                    cycle.append(cur)
                    cur = parent[cur]
                cycle.append(nbr)
                return list(reversed(cycle))
            if color[nbr] == WHITE:
                color[nbr] = GRAY
                parent[nbr] = node
                stack.append((nbr, 0))
        return None

    for sid in graph:
        if color[sid] == WHITE:
            result = dfs(sid)
            if result:
                return result
    return None


# ---------------------------------------------------------------------------
# file
# ---------------------------------------------------------------------------


def cmd_file(args: argparse.Namespace) -> int:
    rig_root = find_rig_root()
    stories_dir = rig_root / "stories"
    stories = load_all_stories(stories_dir)
    by_id = {s["story_id"]: s for s in stories}

    # Selection
    if args.ids:
        selected = [by_id[i] for i in args.ids if i in by_id]
        missing = set(args.ids) - set(by_id)
        if missing:
            print(f"file: unknown story_ids: {sorted(missing)}", file=sys.stderr)
            return 1
    elif args.phase is not None:
        selected = [
            s for s in stories if s.get("phase") == args.phase and s.get("status") == "ready"
        ]
    else:
        selected = [s for s in stories if s.get("status") == "ready"]

    if not selected:
        print("file: nothing to file (no stories matched + status=ready)")
        return 0

    # Build plan
    prefix = get_bd_prefix(rig_root)
    plan = build_graph_plan(selected, stories, prefix)

    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0

    # Run bd create --graph
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="stories-plan-", delete=False
    ) as f:
        json.dump(plan, f, indent=2)
        plan_path = f.name
    try:
        result = subprocess.run(
            ["bd", "create", "--graph", plan_path],
            cwd=rig_root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"file: bd create --graph failed:\n{result.stderr}", file=sys.stderr)
            return result.returncode
        print(result.stdout)
        # Parse bd output for assigned IDs and write back
        assigned = parse_bd_create_output(result.stdout)
        for story in selected:
            sid = story["story_id"]
            if sid in assigned:
                update_frontmatter(
                    story["__path"],
                    {"filed_as_bead": assigned[sid], "status": "filed"},
                )
                print(
                    f"  {sid} -> {assigned[sid]} (updated {story['__path'].relative_to(rig_root)})"
                )
            else:
                print(f"  WARN: {sid} not found in bd create output", file=sys.stderr)
    finally:
        os.unlink(plan_path)
    return 0


def build_graph_plan(
    selected: list[dict[str, Any]],
    all_stories: list[dict[str, Any]],
    prefix: str,
) -> dict[str, Any]:
    """Construct the bd graph-apply JSON plan for the selected stories."""
    by_id = {s["story_id"]: s for s in all_stories}
    selected_ids = {s["story_id"] for s in selected}

    nodes = []
    for s in selected:
        body = s.get("__path").read_text()
        # Re-parse to get the body text (we drop __path from metadata).
        _, body_text = parse_frontmatter(s["__path"])
        nodes.append(
            {
                "key": s["story_id"],
                "title": s["title"],
                "type": "task",
                "priority": 2,
                "description": body_text.strip(),
                "labels": s.get("labels") or [],
                "metadata": {
                    "story_id": s["story_id"],
                    "build_item": "" if s.get("build_item") is None else str(s.get("build_item")),
                    "phase": "" if s.get("phase") is None else str(s.get("phase")),
                    "story_file": f"stories/{s['__path'].name}",
                },
            }
        )

    edges = []
    for s in selected:
        for dep in s.get("deps") or []:
            if dep in selected_ids or dep in by_id:
                # bd "blocks" edge convention: from_key is the BLOCKED bead,
                # to_key is the BLOCKER. A story with deps=[X] is BLOCKED BY X,
                # so the edge is {from_key: story, to_key: X}.
                edges.append({"from_key": s["story_id"], "to_key": dep, "type": "blocks"})

    return {
        "commit_message": f"File {len(selected)} stories: {', '.join(s['story_id'] for s in selected)}",
        "nodes": nodes,
        "edges": edges,
    }


def parse_bd_create_output(output: str) -> dict[str, str]:
    """Extract story_id -> bead_id from `bd create --graph` stdout."""
    assigned: dict[str, str] = {}
    # bd typically prints "Created <bead-id>: <title>" or similar.
    # We match conservatively on patterns mentioning both prefixes.
    for line in output.splitlines():
        m = re.search(r"\b(EL-\d+)\b.*?\b([a-z]+-[a-z0-9]+)\b", line)
        if m:
            assigned[m.group(1)] = m.group(2)
        m2 = re.search(r"\b([a-z]+-[a-z0-9]+)\b.*?\b(EL-\d+)\b", line)
        if m2 and m2.group(2) not in assigned:
            assigned[m2.group(2)] = m2.group(1)
    return assigned


# ---------------------------------------------------------------------------
# ready
# ---------------------------------------------------------------------------


def cmd_ready(args: argparse.Namespace) -> int:
    rig_root = find_rig_root()
    stories_dir = rig_root / "stories"
    stories = load_all_stories(stories_dir)
    by_bead = {s.get("filed_as_bead"): s for s in stories if s.get("filed_as_bead")}
    result = subprocess.run(
        ["bd", "ready", "--json"],
        cwd=rig_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Fall back to text output if --json isn't supported on this bd.
        result = subprocess.run(
            ["bd", "ready"],
            cwd=rig_root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"ready: bd ready failed:\n{result.stderr}", file=sys.stderr)
            return result.returncode
        print(result.stdout)
        return 0
    try:
        ready_items = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(result.stdout)
        return 0
    if not ready_items:
        print("ready: no stories ready to chain")
        return 0
    print(f"ready: {len(ready_items)} stories ready")
    for item in ready_items:
        bead_id = item.get("id") or item.get("key") or ""
        title = item.get("title") or ""
        story = by_bead.get(bead_id)
        story_id = story.get("story_id") if story else "?"
        path = story.get("__path").relative_to(rig_root) if story else "?"
        print(f"  {story_id:8s}  ({bead_id})  {title:50s}  {path}")
    return 0


# ---------------------------------------------------------------------------
# archive
# ---------------------------------------------------------------------------


def cmd_archive(args: argparse.Namespace) -> int:
    rig_root = find_rig_root()
    stories_dir = rig_root / "stories"
    archive_dir = stories_dir / ARCHIVE_DIRNAME
    archive_dir.mkdir(exist_ok=True)
    stories = load_all_stories(stories_dir)
    by_id = {s["story_id"]: s for s in stories}
    if args.story_id not in by_id:
        print(f"archive: unknown story_id '{args.story_id}'", file=sys.stderr)
        return 1
    story = by_id[args.story_id]
    src = story["__path"]
    dst = archive_dir / src.name

    closing_block = {
        "closed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "closed",
    }
    if args.pr:
        closing_block["merged_pr"] = args.pr
    if args.sha:
        closing_block["merged_sha"] = args.sha

    update_frontmatter(src, closing_block)
    shutil.move(str(src), str(dst))
    print(f"archive: moved {src.relative_to(rig_root)} -> {dst.relative_to(rig_root)}")
    return 0


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------


def cmd_graph(args: argparse.Namespace) -> int:
    rig_root = find_rig_root()
    out_path = args.output or os.path.join(tempfile.gettempdir(), "stories-graph.html")
    bd_args = ["bd", "graph", "--html", "--all"]
    if args.id:
        bd_args = ["bd", "graph", "--html", args.id]
    with open(out_path, "w") as f:
        result = subprocess.run(
            bd_args,
            cwd=rig_root,
            stdout=f,
            stderr=subprocess.PIPE,
            text=True,
        )
    if result.returncode != 0:
        print(f"graph: bd graph failed:\n{result.stderr}", file=sys.stderr)
        return result.returncode
    print(f"graph: wrote {out_path}")
    print(f"  open with: xdg-open {out_path}")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="stories",
        description="SDLC story-graph bridge: stories/*.md <-> bd beads.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_validate = sub.add_parser("validate", help="Validate the stories/ directory.")
    p_validate.set_defaults(func=cmd_validate)

    p_file = sub.add_parser("file", help="File ready stories into bd as beads.")
    p_file.add_argument(
        "ids", nargs="*", help="Specific story IDs to file (else all status=ready)."
    )
    p_file.add_argument("--phase", type=int, help="File all status=ready stories in this phase.")
    p_file.add_argument(
        "--dry-run", action="store_true", help="Print the graph plan JSON; do not call bd."
    )
    p_file.set_defaults(func=cmd_file)

    p_ready = sub.add_parser("ready", help="Show ready set joined with story-file paths.")
    p_ready.set_defaults(func=cmd_ready)

    p_archive = sub.add_parser("archive", help="Move a closed story's file into _archive/.")
    p_archive.add_argument("story_id", help="Story ID to archive (e.g., EL-014).")
    p_archive.add_argument("--pr", help="Merged PR URL to record in closing note.")
    p_archive.add_argument("--sha", help="Merged SHA to record in closing note.")
    p_archive.set_defaults(func=cmd_archive)

    p_graph = sub.add_parser("graph", help="Render bd dependency graph as HTML.")
    p_graph.add_argument("--id", help="Render only the specified bead's subgraph.")
    p_graph.add_argument("--output", help="Output HTML path (default: temp file).")
    p_graph.set_defaults(func=cmd_graph)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
