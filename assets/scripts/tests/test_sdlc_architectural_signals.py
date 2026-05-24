"""Tests for ``sdlc-architectural-signals.py``.

Each test builds a tiny throwaway git repository, makes a baseline commit, applies
a change, makes a head commit, and invokes the signals script against the two
SHAs. Tests are stdlib-only (``unittest`` + ``subprocess`` + ``tempfile``); no
pytest dependency.

Run with:

    python3 -m unittest discover -s assets/scripts/tests -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "sdlc-architectural-signals.py"
assert SCRIPT.exists(), f"signals script not found at {SCRIPT}"


def _load_module() -> object:
    """Import the script as a module (its filename has a hyphen so it's not directly importable)."""
    spec = importlib.util.spec_from_file_location("signals_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # required by @dataclass on 3.13+
    spec.loader.exec_module(mod)
    return mod


signals = _load_module()


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    base_env = os.environ.copy()
    base_env.setdefault("GIT_AUTHOR_NAME", "test")
    base_env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    base_env.setdefault("GIT_COMMITTER_NAME", "test")
    base_env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    if env:
        base_env.update(env)
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=base_env, capture_output=True, text=True, check=True
    )
    return result.stdout


def make_repo() -> Path:
    """Create a temp git repo with one initial commit; return its path."""
    tmp = Path(tempfile.mkdtemp(prefix="signals-test-"))
    _git(tmp, "init", "-q", "-b", "main")
    (tmp / "README.md").write_text("# fixture\n")
    _git(tmp, "add", ".")
    _git(tmp, "commit", "-q", "-m", "initial")
    return tmp


def commit(repo: Path, files: dict[str, str], message: str) -> str:
    for relpath, content in files.items():
        path = repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def write_rig_config(repo: Path, **fields: list[str]) -> Path:
    cfg = repo / "architecture.toml"
    lines = []
    for key, value in fields.items():
        items = ", ".join(f'"{v}"' for v in value)
        lines.append(f"{key} = [{items}]")
    cfg.write_text("\n".join(lines) + "\n")
    return cfg


def run_script(repo: Path, baseline: str, head: str, rig_config: Path) -> dict:
    """Invoke the script in-process via its run() function (cwd-bound)."""
    cwd_before = os.getcwd()
    os.chdir(repo)
    try:
        return signals.run(baseline, head, rig_config)  # type: ignore[attr-defined]
    finally:
        os.chdir(cwd_before)


# ---------- Signal A — sensitive file delta ----------------------------------


class SignalA(unittest.TestCase):
    def test_fires_when_sensitive_file_touched(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"agents/risk_agent.py": "x = 1\n"}, "baseline")
        head = commit(repo, {"agents/risk_agent.py": "x = 2\n"}, "edit")
        cfg = write_rig_config(repo, sensitive_files=["agents/risk_agent.py"])
        result = run_script(repo, baseline, head, cfg)
        self.assertIn("A", result["signals"])
        self.assertEqual(result["recommendation"], "human_required")

    def test_glob_match(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"indicators/elder.py": "x = 1\n"}, "baseline")
        head = commit(repo, {"indicators/elder.py": "x = 2\n"}, "edit")
        cfg = write_rig_config(repo, sensitive_files=["indicators/*.py"])
        result = run_script(repo, baseline, head, cfg)
        self.assertIn("A", result["signals"])

    def test_does_not_fire_when_non_sensitive(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"docs/notes.md": "hi\n"}, "baseline")
        head = commit(repo, {"docs/notes.md": "hello\n"}, "edit")
        cfg = write_rig_config(repo, sensitive_files=["agents/risk_agent.py"])
        result = run_script(repo, baseline, head, cfg)
        self.assertNotIn("A", result["signals"])


# ---------- Signal B — Protocol signature delta -------------------------------


PROTOCOL_BASELINE = textwrap.dedent(
    """\
    from typing import Protocol, runtime_checkable

    @runtime_checkable
    class Agent(Protocol):
        def run(self, x: int) -> str: ...
    """
)

PROTOCOL_SIG_CHANGED = textwrap.dedent(
    """\
    from typing import Protocol, runtime_checkable

    @runtime_checkable
    class Agent(Protocol):
        def run(self, x: int, y: int) -> str: ...
    """
)

