"""Tests for the tech-debt autofix orchestrator module.

Covers issue-body parsing, slug rendering, story-id allocation (including
within-batch collision avoidance for dry-run), spec rendering, idempotency
marker detection, and the top-level `spawn` subcommand flow against a
fake `gh` runner.

stdlib-only (`unittest` + subprocess + tempfile + importlib). Mocks `gh`
via a fake runner callable passed into the public functions.

Run with:

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "overlay"
    / "per-provider"
    / "claude"
    / ".claude"
    / "sdlc-discipline"
    / "tech_debt_autofix.py"
)
assert MODULE_PATH.exists(), f"tech_debt_autofix.py not found at {MODULE_PATH}"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("tech_debt_autofix", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


autofix = _load_module()


def _fake_gh_factory(responses: list[subprocess.CompletedProcess[str]]) -> Any:
    calls: list[list[str]] = []
    iterator = iter(responses)

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        try:
            return next(iterator)
        except StopIteration as exc:
            raise AssertionError(f"unexpected extra gh call: {args}") from exc

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def _ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _err(stderr: str = "fail") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


def _well_formed_body() -> str:
    return (
        "## Tech-debt finding\n"
        "\n"
        "| Field | Value |\n"
        "| --- | --- |\n"
        "| Target | `core/coordinator.py` (lines 5-8) |\n"
        "| Severity | **med** |\n"
        "| Category | `docstring-vs-code` |\n"
        "\n"
        "## Suggested fix\n"
        "\n"
        "Tighten docstring or add flag-check in _walk_stages.\n"
        "\n"
        "## Source\n"
        "\n"
        "- Parent PR: https://github.com/org/repo/pull/200\n"
        "- Review file: `reviews/el-x.md`\n"
    )


def _issue(
    number: int,
    *,
    title: str = "[tech-debt] Docstring drift in coordinator",
    body: str | None = None,
    url: str = "https://github.com/org/repo/issues/1",
    comments: list[dict[str, Any]] | None = None,
    state: str = "OPEN",
    labels: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "body": body if body is not None else _well_formed_body(),
        "url": url,
        "comments": comments or [],
        "state": state,
        "labels": [{"name": n} for n in (labels or ["tech-debt:autofix-safe", "tech-debt"])],
    }


def _make_rig(tmp: Path, existing_stories: list[str] | None = None) -> Path:
    stories = tmp / "stories"
    stories.mkdir()
    for name in existing_stories or []:
        (stories / name).write_text("placeholder\n")
    return tmp


class ParseIssueBodyTests(unittest.TestCase):
    def test_well_formed_body_returns_all_fields(self) -> None:
        fields = autofix.parse_issue_body(_well_formed_body())
        self.assertIsNotNone(fields)
        assert fields is not None
        self.assertEqual(fields["target_path"], "core/coordinator.py")
        self.assertEqual(fields["target_lines"], "5-8")
        self.assertEqual(fields["severity"], "med")
        self.assertEqual(fields["category"], "docstring-vs-code")
        self.assertIn("Tighten docstring", fields["suggested_fix"])

    def test_missing_target_returns_none(self) -> None:
        body = _well_formed_body().replace("| Target | `core/coordinator.py` (lines 5-8) |", "")
        self.assertIsNone(autofix.parse_issue_body(body))

    def test_missing_suggested_fix_returns_none(self) -> None:
        body = (
            "## Tech-debt finding\n\n"
            "| Field | Value |\n"
            "| --- | --- |\n"
            "| Target | `x.py` (lines 1-2) |\n"
            "| Severity | **low** |\n"
            "| Category | `dead-code` |\n"
            "\n"
        )
        self.assertIsNone(autofix.parse_issue_body(body))

    def test_empty_body_returns_none(self) -> None:
        self.assertIsNone(autofix.parse_issue_body(""))


class SlugFromSummaryTests(unittest.TestCase):
    def test_basic_kebab_case(self) -> None:
        self.assertEqual(
            autofix.slug_from_summary("Docstring drift in coordinator"),
            "docstring-drift-in-coordinator",
        )

    def test_strips_punctuation(self) -> None:
        self.assertEqual(
            autofix.slug_from_summary("Fix: foo.bar() — doesn't match!"),
            "fix-foobar-doesnt-match",
        )

    def test_truncates_to_max(self) -> None:
        out = autofix.slug_from_summary("a" * 200, max_chars=10)
        self.assertEqual(out, "a" * 10)

    def test_empty_falls_back(self) -> None:
        self.assertEqual(autofix.slug_from_summary("!!!"), "tech-debt")


class StripTitlePrefixTests(unittest.TestCase):
    def test_strips_when_present(self) -> None:
        self.assertEqual(autofix.strip_title_prefix("[tech-debt] X"), "X")

    def test_passthrough_when_absent(self) -> None:
        self.assertEqual(autofix.strip_title_prefix("Plain title"), "Plain title")


class NextFreeStoryIdTests(unittest.TestCase):
    def test_empty_returns_001(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(autofix.next_free_story_id(Path(tmp)), "EL-001")

    def test_picks_max_plus_one(self) -> None:
        with TemporaryDirectory() as tmp:
            stories = Path(tmp)
            (stories / "EL-099-foo.md").write_text("x")
            (stories / "EL-100-bar.md").write_text("x")
            (stories / "EL-042-baz.md").write_text("x")
            self.assertEqual(autofix.next_free_story_id(stories), "EL-101")

    def test_ignores_other_prefixes(self) -> None:
        with TemporaryDirectory() as tmp:
            stories = Path(tmp)
            (stories / "VAL-099-foo.md").write_text("x")
            (stories / "EL-005-bar.md").write_text("x")
            self.assertEqual(autofix.next_free_story_id(stories), "EL-006")


class AllocateStoryIdTests(unittest.TestCase):
    def test_avoids_within_batch_collisions(self) -> None:
        with TemporaryDirectory() as tmp:
            stories = Path(tmp)
            (stories / "EL-050-foo.md").write_text("x")
            used: set[str] = set()
            a = autofix._allocate_story_id(stories, used)
            b = autofix._allocate_story_id(stories, used)
            c = autofix._allocate_story_id(stories, used)
            self.assertEqual([a, b, c], ["EL-051", "EL-052", "EL-053"])
            self.assertEqual(used, {"EL-051", "EL-052", "EL-053"})


class AlreadySpawnedTests(unittest.TestCase):
    def test_detects_marker(self) -> None:
        comments = [
            {"body": "random comment"},
            {"body": "Auto-spawned. <!-- tech-debt-autofix-spawned story=EL-099 -->"},
        ]
        self.assertTrue(autofix.already_spawned(comments))

    def test_no_marker_returns_false(self) -> None:
        self.assertFalse(autofix.already_spawned([{"body": "hi"}]))

    def test_empty_returns_false(self) -> None:
        self.assertFalse(autofix.already_spawned([]))


class RenderStorySpecTests(unittest.TestCase):
    def test_contains_key_fields(self) -> None:
        fields = autofix.parse_issue_body(_well_formed_body())
        assert fields is not None
        out = autofix.render_story_spec(
            story_id="EL-101",
            title="Docstring drift in coordinator",
            issue_number=283,
            issue_url="https://example/283",
            fields=fields,
        )
        self.assertIn("story_id: EL-101", out)
        self.assertIn("status: ready", out)
        self.assertIn("# EL-101 Docstring drift in coordinator", out)
        self.assertIn("#283", out)
        self.assertIn("core/coordinator.py", out)
        self.assertIn("lines 5-8", out)
        self.assertIn("Closes #283", out)
        self.assertIn("https://example/283", out)
        # No unresolved template gaps.
        self.assertNotIn("{", out)


class SpawnCommandTests(unittest.TestCase):
    def _args(
        self,
        rig_root: Path,
        *,
        dry_run: bool = False,
        issue: int | None = None,
        limit: int = 10,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            rig_root=rig_root,
            dry_run=dry_run,
            issue=issue,
            limit=limit,
            command="spawn",
        )

    def test_dry_run_prints_no_write_no_comment(self) -> None:
        with TemporaryDirectory() as tmp:
            rig = _make_rig(Path(tmp), existing_stories=["EL-100-foo.md"])
            issue = _issue(283)
            gh = _fake_gh_factory([_ok(json.dumps([issue]))])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = autofix.spawn_command(self._args(rig, dry_run=True), gh_runner=gh)
            self.assertEqual(rc, 0)
            self.assertIn("--- ", buf.getvalue())
            self.assertIn("EL-101", buf.getvalue())
            # gh was called once (list); no comment call.
            self.assertEqual(len(gh.calls), 1)
            self.assertEqual(gh.calls[0][0:2], ["issue", "list"])
            # No story files written.
            self.assertFalse(any((rig / "stories").glob("EL-101-*.md")))

    def test_write_mode_files_story_and_comments(self) -> None:
        with TemporaryDirectory() as tmp:
            rig = _make_rig(Path(tmp), existing_stories=["EL-100-foo.md"])
            issue = _issue(283)
            gh = _fake_gh_factory(
                [
                    _ok(json.dumps([issue])),
                    _ok("https://github.com/org/repo/issues/283#comment-1"),
                ]
            )
            with redirect_stdout(io.StringIO()):
                rc = autofix.spawn_command(self._args(rig), gh_runner=gh)
            self.assertEqual(rc, 0)
            written = list((rig / "stories").glob("EL-101-*.md"))
            self.assertEqual(len(written), 1)
            content = written[0].read_text()
            self.assertIn("status: ready", content)
            self.assertIn("Closes #283", content)
            # Comment call carries the marker.
            self.assertEqual(gh.calls[1][0:3], ["issue", "comment", "283"])
            body_arg = gh.calls[1][gh.calls[1].index("--body") + 1]
            self.assertIn("<!-- tech-debt-autofix-spawned story=EL-101 -->", body_arg)

    def test_already_spawned_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            rig = _make_rig(Path(tmp))
            marker = "<!-- tech-debt-autofix-spawned story=EL-050 -->"
            issue = _issue(283, comments=[{"body": f"earlier run. {marker}"}])
            gh = _fake_gh_factory([_ok(json.dumps([issue]))])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = autofix.spawn_command(self._args(rig), gh_runner=gh)
            self.assertEqual(rc, 0)
            self.assertIn("already spawned", buf.getvalue())
            # No comment call; only the list call ran.
            self.assertEqual(len(gh.calls), 1)
            self.assertFalse(any((rig / "stories").glob("EL-*.md")))

    def test_partial_body_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            rig = _make_rig(Path(tmp))
            issue = _issue(283, body="## Unrelated content\n\nNo trailer fields.\n")
            gh = _fake_gh_factory([_ok(json.dumps([issue]))])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = autofix.spawn_command(self._args(rig), gh_runner=gh)
            self.assertEqual(rc, 0)
            self.assertIn("body missing required fields", buf.getvalue())
            self.assertEqual(len(gh.calls), 1)

    def test_multiple_issues_ids_advance(self) -> None:
        with TemporaryDirectory() as tmp:
            rig = _make_rig(Path(tmp), existing_stories=["EL-100-foo.md"])
            issues = [_issue(283), _issue(284, title="[tech-debt] Second one")]
            gh = _fake_gh_factory(
                [
                    _ok(json.dumps(issues)),
                    _ok(""),  # comment on #283
                    _ok(""),  # comment on #284
                ]
            )
            with redirect_stdout(io.StringIO()):
                rc = autofix.spawn_command(self._args(rig), gh_runner=gh)
            self.assertEqual(rc, 0)
            written = sorted((rig / "stories").glob("EL-1*.md"))
            names = [p.name for p in written]
            self.assertIn("EL-100-foo.md", names)
            self.assertTrue(any(n.startswith("EL-101-") for n in names))
            self.assertTrue(any(n.startswith("EL-102-") for n in names))

    def test_single_issue_mode(self) -> None:
        with TemporaryDirectory() as tmp:
            rig = _make_rig(Path(tmp))
            issue = _issue(283)
            gh = _fake_gh_factory(
                [
                    _ok(json.dumps(issue)),  # gh issue view returns object, not list
                    _ok(""),
                ]
            )
            with redirect_stdout(io.StringIO()):
                rc = autofix.spawn_command(self._args(rig, issue=283), gh_runner=gh)
            self.assertEqual(rc, 0)
            self.assertEqual(gh.calls[0][0:3], ["issue", "view", "283"])
            self.assertEqual(len(list((rig / "stories").glob("EL-*.md"))), 1)

    def test_no_issues_exit_clean(self) -> None:
        with TemporaryDirectory() as tmp:
            rig = _make_rig(Path(tmp))
            gh = _fake_gh_factory([_ok("[]")])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = autofix.spawn_command(self._args(rig), gh_runner=gh)
            self.assertEqual(rc, 0)
            self.assertIn("nothing to do", buf.getvalue())

    def test_missing_stories_dir_returns_1(self) -> None:
        with TemporaryDirectory() as tmp:
            gh = _fake_gh_factory([])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = autofix.spawn_command(self._args(Path(tmp)), gh_runner=gh)
            self.assertEqual(rc, 1)


class ListAutofixIssuesTests(unittest.TestCase):
    def test_single_issue_wrong_label_skipped(self) -> None:
        issue = _issue(283, labels=["tech-debt:needs-human", "tech-debt"])
        gh = _fake_gh_factory([_ok(json.dumps(issue))])
        out = autofix.list_autofix_issues(gh_runner=gh, issue_number=283)
        self.assertEqual(out, [])

    def test_single_issue_closed_skipped(self) -> None:
        issue = _issue(283, state="CLOSED")
        gh = _fake_gh_factory([_ok(json.dumps(issue))])
        out = autofix.list_autofix_issues(gh_runner=gh, issue_number=283)
        self.assertEqual(out, [])

    def test_gh_error_returns_empty(self) -> None:
        gh = _fake_gh_factory([_err("boom")])
        out = autofix.list_autofix_issues(gh_runner=gh)
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
