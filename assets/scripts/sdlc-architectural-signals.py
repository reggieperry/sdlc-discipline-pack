#!/usr/bin/env python3
"""sdlc-architectural-signals.py — detect architectural signals in a diff.

Seven signals (A-G). Most route ``recommendation = "human_required"``; the
mechanical sweep (G) routes ``glance_merge``, and Signal A's consequence depends
on substance once the rig opts in (see ``derive_recommendation``).

::

    Signal A   sensitive file delta            (file matches rig-config sensitive_files)
    Signal B   Protocol signature delta        (method sig changed on @runtime_checkable)
    Signal C   domain-model field delta        (frozen-dataclass field renamed or removed)
    Signal D   architectural layer crossing    (lint-imports contract violation)
    Signal E   public-name removal w/o rename  (name in __all__ removed, no equivalent added)
    Signal F   assertion-count regression      (test assertions decreased baseline → head)
    Signal G   mechanical sweep                (uniform 1+/1- YAML-key edits across specs)

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
import re
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
    # Issue #191 — Signal A consequence-split. A rig opts into substance-based
    # tiering by classifying (a subset of) its sensitive files. constant_files
    # hold tuned constants (a constant-RHS modification forces human_required);
    # algorithm_files hold domain math (an existing-body edit forces
    # human_required). Both default empty, in which case Signal A keeps its
    # unconditional human_required consequence — fully backward-compatible.
    constant_files: tuple[str, ...] = ()
    algorithm_files: tuple[str, ...] = ()


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
        constant_files=tuple(data.get("constant_files", ())),
        algorithm_files=tuple(data.get("algorithm_files", ())),
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


# ---------- Signal A consequence-split: substance detection ------------------
#
# Issue #191. A sensitive-file touch forces human_required only when it is
# *substantive* — a constant-RHS modification in a declared constant_files path,
# or an algorithm-body edit in a declared algorithm_files path. A purely
# structural-additive sensitive touch falls through to the size/G logic. The
# detection is conservative: when the per-file diff can't be retrieved, the
# helpers return True (the human_required-forcing answer).

# An UPPER_SNAKE_CASE assignment, optionally type-annotated (e.g. ``MAX_RISK:
# Final[float] = 0.02`` or ``SIX_PERCENT = 0.06``). The ``(:[^=]*)?`` swallows a
# ``: Final[...]`` annotation without consuming the assignment ``=``. Biased to
# over-match — over-matching errs toward human_required, the safe direction.
_CONSTANT_ASSIGN_RE = re.compile(r"^\s*[A-Z_][A-Z0-9_]*\s*(:[^=]*)?=")


def _file_diff(baseline: str, head: str, path: str) -> str | None:
    """Per-file unified diff (context-free). None if git fails."""

    try:
        return _git("diff", "--unified=0", f"{baseline}..{head}", "--", path)
    except RuntimeError:
        return None


def constant_rhs_modified(baseline: str, head: str, path: str) -> bool:
    """True if the per-file diff modifies an existing constant assignment.

    Detection keys on a *removed* (``-``) line matching the constant-assignment
    pattern: an RHS change emits the old value as a deletion under
    ``--unified=0``, whereas a purely-additive new constant emits only ``+``
    lines. Conservative — returns True when the diff can't be retrieved.
    """

    raw = _file_diff(baseline, head, path)
    if raw is None:
        return True
    for line in raw.splitlines():
        if line.startswith("---"):
            continue
        if line.startswith("-") and _CONSTANT_ASSIGN_RE.match(line[1:]):
            return True
    return False


def algorithm_body_edited(baseline: str, head: str, path: str) -> bool:
    """True if the per-file diff deletes or modifies an existing line.

    Same conservative body-edit heuristic as
    :func:`edits_existing_function_bodies`, scoped to one file. A pure append
    (only ``+`` lines) does not trigger. Returns True when the diff can't be
    retrieved.
    """

    raw = _file_diff(baseline, head, path)
    if raw is None:
        return True
    for line in raw.splitlines():
        if line.startswith("---"):
            continue
        if line.startswith("-"):
            return True
    return False


def sensitive_touch_is_substantive(ctx: SignalContext, baseline: str, head: str) -> bool:
    """True if any sensitive-file touch is substantive under the opt-in keys.

    A constant-RHS modification in a ``constant_files`` path, or an
    algorithm-body edit in an ``algorithm_files`` path. Only consulted when the
    rig has opted into substance-based tiering (issue #191); a sensitive file in
    neither list never contributes — it is treated as structural-additive.
    """

    rig = ctx.rig
    for _status, path in ctx.files:
        if any(fnmatch.fnmatch(path, pat) for pat in rig.constant_files):
            if constant_rhs_modified(baseline, head, path):
                return True
        if any(fnmatch.fnmatch(path, pat) for pat in rig.algorithm_files):
            if algorithm_body_edited(baseline, head, path):
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


SPEC_SWEEP_DIRS: tuple[str, ...] = ("stories/",)


def signal_g_mechanical_sweep(ctx: SignalContext) -> list[SignalDetail]:
    """Detect uniform same-shape edits across spec files.

    Fires when ALL of:

    - Every changed file is under a spec-shaped directory (today: ``stories/``).
    - There are at least 2 changed files (one file is not a "sweep").
    - Every file is a modification (not an addition or deletion).
    - Every file's diff has exactly one ``+`` line and exactly one ``-`` line
      (uniform 1+/1- substitution; no inserted blanks, no reflows).
    - All ``-`` lines and all ``+`` lines target the same YAML key (the
      substring before the first ``:``); the value can differ across files.

    Empirical anchor: PR #247 (STRESS-07) on Elder, 2026-05-17 — 19
    ``stories/*.md`` files, each 1+/1- on the ``status:`` line. Today's
    reviewer correctly identified the shape but justified the tier via the
    fragile ``edits_existing_function_bodies`` heuristic. Naming the
    pattern as its own signal makes the right behavior intentional rather
    than accidental.

    When this signal fires, ``derive_recommendation`` routes to
    ``glance_merge`` (post-issue-#191, which removed the review_encouraged
    tier this used to route to) — sensitive-file touch (Signal A) still
    wins back to ``human_required`` if it also fires. The stories.py
    validate gate (pack #75) catches the one failure mode that mattered
    for auto-merging a sweep (a YAML-status typo).
    """

    if len(ctx.files) < 2:
        return []

    if not all(path.startswith(SPEC_SWEEP_DIRS) for _status, path in ctx.files):
        return []

    yaml_key: str | None = None
    for status, path in ctx.files:
        # Only pure modifications count; new/deleted files break the sweep shape.
        if status != "M":
            return []

        diff = _git("diff", "--unified=0", f"{ctx.baseline}..{ctx.head}", "--", path)
        plus_lines: list[str] = []
        minus_lines: list[str] = []
        for line in diff.splitlines():
            if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
                continue
            if line.startswith("+"):
                plus_lines.append(line[1:])
            elif line.startswith("-"):
                minus_lines.append(line[1:])

        if len(plus_lines) != 1 or len(minus_lines) != 1:
            return []

        # Extract YAML key — substring before the first ``:`` after stripping
        # leading whitespace. Non-YAML edits (no colon) disqualify the sweep.
        minus_text = minus_lines[0].lstrip()
        plus_text = plus_lines[0].lstrip()
        if ":" not in minus_text or ":" not in plus_text:
            return []
        rkey = minus_text.split(":", 1)[0].strip()
        akey = plus_text.split(":", 1)[0].strip()
        if rkey != akey:
            return []  # not a same-key substitution within this file

        if yaml_key is None:
            yaml_key = rkey
        elif yaml_key != rkey:
            return []  # key differs across files

    return [
        SignalDetail(
            signal="G",
            kind="mechanical_sweep",
            file="(multiple)",
            extra={
                "files_count": str(len(ctx.files)),
                "yaml_key": yaml_key or "",
                "rationale": (
                    "all hunks are uniform 1+/1- edits on the same YAML key "
                    "across spec files; routed to glance_merge rather than "
                    "human_required (issue #191 two-tier model)"
                ),
            },
        )
    ]


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
    *,
    sensitive_opted_in: bool = False,
    sensitive_substantive: bool = False,
) -> str:
    # Two-tier model (issue #191): glance_merge | human_required. The middle
    # review_encouraged tier was removed — it was the residual fallthrough
    # bucket, never gated a fix or a rejection across the rig's history, and
    # the 24h delayed-merge buffer it was meant to drive never fired. The
    # reviewer phase still runs on every PR and produces the same findings;
    # only the parking label is gone.
    if not rig.present:
        return "human_required"
    # Signal A consequence-split (issue #191). When the rig has NOT opted into
    # substance-based tiering (constant_files / algorithm_files both empty), a
    # sensitive-file touch forces human_required unconditionally — backward-
    # compatible with the file-level flag. When opted in, only a *substantive*
    # touch (a constant-RHS modification or an algorithm-body edit) forces
    # human_required; a purely structural-additive sensitive touch falls through
    # to the size/G logic below.
    if "A" in signals and (not sensitive_opted_in or sensitive_substantive):
        return "human_required"
    # Mechanical sweep (Signal G) — uniform 1+/1- same-YAML-key edits across
    # spec files — is the lowest-risk diff shape the gate recognizes. Post-
    # collapse it routes to glance_merge; the stories.py validate gate
    # (pack #75) catches the one failure mode that mattered (a YAML-status
    # typo). See signal_g_mechanical_sweep.
    if "G" in signals:
        return "glance_merge"
    # Any other architectural signal forces human_required. A and G are excluded:
    # A here can only be the opted-in structural-additive case (it would have
    # returned above otherwise), which is permitted to fall through; G returned
    # above. Every other signal (B–F) still gates.
    if [s for s in signals if s not in ("A", "G")]:
        return "human_required"
    if (
        files_changed <= GLANCE_MAX_FILES
        and lines_added <= GLANCE_MAX_LINES_ADDED
        and not edits_bodies
    ):
        return "glance_merge"
    # Residual fallthrough — no signal, but large or body-editing. Used to be
    # review_encouraged; now human_required. The operator merged these by hand
    # all along; the tier just named the park honestly.
    return "human_required"


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
    details += signal_g_mechanical_sweep(ctx)

    fired = sorted({d.signal for d in details if d.signal != "MISSING_CONFIG"})
    if not rig.present:
        fired = ["MISSING_CONFIG"]

    # Signal A consequence-split (issue #191). opted_in is true only when the rig
    # classifies some sensitive files as constants or algorithms; otherwise the
    # split is inert and Signal A keeps its unconditional human_required path.
    sensitive_opted_in = bool(rig.constant_files or rig.algorithm_files)
    sensitive_substantive = sensitive_opted_in and sensitive_touch_is_substantive(
        ctx, baseline, head
    )

    recommendation = derive_recommendation(
        fired,
        rig,
        len(files),
        lines_added,
        edits_bodies,
        sensitive_opted_in=sensitive_opted_in,
        sensitive_substantive=sensitive_substantive,
    )

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
            "constant_files_count": len(rig.constant_files),
            "algorithm_files_count": len(rig.algorithm_files),
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