PROTOCOL_METHOD_ADDED = textwrap.dedent(
    """\
    from typing import Protocol, runtime_checkable

    @runtime_checkable
    class Agent(Protocol):
        def run(self, x: int) -> str: ...
        def teardown(self) -> None: ...
    """
)

# Same method, sync → async. The args and return annotation are identical;
# the only delta is `def → async def`. Empirical case: Elder PR #220
# (EL-078) — `CheckpointStore.save` on a protocol_modules-listed file.
PROTOCOL_SYNC_TO_ASYNC = textwrap.dedent(
    """\
    from typing import Protocol, runtime_checkable

    @runtime_checkable
    class Agent(Protocol):
        async def run(self, x: int) -> str: ...
    """
)

# Method stays async with identical signature — no Signal B fire.
# Comment changes between baseline and head so the commit is non-empty
# while the Protocol signature is unchanged.
PROTOCOL_ASYNC_BASELINE = textwrap.dedent(
    """\
    # async-protocol baseline (rev a)
    from typing import Protocol, runtime_checkable

    @runtime_checkable
    class Agent(Protocol):
        async def run(self, x: int) -> str: ...
    """
)

PROTOCOL_ASYNC_HEAD_NO_SIG_CHANGE = textwrap.dedent(
    """\
    # async-protocol baseline (rev b — comment-only edit, no sig change)
    from typing import Protocol, runtime_checkable

    @runtime_checkable
    class Agent(Protocol):
        async def run(self, x: int) -> str: ...
    """
)


class SignalB(unittest.TestCase):
    def test_fires_on_signature_change(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"core/agent.py": PROTOCOL_BASELINE}, "baseline")
        head = commit(repo, {"core/agent.py": PROTOCOL_SIG_CHANGED}, "edit")
        cfg = write_rig_config(repo, protocol_modules=["core/agent.py"])
        result = run_script(repo, baseline, head, cfg)
        self.assertIn("B", result["signals"])
        self.assertEqual(result["recommendation"], "human_required")

    def test_does_not_fire_on_method_addition(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"core/agent.py": PROTOCOL_BASELINE}, "baseline")
        head = commit(repo, {"core/agent.py": PROTOCOL_METHOD_ADDED}, "edit")
        cfg = write_rig_config(repo, protocol_modules=["core/agent.py"])
        result = run_script(repo, baseline, head, cfg)
        self.assertNotIn("B", result["signals"])

    def test_fires_on_sync_to_async_change(self) -> None:
        """def → async def on a Protocol method must fire Signal B.

        Args and return annotation are identical; only the function kind
        changes. The signature identity must include the kind to catch
        this — without it (pre-fix), Signal B silently passed and the
        recommendation undercounted Protocol ripples.
        """
        repo = make_repo()
        baseline = commit(repo, {"core/agent.py": PROTOCOL_BASELINE}, "baseline")
        head = commit(repo, {"core/agent.py": PROTOCOL_SYNC_TO_ASYNC}, "edit")
        cfg = write_rig_config(repo, protocol_modules=["core/agent.py"])
        result = run_script(repo, baseline, head, cfg)
        self.assertIn("B", result["signals"])
        self.assertEqual(result["recommendation"], "human_required")

    def test_does_not_fire_on_async_to_async_identical(self) -> None:
        """Async Protocol method with identical signature must NOT fire."""
        repo = make_repo()
        baseline = commit(repo, {"core/agent.py": PROTOCOL_ASYNC_BASELINE}, "baseline")
        head = commit(repo, {"core/agent.py": PROTOCOL_ASYNC_HEAD_NO_SIG_CHANGE}, "comment edit")
        cfg = write_rig_config(repo, protocol_modules=["core/agent.py"])
        result = run_script(repo, baseline, head, cfg)
        self.assertNotIn("B", result["signals"])


# ---------- Signal C — domain-model field delta -------------------------------


DOMAIN_BASELINE = textwrap.dedent(
    """\
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Trade:
        ticker: str
        size: int
    """
)

