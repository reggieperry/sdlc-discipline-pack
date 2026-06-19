"""Guard: no bd-write site may use `--status=escalated` (issue #243).

`escalated` is not a valid bd status (valid: open / in_progress / blocked /
deferred / closed / pinned / hooked). bd rejects an update carrying it
ATOMICALLY — the co-located `--set-metadata` / `--notes` are silently
dropped too — so a phase that writes `bd update --status=escalated
--set-metadata requires_human_decision=true ...` lands a bead with NONE of
its park markers, indistinguishable from crash residue. The downstream
`requires_human_decision` mechanism (the #197 kickoff guard +
`sdlc-human-decision.sh`) never engages, and the bead becomes a
merged-but-blocked zombie once its PR merges (el-az1chd / EL-274 sat
blocked 17h after PR #738 merged).

This test fails the suite the moment any bd-write site reintroduces the
invalid status. It scans the whole pack — agent prompt templates, formula
TOMLs, and scripts — because every `bd update --status=escalated` is the
same bug regardless of which file emits it.

Distinct from a frontmatter-status check: this guards the imperative bd
CLI argument (`--status=escalated`), not story-spec frontmatter (which
`stories.py` VALID_STATUSES already guards).

stdlib-only (unittest + re + pathlib). Matches pack convention.

Run with::

    python3 -m unittest assets.scripts.tests.test_no_escalated_status -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[3]

# Match the bd-CLI status argument in any of its accepted spellings:
#   --status=escalated   --status escalated   --status="escalated"
# The reference in this test file's own docstring/source is excluded by
# skipping the test file itself (see _candidate_files).
_ESCALATED = re.compile(r"--status[=\s]+[\"']?escalated\b")

# Directories whose content is live pack behavior. _archive holds retired
# templates kept only for historical reference — they are never materialized
# into a worktree, so a stale `escalated` there cannot reach bd. The tests
# directory is excluded too: a test that documents the forbidden token in a
# docstring (this guard's whole subject) is not a bd-write site, so scanning
# it would flag the guard's own description. Excluded so the guard stays
# focused on reachable write sites.
_SCAN_DIRS = ("agents", "formulas", "assets/scripts", "commands", "overlay")
_EXCLUDE_PARTS = {"_archive", "__pycache__", ".git", "tests"}


def _candidate_files() -> list[Path]:
    files: list[Path] = []
    this_file = Path(__file__).resolve()
    for rel in _SCAN_DIRS:
        base = PACK_ROOT / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if any(part in _EXCLUDE_PARTS for part in path.parts):
                continue
            # Skip this test file — its docstring names the forbidden token.
            if path.resolve() == this_file:
                continue
            if path.suffix not in (".md", ".sh", ".py", ".toml"):
                continue
            files.append(path)
    return files


class NoEscalatedStatusTests(unittest.TestCase):
    def test_no_bd_write_uses_status_escalated(self) -> None:
        offenders: list[str] = []
        for path in _candidate_files():
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _ESCALATED.search(line):
                    rel = path.relative_to(PACK_ROOT)
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
        self.assertEqual(
            offenders,
            [],
            "bd rejects --status=escalated atomically (it is not a valid bd "
            "status); use the human-decision park instead "
            "(--status=blocked --assignee '' --set-metadata "
            "requires_human_decision=true ...). Offending sites:\n" + "\n".join(offenders),
        )

    def test_scan_actually_covers_the_known_write_sites(self) -> None:
        """Meta-guard: the scan must reach the files that carried the bug, so a
        future refactor that moves a write site out of _SCAN_DIRS can't make
        the primary test pass vacuously."""
        scanned = {p.relative_to(PACK_ROOT).as_posix() for p in _candidate_files()}
        for expected in (
            "agents/finalizer/prompt.template.md",
            "agents/worker/prompt.template.md",
            "formulas/mol-sdlc-work.toml",
            "formulas/mol-sdlc-plan.toml",
        ):
            self.assertIn(
                expected,
                scanned,
                f"scan must cover {expected} (a known former --status=escalated site)",
            )


if __name__ == "__main__":
    unittest.main()
