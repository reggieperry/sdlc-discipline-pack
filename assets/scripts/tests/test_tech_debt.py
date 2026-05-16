"""Tests for the tech-debt automation module.

Covers trailer parsing, item validation, feature-gate config reading, dedup
logic, issue-body construction, and the top-level `file` subcommand flow.

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
from contextlib import redirect_stderr, redirect_stdout
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
    / "tech_debt.py"
)
assert MODULE_PATH.exists(), f"tech_debt.py not found at {MODULE_PATH}"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("tech_debt", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tech_debt = _load_module()


def _fake_gh_factory(responses: list[subprocess.CompletedProcess[str]]) -> Any:
    """Build a fake gh runner that returns canned responses in order."""
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


VALID_ITEM = {
    "target_path": "core/coordinator.py",
    "target_lines": "267-282",
    "severity": "med",
    "category": "docstring-vs-code",
    "summary": "Docstring claim doesn't match code behavior",
    "suggested_fix": "Tighten docstring or add flag-check in _walk_stages",
}


def _trailer_block(items: list[dict[str, Any]]) -> str:
    body = json.dumps(items, indent=2)
    return f"```json tech_debt_trailer\n{body}\n```"


class TrailerParsingTests(unittest.TestCase):
    def test_missing_file_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(tech_debt.parse_trailer(Path(tmp) / "nope.md"), [])

    def test_no_trailer_fence_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.md"
            path.write_text("# Review\n\nProse only, no trailer here.\n")
            self.assertEqual(tech_debt.parse_trailer(path), [])

    def test_valid_trailer_returns_items(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.md"
            path.write_text("# Review\n\n" + _trailer_block([VALID_ITEM]) + "\n")
            items = tech_debt.parse_trailer(path)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["summary"], VALID_ITEM["summary"])

    def test_malformed_json_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.md"
            path.write_text("```json tech_debt_trailer\n[not valid json\n```\n")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(tech_debt.parse_trailer(path), [])

    def test_top_level_not_list_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.md"
            path.write_text('```json tech_debt_trailer\n{"foo": "bar"}\n```\n')
            with redirect_stderr(io.StringIO()):
                self.assertEqual(tech_debt.parse_trailer(path), [])

    def test_empty_array_returns_empty_list(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.md"
            path.write_text("```json tech_debt_trailer\n[]\n```\n")
            self.assertEqual(tech_debt.parse_trailer(path), [])


class ValidationTests(unittest.TestCase):
    def test_valid_item_passes(self) -> None:
        self.assertIsNone(tech_debt.validate_item(VALID_ITEM))

    def test_missing_field_caught(self) -> None:
        bad = {k: v for k, v in VALID_ITEM.items() if k != "summary"}
        reason = tech_debt.validate_item(bad)
        assert reason is not None
        self.assertIn("summary", reason)

    def test_invalid_severity_caught(self) -> None:
        bad = dict(VALID_ITEM, severity="critical")
        reason = tech_debt.validate_item(bad)
        assert reason is not None
        self.assertIn("severity", reason)

    def test_empty_summary_caught(self) -> None:
        bad = dict(VALID_ITEM, summary="   ")
        reason = tech_debt.validate_item(bad)
        assert reason is not None
        self.assertIn("summary", reason)

    def test_non_dict_caught(self) -> None:
        reason = tech_debt.validate_item(["not a dict"])
        assert reason is not None
        self.assertIn("not a JSON object", reason)


class FeatureGateTests(unittest.TestCase):
    def test_missing_config_returns_false(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertFalse(tech_debt.is_enabled(Path(tmp)))

    def test_config_without_section_returns_false(self) -> None:
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "architecture.toml").write_text('[other]\nkey = "value"\n')
            self.assertFalse(tech_debt.is_enabled(Path(tmp)))

    def test_config_with_enabled_false_returns_false(self) -> None:
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "architecture.toml").write_text(
                "[tech_debt_automation]\nenabled = false\n"
            )
            self.assertFalse(tech_debt.is_enabled(Path(tmp)))

    def test_config_with_enabled_true_returns_true(self) -> None:
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "architecture.toml").write_text("[tech_debt_automation]\nenabled = true\n")
            self.assertTrue(tech_debt.is_enabled(Path(tmp)))

    def test_config_under_project_rules_dir_found(self) -> None:
        with TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / ".claude" / "rules" / "project"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "architecture.toml").write_text("[tech_debt_automation]\nenabled = true\n")
            self.assertTrue(tech_debt.is_enabled(Path(tmp)))

    def test_malformed_toml_returns_false(self) -> None:
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "architecture.toml").write_text("not valid toml = =\n")
            with redirect_stderr(io.StringIO()):
                self.assertFalse(tech_debt.is_enabled(Path(tmp)))


class EnsureLabelTests(unittest.TestCase):
    def test_label_already_present_no_create(self) -> None:
        gh = _fake_gh_factory([_ok(json.dumps([{"name": "tech-debt"}]))])
        self.assertTrue(tech_debt.ensure_label(gh_runner=gh))
        # Only the list call; no create.
        self.assertEqual(len(gh.calls), 1)
        self.assertEqual(gh.calls[0][:2], ["label", "list"])

    def test_label_absent_creates_successfully(self) -> None:
        gh = _fake_gh_factory([_ok("[]"), _ok("https://github.com/owner/repo/labels/tech-debt\n")])
        self.assertTrue(tech_debt.ensure_label(gh_runner=gh))
        self.assertEqual(len(gh.calls), 2)
        self.assertEqual(gh.calls[1][:3], ["label", "create", "tech-debt"])

    def test_label_create_already_exists_treated_as_success(self) -> None:
        already = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="label 'tech-debt' already exists\n",
        )
        gh = _fake_gh_factory([_ok("[]"), already])
        self.assertTrue(tech_debt.ensure_label(gh_runner=gh))

    def test_label_create_hard_failure_returns_false(self) -> None:
        gh = _fake_gh_factory([_ok("[]"), _err("network down")])
        with redirect_stderr(io.StringIO()):
            self.assertFalse(tech_debt.ensure_label(gh_runner=gh))

    def test_label_list_failure_falls_through_to_create(self) -> None:
        # If list fails, we still attempt create; create succeeds.
        gh = _fake_gh_factory(
            [_err("temporary"), _ok("https://github.com/owner/repo/labels/tech-debt\n")]
        )
        self.assertTrue(tech_debt.ensure_label(gh_runner=gh))


class IssueExistsTests(unittest.TestCase):
    def test_match_returns_true(self) -> None:
        gh = _fake_gh_factory([_ok(json.dumps([{"title": "[tech-debt] Foo"}]))])
        self.assertTrue(tech_debt.issue_exists("[tech-debt] Foo", gh_runner=gh))
        self.assertEqual(gh.calls[0][:4], ["issue", "list", "--label", "tech-debt"])

    def test_no_match_returns_false(self) -> None:
        gh = _fake_gh_factory([_ok(json.dumps([{"title": "[tech-debt] Bar"}]))])
        self.assertFalse(tech_debt.issue_exists("[tech-debt] Foo", gh_runner=gh))

    def test_empty_search_returns_false(self) -> None:
        gh = _fake_gh_factory([_ok("[]")])
        self.assertFalse(tech_debt.issue_exists("[tech-debt] Foo", gh_runner=gh))

    def test_gh_failure_returns_false_with_stderr(self) -> None:
        gh = _fake_gh_factory([_err("auth required")])
        with redirect_stderr(io.StringIO()):
            self.assertFalse(tech_debt.issue_exists("[tech-debt] Foo", gh_runner=gh))


class CreateIssueTests(unittest.TestCase):
    def test_success_returns_url(self) -> None:
        gh = _fake_gh_factory([_ok("https://github.com/owner/repo/issues/42\n")])
        url = tech_debt.create_issue("[tech-debt] Foo", "body", gh_runner=gh)
        self.assertEqual(url, "https://github.com/owner/repo/issues/42")
        self.assertEqual(gh.calls[0][:2], ["issue", "create"])
        self.assertIn("--label", gh.calls[0])
        self.assertIn("tech-debt", gh.calls[0])

    def test_failure_returns_none(self) -> None:
        gh = _fake_gh_factory([_err("network down")])
        with redirect_stderr(io.StringIO()):
            self.assertIsNone(tech_debt.create_issue("[tech-debt] Foo", "body", gh_runner=gh))

    def test_empty_stdout_returns_none(self) -> None:
        gh = _fake_gh_factory([_ok("")])
        self.assertIsNone(tech_debt.create_issue("[tech-debt] Foo", "body", gh_runner=gh))


class IssueBodyTests(unittest.TestCase):
    def test_body_contains_required_fields(self) -> None:
        body = tech_debt.build_issue_body(
            VALID_ITEM,
            "https://github.com/owner/repo/pull/100",
            "reviews/el-zbcku6.md",
        )
        self.assertIn(VALID_ITEM["target_path"], body)
        self.assertIn(VALID_ITEM["target_lines"], body)
        self.assertIn(VALID_ITEM["severity"], body)
        self.assertIn(VALID_ITEM["category"], body)
        self.assertIn(VALID_ITEM["suggested_fix"], body)
        self.assertIn("https://github.com/owner/repo/pull/100", body)
        self.assertIn("reviews/el-zbcku6.md", body)


class FileCommandTests(unittest.TestCase):
    def _args(self, rig_root: Path, review_file: Path, pr_url: str = "") -> argparse.Namespace:
        return argparse.Namespace(
            rig_root=rig_root,
            review_file=review_file,
            pr_url=pr_url,
            command="file",
        )

    def test_disabled_rig_no_ops(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # No architecture.toml -> disabled
            review = root / "reviews" / "el-x.md"
            review.parent.mkdir()
            review.write_text("# Review\n\n" + _trailer_block([VALID_ITEM]))
            gh = _fake_gh_factory([])  # asserts no gh calls happen
            with redirect_stdout(io.StringIO()) as out:
                rc = tech_debt.file_command(self._args(root, review), gh_runner=gh)
            self.assertEqual(rc, 0)
            self.assertIn("disabled", out.getvalue())
            self.assertEqual(gh.calls, [])

    def test_missing_trailer_no_ops(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "architecture.toml").write_text("[tech_debt_automation]\nenabled = true\n")
            review = root / "reviews" / "el-x.md"
            review.parent.mkdir()
            review.write_text("# Review\n\nNo trailer.\n")
            gh = _fake_gh_factory([])
            with redirect_stdout(io.StringIO()) as out:
                rc = tech_debt.file_command(self._args(root, review), gh_runner=gh)
            self.assertEqual(rc, 0)
            self.assertIn("no trailer", out.getvalue())
            self.assertEqual(gh.calls, [])

    def test_files_one_issue_when_no_duplicate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "architecture.toml").write_text("[tech_debt_automation]\nenabled = true\n")
            review = root / "reviews" / "el-x.md"
            review.parent.mkdir()
            review.write_text("# Review\n\n" + _trailer_block([VALID_ITEM]))
            gh = _fake_gh_factory(
                [
                    _ok(json.dumps([{"name": "tech-debt"}])),  # ensure_label: present
                    _ok("[]"),  # dedup check: no existing
                    _ok("https://github.com/owner/repo/issues/42\n"),  # create
                ]
            )
            with redirect_stdout(io.StringIO()) as out:
                rc = tech_debt.file_command(
                    self._args(root, review, pr_url="https://github.com/owner/repo/pull/100"),
                    gh_runner=gh,
                )
            self.assertEqual(rc, 0)
            self.assertIn("filed", out.getvalue())
            self.assertEqual(len(gh.calls), 3)

    def test_skips_duplicate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "architecture.toml").write_text("[tech_debt_automation]\nenabled = true\n")
            review = root / "reviews" / "el-x.md"
            review.parent.mkdir()
            review.write_text("# Review\n\n" + _trailer_block([VALID_ITEM]))
            title = f"[tech-debt] {VALID_ITEM['summary']}"
            gh = _fake_gh_factory(
                [
                    _ok(json.dumps([{"name": "tech-debt"}])),  # ensure_label: present
                    _ok(json.dumps([{"title": title}])),  # dedup: matches
                ]
            )
            with redirect_stdout(io.StringIO()) as out:
                rc = tech_debt.file_command(self._args(root, review), gh_runner=gh)
            self.assertEqual(rc, 0)
            self.assertIn("dup", out.getvalue())
            self.assertEqual(len(gh.calls), 2)  # label-list + dedup-list

    def test_skips_invalid_item_files_valid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "architecture.toml").write_text("[tech_debt_automation]\nenabled = true\n")
            review = root / "reviews" / "el-x.md"
            review.parent.mkdir()
            invalid = dict(VALID_ITEM, severity="critical")
            review.write_text("# Review\n\n" + _trailer_block([invalid, VALID_ITEM]))
            gh = _fake_gh_factory(
                [
                    _ok(json.dumps([{"name": "tech-debt"}])),  # ensure_label: present
                    _ok("[]"),  # dedup for the valid item
                    _ok("https://github.com/owner/repo/issues/42\n"),  # create the valid item
                ]
            )
            with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
                rc = tech_debt.file_command(self._args(root, review), gh_runner=gh)
            self.assertEqual(rc, 0)
            output = out.getvalue()
            self.assertIn("filed", output)
            self.assertIn("1 filed, 0 dup, 1 invalid", output)

    def test_label_provisioning_failure_aborts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "architecture.toml").write_text("[tech_debt_automation]\nenabled = true\n")
            review = root / "reviews" / "el-x.md"
            review.parent.mkdir()
            review.write_text("# Review\n\n" + _trailer_block([VALID_ITEM]))
            # list returns no label; create fails with a hard error
            gh = _fake_gh_factory([_ok("[]"), _err("permission denied")])
            err_buf = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err_buf):
                rc = tech_debt.file_command(self._args(root, review), gh_runner=gh)
            self.assertEqual(rc, 0)  # non-blocking: returns 0 even on abort
            self.assertIn("label provisioning failed", err_buf.getvalue())
            # Should not have attempted dedup or create
            self.assertEqual(len(gh.calls), 2)


if __name__ == "__main__":
    unittest.main()