DOMAIN_FIELD_REMOVED = textwrap.dedent(
    """\
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Trade:
        ticker: str
    """
)

DOMAIN_FIELD_ADDED = textwrap.dedent(
    """\
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Trade:
        ticker: str
        size: int
        price: float
    """
)


class SignalC(unittest.TestCase):
    def test_fires_on_field_removal(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"core/state.py": DOMAIN_BASELINE}, "baseline")
        head = commit(repo, {"core/state.py": DOMAIN_FIELD_REMOVED}, "edit")
        cfg = write_rig_config(repo, domain_model_files=["core/state.py"])
        result = run_script(repo, baseline, head, cfg)
        self.assertIn("C", result["signals"])
        details_c = [d for d in result["details"] if d["signal"] == "C"]
        self.assertEqual(details_c[0]["class"], "Trade")
        self.assertEqual(details_c[0]["field"], "size")

    def test_does_not_fire_on_field_addition(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"core/state.py": DOMAIN_BASELINE}, "baseline")
        head = commit(repo, {"core/state.py": DOMAIN_FIELD_ADDED}, "edit")
        cfg = write_rig_config(repo, domain_model_files=["core/state.py"])
        result = run_script(repo, baseline, head, cfg)
        self.assertNotIn("C", result["signals"])


# ---------- Signal D — layer crossing ----------------------------------------


class SignalD(unittest.TestCase):
    def test_lint_imports_absent_does_not_fire(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"a.py": "x = 1\n"}, "baseline")
        head = commit(repo, {"a.py": "x = 2\n"}, "edit")
        cfg = write_rig_config(repo)
        result = run_script(repo, baseline, head, cfg)
        self.assertNotIn("D", result["signals"])
        # availability may be "unavailable" or "available" depending on env; both fine
        self.assertIn(
            result["tool_availability"]["lint_imports"],
            ("available", "unavailable", "error"),
        )


# ---------- Signal E — public-name removal -----------------------------------


NAMES_BASELINE = textwrap.dedent(
    """\
    __all__ = ["alpha", "beta"]

    def alpha() -> int:
        return 1

    def beta() -> int:
        return 2
    """
)

NAMES_REMOVAL = textwrap.dedent(
    """\
    __all__ = ["alpha"]

    def alpha() -> int:
        return 1
    """
)

NAMES_RENAME = textwrap.dedent(
    """\
    __all__ = ["alpha", "gamma"]

    def alpha() -> int:
        return 1

    def gamma() -> int:
        return 2
    """
)


class SignalE(unittest.TestCase):
    def test_fires_on_pure_removal(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"mod.py": NAMES_BASELINE}, "baseline")
        head = commit(repo, {"mod.py": NAMES_REMOVAL}, "edit")
        cfg = write_rig_config(repo)
        result = run_script(repo, baseline, head, cfg)
        self.assertIn("E", result["signals"])

    def test_does_not_fire_on_rename(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"mod.py": NAMES_BASELINE}, "baseline")
        head = commit(repo, {"mod.py": NAMES_RENAME}, "edit")
        cfg = write_rig_config(repo)
        result = run_script(repo, baseline, head, cfg)
        self.assertNotIn("E", result["signals"])


# ---------- Signal F — assertion regression ----------------------------------


TEST_BASELINE = textwrap.dedent(
    """\
    def test_a():
        assert 1 == 1
        assert 2 == 2
        assert 3 == 3
    """
)

TEST_REGRESSION = textwrap.dedent(
    """\
    def test_a():
        assert 1 == 1
    """
)


class SignalF(unittest.TestCase):
    def test_fires_on_assertion_count_drop(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"tests/test_thing.py": TEST_BASELINE}, "baseline")
        head = commit(repo, {"tests/test_thing.py": TEST_REGRESSION}, "edit")
        cfg = write_rig_config(repo)
        result = run_script(repo, baseline, head, cfg)
        self.assertIn("F", result["signals"])

    def test_does_not_fire_on_assertion_stable(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"tests/test_thing.py": TEST_BASELINE}, "baseline")
        head = commit(repo, {"tests/test_thing.py": TEST_BASELINE + "# new comment\n"}, "edit")
        cfg = write_rig_config(repo)
        result = run_script(repo, baseline, head, cfg)
        self.assertNotIn("F", result["signals"])


