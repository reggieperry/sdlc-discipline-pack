"""Content guards for the rig-root hijack fixes (#223).

The rig ROOT must never be left on a `feature/*` branch — it breaks `git pull`
and makes the zombie-reconciler refuse to commit archives. A deep-reason +
2-adversary diagnosis found two pack-side contributors: the worker's
rebase-iteration path running a checkout in the ambient cwd (so a failed `cd`
to the worktree lands the feature-branch checkout on the rig root), and the
zombie-reconciler silently leaving archives uncommitted when the rig is
off-main. These guards pin the fixes at the file-content level — the worker
template is LLM-executed (no runtime to drive), so a content assertion is the
available regression surface. Stdlib-only, matching the surrounding tests.
"""

from __future__ import annotations

import unittest
from pathlib import Path

_PACK_ROOT = Path(__file__).resolve().parents[3]
_WORKER_TEMPLATE = _PACK_ROOT / "agents" / "worker" / "prompt.template.md"
_RECONCILER = _PACK_ROOT / "assets" / "scripts" / "sdlc-zombie-reconciler.sh"
_MOL_WORK = _PACK_ROOT / "formulas" / "mol-sdlc-work.toml"
# Pack #226 moved workspace-setup (and its #223 WORKTREE_PATH binding) into
# the planner formula; the worker formula's workspace-resume keeps the
# RIG_ROOT guard.
_MOL_PLAN = _PACK_ROOT / "formulas" / "mol-sdlc-plan.toml"


class WorkerRebaseCheckoutTests(unittest.TestCase):
    """The worker's rebase-iteration checkout/reset must be worktree-scoped."""

    def test_rebase_checkout_targets_the_worktree(self) -> None:
        text = _WORKER_TEMPLATE.read_text(encoding="utf-8")
        self.assertNotIn(
            'git checkout "$BRANCH"',
            text,
            'bare `git checkout "$BRANCH"` runs in the ambient cwd and can land on '
            'the rig root; use `git -C "$WORKTREE" checkout "$BRANCH"`',
        )
        self.assertIn('git -C "$WORKTREE" checkout "$BRANCH"', text)

    def test_rebase_reset_targets_the_worktree(self) -> None:
        text = _WORKER_TEMPLATE.read_text(encoding="utf-8")
        self.assertNotIn(
            'git reset --hard "origin/$BRANCH"',
            text,
            "bare `git reset --hard` runs in the ambient cwd; use "
            '`git -C "$WORKTREE" reset --hard "origin/$BRANCH"`',
        )
        self.assertIn('git -C "$WORKTREE" reset --hard "origin/$BRANCH"', text)


class ReconcilerOffMainNotifyTests(unittest.TestCase):
    """When the rig is off the default branch the reconciler notifies, not just logs."""

    def test_off_main_path_emits_a_notification(self) -> None:
        text = _RECONCILER.read_text(encoding="utf-8")
        self.assertIn("is off the default branch", text)


class MolWorkRigRootBindingTests(unittest.TestCase):
    """The formulas assign RIG_ROOT; the planner's workspace-setup binds WORKTREE_PATH on both branches."""

    def test_rig_root_is_assigned_from_gc_rig_root(self) -> None:
        for formula in (_MOL_WORK, _MOL_PLAN):
            text = formula.read_text(encoding="utf-8")
            self.assertRegex(text, r'RIG_ROOT="\$\{GC_RIG_ROOT')

    def test_worktree_path_bound_on_existing_worktree_branch(self) -> None:
        text = _MOL_PLAN.read_text(encoding="utf-8")
        self.assertIn('WORKTREE_PATH="$WORKTREE"', text)


if __name__ == "__main__":
    unittest.main()
