#!/usr/bin/env python3
"""sdlc-architectural-signals.py — detect architectural signals in a diff.

Six signals (A-F); any one fires → ``recommendation = "human_required"``.

::

    Signal A   sensitive file delta            (file matches rig-config sensitive_files)
    Signal B   Protocol signature delta        (method sig changed on @runtime_checkable)
    Signal C   domain-model field delta        (frozen-dataclass field renamed or removed)
    Signal D   architectural layer crossing    (lint-imports contract violation)
    Signal E   public-name removal w/o rename  (name in __all__ removed, no equivalent added)
    Signal F   assertion-count regression      (test assertions decreased baseline → head)

None of the detections require LLM judgment: AST diff, regex, and linter output only.
The LLM's job (in the reviewer prompt — story 3) is to SURFACE the signals to a human,
not to decide them.

Usage
-----

::

    sdlc-architectural-signals.py BASELINE_SHA HEAD_SHA --rig-config PATH

The script must be run with the cwd inside the target git repository. ``BASELINE_SHA``
and ``HEAD_SHA`` are passed to ``git show`` and ``git diff`` as-is; commit-ish refs
(branch names, ``HEAD~1``, etc.) work.

``--rig-config`` points to a TOML file declaring the rig's architectural shape::

    sensitive_files = ["risk_parameters.py", "agents/risk_agent.py", "indicators/*.py"]
    domain_model_files = ["core/state.py", "core/domain.py"]
    protocol_modules = ["core/agent.py"]

Path entries are repo-relative and may be shell globs (``fnmatch``). A missing
``--rig-config`` file is itself architectural: the script reports
``signals=["MISSING_CONFIG"]`` and ``recommendation="human_required"`` so the
chain routes every PR to manual review until the rig authors a config.

Output
------

JSON on stdout. Schema version "1". Example::

    {
      "version": "1",
      "baseline_sha": "abc...",
      "head_sha": "def...",
      "rig_config": {"path": ".../architecture.toml", "present": true, ...},
      "signals": ["A", "C"],
      "details": [
        {"signal": "A", "kind": "sensitive_file", "file": "agents/risk_agent.py", ...},
        {"signal": "C", "kind": "domain_field_removed", "file": "core/state.py", ...}
      ],
      "diff_stats": {"files_changed": 3, "lines_added": 42, "lines_removed": 5,
                     "edits_existing_function_bodies": false},
      "recommendation": "human_required",
      "tool_availability": {"lint_imports": "available"}
    }

Stdlib only.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = "1"

# Recommendation cliffs. Tunable but defaulted tight; the LOC rubric used to be
# 200 LOC, but with signals carrying the high-risk-detection load the script can
# afford a stricter "glance" definition.
GLANCE_MAX_FILES = 5
GLANCE_MAX_LINES_ADDED = 100


@dataclass(frozen=True)
class RigConfig:
    """Architectural-shape declaration for a single rig."""

    path: Path
    present: bool
    sensitive_files: tuple[str, ...] = ()
    domain_model_files: tuple[str, ...] = ()
    protocol_modules: tuple[str, ...] = ()


@dataclass
class SignalDetail:
    signal: str
    kind: str
    file: str
    extra: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"signal": self.signal, "kind": self.kind, "file": self.file}
        out.update(self.extra)
        return out


@dataclass(frozen=True)
class SignalContext:
    """Per-invocation context shared across signal_* functions.

    Bundles the four values previously threaded as a data clump through
    every signal function — files, rig, baseline, head. Frozen to match
    `RigConfig` and signal the read-only contract: signal functions
    observe context state but never mutate it.

    Introduced in v2.30 (audit finding #6, Tier 3).
    """

    files: list[tuple[str, str]]
    rig: RigConfig
    baseline: str
    head: str


# ---------- rig-config loading ----------------------------------------------


def load_rig_config(path: Path) -> RigConfig:
    if not path.exists():
        return RigConfig(path=path, present=False)
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return RigConfig(
        path=path,
        present=True,
        sensitive_files=tuple(data.get("sensitive_files", ())),
        domain_model_files=tuple(data.get("domain_model_files", ())),
        protocol_modules=tuple(data.get("protocol_modules", ())),
    )


# ---------- git helpers ------------------------------------------------------


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def changed_files(baseline: str, head: str) -> list[tuple[str, str]]:
    """Return [(status, path), ...] from ``git diff --name-status baseline..head``."""

    raw = _git("diff", "--name-status", f"{baseline}..{head}")
    out: list[tuple[str, str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        # Rename/copy entries have form: R100\told\tnew. Treat as one change at the new path.
        status = parts[0][0]  # first char, drops similarity score
        path = parts[-1]
        out.append((status, path))
    return out


def numstat(baseline: str, head: str) -> tuple[int, int]:
    """Total (lines_added, lines_removed) across the diff. Binary files contribute 0."""

    raw = _git("diff", "--numstat", f"{baseline}..{head}")
    added = removed = 0
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            added += int(parts[0])
            removed += int(parts[1])
        except ValueError:
            continue  # "-" for binary
    return added, removed


def file_at(rev: str, path: str) -> str | None:
    """Return file contents at ``rev``, or None if the file didn't exist there."""

    result = subprocess.run(
        ["git", "show", f"{rev}:{path}"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return None
    return result.stdout


def edits_existing_function_bodies(baseline: str, head: str) -> bool:
    """True if the diff modifies lines inside any pre-existing function body.

    Conservative heuristic: any deletion (``-`` line) in a hunk that isn't at the
    file's end-of-file boundary is treated as an edit to existing code. Pure
    additions (only ``+`` lines, no ``-`` lines in the hunk) do not trigger.
    """

    raw = _git("diff", "--unified=0", f"{baseline}..{head}")
    for line in raw.splitlines():
        # Skip diff metadata; only consider hunk content lines.
        if line.startswith("---") or line.startswith("+++") or line.startswith("diff "):
            continue
        if line.startswith("-") and not line.startswith("---"):
            return True
    return False


# ---------- AST extraction ---------------------------------------------------


def parse_module(src: str) -> ast.Module | None:
    try:
        return ast.parse(src)
    except SyntaxError:
        return None


def dataclass_fields_by_class(mod: ast.Module) -> dict[str, set[str]]:
    """Map class-name → set of field names for ``@dataclass(frozen=True)`` classes."""

    out: dict[str, set[str]] = {}
    for node in mod.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not _is_frozen_dataclass(node):
            continue
        fields = {
            n.target.id
            for n in node.body
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
        }
        out[node.name] = fields
    return out


def _is_frozen_dataclass(node: ast.ClassDef) -> bool:
    for deco in node.decorator_list:
        # @dataclass(frozen=True) or @dataclasses.dataclass(frozen=True)
        if isinstance(deco, ast.Call):
            target = deco.func
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            if name != "dataclass":
                continue
            for kw in deco.keywords:
                if kw.arg == "frozen" and isinstance(kw.value, ast.Constant) and kw.value.value:
                    return True
    return False


def protocol_signatures_by_class(mod: ast.Module) -> dict[str, dict[str, str]]:
    """Map class-name → {method_name: signature-string} for ``@runtime_checkable Protocol`` classes.

    The signature string includes the function kind (``async`` prefix) when
    applicable. ``ast.FunctionDef`` and ``ast.AsyncFunctionDef`` carry the
    sync/async distinction in the node TYPE, not in ``args`` — without the
    explicit prefix, ``def → async def`` round-trips identically and Signal
    B misses the change. Empirical case: Elder PR #220 (EL-078) flipped
    ``CheckpointStore.save`` from sync to async on a ``protocol_modules``-
    listed file; the gate should have routed ``human_required`` and did not.
    """

    out: dict[str, dict[str, str]] = {}
    for node in mod.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not _is_runtime_checkable_protocol(node):
            continue
        methods: dict[str, str] = {}
        for n in node.body:
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef):
                kind_prefix = "async " if isinstance(n, ast.AsyncFunctionDef) else ""
                methods[n.name] = (
                    kind_prefix
                    + ast.unparse(n.args)
                    + " -> "
                    + (ast.unparse(n.returns) if n.returns else "None")
                )
        out[node.name] = methods
    return out


def _is_runtime_checkable_protocol(node: ast.ClassDef) -> bool:
    has_runtime_checkable = any(
        (isinstance(deco, ast.Name) and deco.id == "runtime_checkable")
        or (isinstance(deco, ast.Attribute) and deco.attr == "runtime_checkable")
        for deco in node.decorator_list
    )
    inherits_protocol = any(
        (isinstance(base, ast.Name) and base.id == "Protocol")
        or (isinstance(base, ast.Attribute) and base.attr == "Protocol")
        for base in node.bases
    )
    return has_runtime_checkable and inherits_protocol


def public_names(mod: ast.Module) -> set[str]:
    """Top-level public names: contents of ``__all__`` if present, else non-underscore defs."""

    explicit = _all_list(mod)
    if explicit is not None:
        return explicit
    out: set[str] = set()
    for node in mod.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if not node.name.startswith("_"):
                out.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and not tgt.id.startswith("_"):
                    out.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("_"):
                out.add(node.target.id)
    return out


def _all_list(mod: ast.Module) -> set[str] | None:
    for node in mod.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    if isinstance(node.value, ast.List | ast.Tuple):
                        return {
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        }
    return None


def assertion_count(src: str) -> int:
    mod = parse_module(src)
    if mod is None:
        return 0
    count = 0
    for node in ast.walk(mod):
        if isinstance(node, ast.Assert):
            count += 1
    return count


# ---------- signal detectors -------------------------------------------------


def signal_a_sensitive_files(ctx: SignalContext) -> list[SignalDetail]:
    if not ctx.rig.present or not ctx.rig.sensitive_files:
        return []
    hits: list[SignalDetail] = []
    for _status, path in ctx.files:
        if any(fnmatch.fnmatch(path, pat) for pat in ctx.rig.sensitive_files):
            hits.append(
                SignalDetail(
                    signal="A",
                    kind="sensitive_file",
                    file=path,
                    extra={"rationale": "matched glob in rig-config sensitive_files"},
                )
            )
    return hits


def signal_b_protocol_signatures(ctx: SignalContext) -> list[SignalDetail]:
    if not ctx.rig.present:
        return []
    hits: list[SignalDetail] = []
    paths = {p for _s, p in ctx.files}
    for path in ctx.rig.protocol_modules:
        matches = [p for p in paths if fnmatch.fnmatch(p, path)]
        for matched in matches:
            base_src = file_at(ctx.baseline, matched)
            head_src = file_at(ctx.head, matched)
            if base_src is None or head_src is None:
                continue
            base_mod = parse_module(base_src)
            head_mod = parse_module(head_src)
            if not base_mod or not head_mod:
                continue
            base_sigs = protocol_signatures_by_class(base_mod)
            head_sigs = protocol_signatures_by_class(head_mod)
            for cls, base_methods in base_sigs.items():
                head_methods = head_sigs.get(cls, {})
                for method, base_sig in base_methods.items():
                    head_sig = head_methods.get(method)
                    if head_sig is None:
                        hits.append(
                            SignalDetail(
                                signal="B",
                                kind="protocol_method_removed",
                                file=matched,
                                extra={"class": cls, "method": method},
                            )
                        )
                    elif head_sig != base_sig:
                        hits.append(
                            SignalDetail(
                                signal="B",
                                kind="protocol_signature_changed",
                                file=matched,
                                extra={
                                    "class": cls,
                                    "method": method,
                                    "baseline_sig": base_sig,
                                    "head_sig": head_sig,
                                },
                            )
                        )
    return hits


def signal_c_domain_fields(ctx: SignalContext) -> list[SignalDetail]:
    if not ctx.rig.present:
        return []
    hits: list[SignalDetail] = []
    paths = {p for _s, p in ctx.files}
    for path in ctx.rig.domain_model_files:
        matches = [p for p in paths if fnmatch.fnmatch(p, path)]
        for matched in matches:
            base_src = file_at(ctx.baseline, matched)
            head_src = file_at(ctx.head, matched)
            if base_src is None or head_src is None:
                continue
            base_mod = parse_module(base_src)
            head_mod = parse_module(head_src)
            if not base_mod or not head_mod:
                continue
            base_fields = dataclass_fields_by_class(base_mod)
            head_fields = dataclass_fields_by_class(head_mod)
            for cls, base_set in base_fields.items():
                head_set = head_fields.get(cls, set())
                removed = base_set - head_set
                for field_name in sorted(removed):
                    hits.append(
                        SignalDetail(
                            signal="C",
                            kind="domain_field_removed",
                            file=matched,
                            extra={
                                "class": cls,
                                "field": field_name,
                                "rationale": "frozen dataclass field removed (set-diff)",
                            },
                        )
                    )
    return hits


def signal_d_layer_crossing() -> tuple[list[SignalDetail], str]:
    """Run lint-imports; return (hits, availability) where availability is
    ``available`` or ``unavailable``. A signal only fires when lint-imports
    actually ran and reported a contract violation; uv-level failures (no
    pyproject, tool not installed, env error) map to ``unavailable``.
    """

    result = subprocess.run(
        ["uv", "run", "lint-imports"], capture_output=True, text=True, check=False
    )
    if result.returncode == 0:
        return [], "available"

    stderr = result.stderr or ""
    stdout = result.stdout or ""
    # uv-level / environment failures: the tool itself didn't run.
    unavailable_markers = (
        "Failed to spawn",  # uv couldn't find lint-imports
        "No `pyproject.toml`",  # uv couldn't find the project
        "No virtual environment",
        "command not found",
        "No such file or directory",
    )
    if any(marker in stderr for marker in unavailable_markers):
        return [], "unavailable"

    # Be conservative: only treat as a violation when the output looks like
    # lint-imports' own contract report. lint-imports prints "Contracts" and
    # either "KEPT" or "BROKEN" in its summary.
    looks_like_lint_imports = "Contracts" in stdout or "BROKEN" in stdout
    if not looks_like_lint_imports:
        return [], "unavailable"

    return (
        [
            SignalDetail(
                signal="D",
                kind="layer_crossing",
                file="(repo-wide)",
                extra={"rationale": "lint-imports reported contract violation"},
            )
        ],
        "available",
    )


def signal_e_public_name_removal(ctx: SignalContext) -> list[SignalDetail]:
    hits: list[SignalDetail] = []
    for _status, path in ctx.files:
        if not path.endswith(".py"):
            continue
        base_src = file_at(ctx.baseline, path)
        head_src = file_at(ctx.head, path)
        if base_src is None or head_src is None:
            continue
        base_mod = parse_module(base_src)
        head_mod = parse_module(head_src)
        if not base_mod or not head_mod:
            continue
        base_names = public_names(base_mod)
        head_names = public_names(head_mod)
        removed = base_names - head_names
        added = head_names - base_names
        # Pure rename heuristic: equal-count + at-least-one-added counts as rename.
        # We treat "added.len >= removed.len" as evidence of likely renames.
        if added and len(added) >= len(removed):
            continue
        for name in sorted(removed):
            hits.append(
                SignalDetail(
                    signal="E",
                    kind="public_name_removed",
                    file=path,
                    extra={
                        "name": name,
                        "rationale": "public name removed, no equivalent added",
                    },
                )
            )
    return hits


def signal_f_assertion_regression(ctx: SignalContext) -> list[SignalDetail]:
    hits: list[SignalDetail] = []
    for _status, path in ctx.files:
        if not path.endswith(".py"):
            continue
        if "test" not in Path(path).name and "/tests/" not in path:
            continue
        base_src = file_at(ctx.baseline, path) or ""
        head_src = file_at(ctx.head, path) or ""
        base_count = assertion_count(base_src)
        head_count = assertion_count(head_src)
        if head_count < base_count:
            hits.append(
                SignalDetail(
                    signal="F",
                    kind="assertion_regression",
                    file=path,
                    extra={
                        "baseline_assertions": str(base_count),
                        "head_assertions": str(head_count),
                    },
                )
            )
    return hits


# ---------- recommendation ---------------------------------------------------


def derive_recommendation(
    signals: list[str],
    rig: RigConfig,
    files_changed: int,
    lines_added: int,
    edits_bodies: bool,
) -> str:
    if not rig.present:
        return "human_required"
    if signals:
        return "human_required"
    if (
        files_changed <= GLANCE_MAX_FILES
        and lines_added <= GLANCE_MAX_LINES_ADDED
        and not edits_bodies
    ):
        return "glance_merge"
    return "review_encouraged"


# ---------- main -------------------------------------------------------------


def run(baseline: str, head: str, rig_config_path: Path) -> dict[str, object]:
    rig = load_rig_config(rig_config_path)
    files = changed_files(baseline, head)
    lines_added, lines_removed = numstat(baseline, head)
    edits_bodies = edits_existing_function_bodies(baseline, head)
    ctx = SignalContext(files=files, rig=rig, baseline=baseline, head=head)

    details: list[SignalDetail] = []
    if not rig.present:
        details.append(
            SignalDetail(
                signal="MISSING_CONFIG",
                kind="rig_config_missing",
                file=str(rig_config_path),
                extra={
                    "rationale": "rig-config not found; defaulting to human_required",
                },
            )
        )

    details += signal_a_sensitive_files(ctx)
    details += signal_b_protocol_signatures(ctx)
    details += signal_c_domain_fields(ctx)
    d_hits, d_availability = signal_d_layer_crossing()
    details += d_hits
    details += signal_e_public_name_removal(ctx)
    details += signal_f_assertion_regression(ctx)

    fired = sorted({d.signal for d in details if d.signal != "MISSING_CONFIG"})
    if not rig.present:
        fired = ["MISSING_CONFIG"]

    recommendation = derive_recommendation(fired, rig, len(files), lines_added, edits_bodies)

    return {
        "version": SCHEMA_VERSION,
        "baseline_sha": baseline,
        "head_sha": head,
        "rig_config": {
            "path": str(rig.path),
            "present": rig.present,
            "sensitive_files_count": len(rig.sensitive_files),
            "domain_model_files_count": len(rig.domain_model_files),
            "protocol_modules_count": len(rig.protocol_modules),
        },
        "signals": fired,
        "details": [d.to_json() for d in details],
        "diff_stats": {
            "files_changed": len(files),
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "edits_existing_function_bodies": edits_bodies,
        },
        "recommendation": recommendation,
        "tool_availability": {"lint_imports": d_availability},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("baseline", help="baseline commit-ish (e.g., merge-base SHA)")
    parser.add_argument("head", help="head commit-ish (e.g., HEAD or branch tip SHA)")
    parser.add_argument(
        "--rig-config", required=True, type=Path, help="path to rig architecture.toml"
    )
    args = parser.parse_args(argv)

    try:
        report = run(args.baseline, args.head, args.rig_config)
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc), "version": SCHEMA_VERSION}), file=sys.stdout)
        return 2

    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    print()  # trailing newline
    return 0


if __name__ == "__main__":
    sys.exit(main())