# ---------- Signal G — mechanical sweep --------------------------------------


def _spec_with_status(status_value: str) -> str:
    return textwrap.dedent(
        f"""\
        ---
        story_id: EL-XXX
        title: example
        status: {status_value}
        labels: []
        ---

        # body
        """
    )


def _spec_with_priority(priority_value: str) -> str:
    return textwrap.dedent(
        f"""\
        ---
        story_id: EL-YYY
        title: example
        priority: {priority_value}
        labels: []
        ---

        # body
        """
    )


class SignalG(unittest.TestCase):
    """Mechanical-sweep recognition (pack #76)."""

    def test_fires_on_uniform_status_sweep_across_stories(self) -> None:
        repo = make_repo()
        baseline = commit(
            repo,
            {
                "stories/EL-100-a.md": _spec_with_status("ready"),
                "stories/EL-101-b.md": _spec_with_status("ready"),
                "stories/EL-102-c.md": _spec_with_status("ready"),
            },
            "baseline",
        )
        head = commit(
            repo,
            {
                "stories/EL-100-a.md": _spec_with_status("shipped"),
                "stories/EL-101-b.md": _spec_with_status("shipped"),
                "stories/EL-102-c.md": _spec_with_status("shipped"),
            },
            "sweep status ready -> shipped",
        )
        cfg = write_rig_config(repo)
        result = run_script(repo, baseline, head, cfg)
        self.assertIn("G", result["signals"])
        self.assertEqual(result["recommendation"], "review_encouraged")

    def test_sensitive_file_override_wins_back_to_human_required(self) -> None:
        """Signal A trumps Signal G — sensitive-file touch is always human_required."""
        repo = make_repo()
        baseline = commit(
            repo,
            {
                "stories/EL-200-a.md": _spec_with_status("ready"),
                "stories/EL-201-b.md": _spec_with_status("ready"),
            },
            "baseline",
        )
        head = commit(
            repo,
            {
                "stories/EL-200-a.md": _spec_with_status("shipped"),
                "stories/EL-201-b.md": _spec_with_status("shipped"),
            },
            "sweep",
        )
        # Declare every stories/*.md as sensitive — pathological but exercises
        # the override.
        cfg = write_rig_config(repo, sensitive_files=["stories/*.md"])
        result = run_script(repo, baseline, head, cfg)
        self.assertIn("A", result["signals"])
        self.assertEqual(
            result["recommendation"],
            "human_required",
            msg="sensitive-file Signal A must override Signal G",
        )

    def test_does_not_fire_on_mixed_yaml_keys(self) -> None:
        """Different keys across files disqualifies the sweep."""
        repo = make_repo()
        baseline = commit(
            repo,
            {
                "stories/EL-300-a.md": _spec_with_status("ready"),
                "stories/EL-301-b.md": _spec_with_priority("high"),
            },
            "baseline",
        )
        head = commit(
            repo,
            {
                "stories/EL-300-a.md": _spec_with_status("shipped"),
                "stories/EL-301-b.md": _spec_with_priority("low"),
            },
            "mixed-key edits",
        )
        cfg = write_rig_config(repo)
        result = run_script(repo, baseline, head, cfg)
        self.assertNotIn("G", result["signals"])

    def test_does_not_fire_on_single_file(self) -> None:
        """One file is not a sweep."""
        repo = make_repo()
        baseline = commit(repo, {"stories/EL-400-a.md": _spec_with_status("ready")}, "baseline")
        head = commit(
            repo, {"stories/EL-400-a.md": _spec_with_status("shipped")}, "single-file edit"
        )
        cfg = write_rig_config(repo)
        result = run_script(repo, baseline, head, cfg)
        self.assertNotIn("G", result["signals"])

    def test_does_not_fire_when_non_spec_file_in_diff(self) -> None:
        """Sweep is disqualified if any file is outside stories/."""
        repo = make_repo()
        baseline = commit(
            repo,
            {
                "stories/EL-500-a.md": _spec_with_status("ready"),
                "stories/EL-501-b.md": _spec_with_status("ready"),
                "core/unrelated.py": "x = 1\n",
            },
            "baseline",
        )
        head = commit(
            repo,
            {
                "stories/EL-500-a.md": _spec_with_status("shipped"),
                "stories/EL-501-b.md": _spec_with_status("shipped"),
                "core/unrelated.py": "x = 2\n",
            },
            "mixed dir edit",
        )
        cfg = write_rig_config(repo)
        result = run_script(repo, baseline, head, cfg)
        self.assertNotIn("G", result["signals"])

    def test_does_not_fire_on_multi_line_per_file(self) -> None:
        """A file with 2+/2- breaks the uniform 1+/1- shape."""
        repo = make_repo()
        spec_base = _spec_with_status("ready")
        # Add a second changed line — modify title too — to break uniformity.
        spec_head = spec_base.replace("status: ready", "status: shipped").replace(
            "title: example", "title: example revised"
        )
        baseline = commit(
            repo,
            {
                "stories/EL-600-a.md": spec_base,
                "stories/EL-601-b.md": spec_base,
            },
            "baseline",
        )
        head = commit(
            repo,
            {
                "stories/EL-600-a.md": spec_head,
                "stories/EL-601-b.md": spec_head,
            },
            "two-line per file",
        )
        cfg = write_rig_config(repo)
        result = run_script(repo, baseline, head, cfg)
        self.assertNotIn("G", result["signals"])


