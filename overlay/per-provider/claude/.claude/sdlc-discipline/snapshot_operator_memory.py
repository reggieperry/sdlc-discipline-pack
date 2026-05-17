"""Snapshot operator-side memory entries for chain-agent context loading.

Reads the operator's Claude Code auto-memory directory at
`$HOME/.claude/projects/<project-key>/memory/`, walks every `*.md` file
under it (except the `MEMORY.md` index), parses each file's YAML
frontmatter, and writes a concatenated context file containing only the
entries whose `metadata.type` is in `{project, reference}`.

The output is consumed by the worker, reviewer, and documenter pool
agents at session start, supplying the operator's project context and
reference pointers alongside the rig's checked-in `CLAUDE.md` and
rules. `user` and `feedback` types are deliberately omitted — they
encode the operator's collaboration preferences with the human-facing
Claude Code session, not the codebase context the chain agents need.

Graceful degradation:

- Operator memory directory absent → write empty output, exit 0.
- Directory empty or no matching entries → write empty output, exit 0.
- Malformed frontmatter on an individual file → skip that file, log to
  stderr, continue with the remaining files.

The project-key convention follows Claude Code's auto-memory layout: a
rig at `/home/user/path/to/rig` keys its memory under
`-home-user-path-to-rig` (absolute path with `/` replaced by `-`).

CLI:

    python3 snapshot_operator_memory.py --output <path>
        [--cwd <dir>] [--home <dir>]

`--cwd` defaults to the current working directory; `--home` defaults to
`$HOME`. Both are injectable for tests. Output's parent directory is
created if absent.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TYPE = re.compile(r"^\s+type:\s*(\w+)\s*$", re.MULTILINE)
_NAME = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
_DESC = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)

_INCLUDED_TYPES = frozenset({"project", "reference"})


def project_key(cwd: Path) -> str:
    """Compute the Claude Code auto-memory project-key for a working directory.

    The convention replaces `/` with `-` on the absolute path; a rig at
    `/home/user/rig` keys under `-home-user-rig`. Symlinks resolved via
    `Path.resolve()` so the key matches whichever path Claude Code wrote
    when it initialized the memory directory.
    """
    return str(cwd.resolve()).replace("/", "-")


def memory_dir(cwd: Path, home: Path) -> Path:
    """Resolve the operator's memory directory for the given working dir."""
    return home / ".claude" / "projects" / project_key(cwd) / "memory"


def parse_frontmatter(text: str) -> dict[str, str]:
    """Extract `name`, `description`, and nested `metadata.type` from frontmatter.

    Returns a dict with keys `name`, `description`, `type` — missing keys
    map to the empty string. The parser handles the simple shape memory
    files use (scalar keys, no anchors, no flow style); anything more
    complex returns the empty defaults so the caller treats the file as
    unparseable and skips it.
    """
    m = _FRONTMATTER.match(text)
    if not m:
        return {"name": "", "description": "", "type": ""}
    block = m.group(1)
    return {
        "name": _match_group(_NAME, block),
        "description": _match_group(_DESC, block),
        "type": _match_group(_TYPE, block),
    }


def _match_group(pattern: re.Pattern[str], text: str) -> str:
    m = pattern.search(text)
    return m.group(1) if m else ""


def select_entries(directory: Path) -> list[tuple[dict[str, str], str]]:
    """Walk the directory, return a deterministic list of matching entries.

    Each element is `(frontmatter_dict, body_text)` where body is the
    file content with the YAML frontmatter stripped. Sort by filename so
    consecutive runs against an unchanged directory produce byte-identical
    output (load-bearing for caching downstream).
    """
    if not directory.is_dir():
        return []
    out: list[tuple[dict[str, str], str]] = []
    for path in sorted(directory.iterdir()):
        if path.name == "MEMORY.md" or path.suffix != ".md" or not path.is_file():
            continue
        try:
            text = path.read_text()
        except OSError as exc:
            print(f"[operator-memory] skip {path.name}: {exc}", file=sys.stderr)
            continue
        fm = parse_frontmatter(text)
        if fm["type"] not in _INCLUDED_TYPES:
            continue
        body = _strip_frontmatter(text)
        out.append((fm, body))
    return out


def _strip_frontmatter(text: str) -> str:
    m = _FRONTMATTER.match(text)
    return text[m.end() :] if m else text


def render_snapshot(entries: list[tuple[dict[str, str], str]]) -> str:
    """Concatenate selected entries with section headers per `name:` slug.

    Returns the empty string when entries is empty — the caller writes
    that empty string to the output file, and chain-agent prompts handle
    the empty case by no-op'ing the context-loading step.
    """
    if not entries:
        return ""
    lines: list[str] = [
        "# Operator-side project + reference memory",
        "",
        "Snapshot of the operator's memory entries with "
        "`metadata.type` in `{project, reference}`. Generated at chain "
        "kickoff. Read this for context the rig's checked-in files do "
        "not carry — recent project state, reference pointers to external "
        "systems, and decision history.",
        "",
    ]
    for fm, body in entries:
        slug = fm["name"] or "(unnamed)"
        lines.append(f"## {slug}")
        if fm["description"]:
            lines.append("")
            lines.append(f"*{fm['description']}*")
        lines.append("")
        lines.append(body.rstrip())
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Snapshot operator memory for chain agent context",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--output", type=Path, required=True, help="path to write the snapshot")
    parser.add_argument(
        "--cwd",
        type=Path,
        default=None,
        help="working directory for project-key resolution (default: current dir)",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=None,
        help="user home for memory-directory resolution (default: $HOME)",
    )
    args = parser.parse_args(argv)

    cwd = args.cwd or Path.cwd()
    home = args.home or Path.home()
    directory = memory_dir(cwd, home)
    entries = select_entries(directory)
    snapshot = render_snapshot(entries)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(snapshot)
    print(
        f"[operator-memory] snapshot: {len(entries)} entries from {directory} → {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