# ---------- Missing rig-config ----------------------------------------------


class MissingConfig(unittest.TestCase):
    def test_missing_config_forces_human_required(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"a.py": "x = 1\n"}, "baseline")
        head = commit(repo, {"a.py": "x = 2\n"}, "edit")
        result = run_script(repo, baseline, head, repo / "nonexistent-architecture.toml")
        self.assertEqual(result["recommendation"], "human_required")
        self.assertEqual(result["signals"], ["MISSING_CONFIG"])
        self.assertFalse(result["rig_config"]["present"])


# ---------- Recommendation derivation (glance vs review_encouraged) ----------


class Recommendation(unittest.TestCase):
    def test_glance_merge_for_tiny_additive_diff(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"a.py": "x = 1\n"}, "baseline")
        head = commit(repo, {"a.py": "x = 1\ny = 2\n"}, "additive edit")
        cfg = write_rig_config(repo)
        result = run_script(repo, baseline, head, cfg)
        # Pure addition, no removed line, no signals: glance_merge.
        self.assertEqual(result["signals"], [])
        self.assertEqual(result["recommendation"], "glance_merge")

    def test_review_encouraged_when_body_edits(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"a.py": "x = 1\n"}, "baseline")
        head = commit(repo, {"a.py": "x = 2\n"}, "edit existing line")
        cfg = write_rig_config(repo)
        result = run_script(repo, baseline, head, cfg)
        # No signals, but a line was deleted -> edits_existing_function_bodies=True.
        self.assertEqual(result["signals"], [])
        self.assertEqual(result["recommendation"], "review_encouraged")


# ---------- Output schema sanity --------------------------------------------


class Schema(unittest.TestCase):
    def test_top_level_keys(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"a.py": "x = 1\n"}, "baseline")
        head = commit(repo, {"a.py": "x = 1\ny = 2\n"}, "edit")
        cfg = write_rig_config(repo)
        result = run_script(repo, baseline, head, cfg)
        for key in (
            "version",
            "baseline_sha",
            "head_sha",
            "rig_config",
            "signals",
            "details",
            "diff_stats",
            "recommendation",
            "tool_availability",
        ):
            self.assertIn(key, result, f"missing top-level key: {key}")
        self.assertEqual(result["version"], "1")

    def test_json_round_trip(self) -> None:
        repo = make_repo()
        baseline = commit(repo, {"a.py": "x = 1\n"}, "baseline")
        head = commit(repo, {"a.py": "x = 1\ny = 2\n"}, "edit")
        cfg = write_rig_config(repo)
        result = run_script(repo, baseline, head, cfg)
        # Should be JSON-serializable end-to-end.
        round_tripped = json.loads(json.dumps(result))
        self.assertEqual(round_tripped, result)


if __name__ == "__main__":
    unittest.main()
