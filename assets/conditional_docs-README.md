# Conditional docs — auto-loading per-feature documentation

This directory hosts compact pointers that Claude Code loads conditionally based on the file paths a session is editing or the topics it's discussing. The pack's documenter writes a registry entry here per shipped feature, alongside the full feature doc at `docs/features/feature-<story_id>-<slug>.md`.

Each entry is small on purpose. It names the full doc, a few conditions for when the agent should load it, and nothing else. The conditions are matched against file paths and conversation topics — narrow conditions reduce false-positive auto-loads.

## Format

```markdown
- `docs/features/feature-<story_id>-<slug>.md`
  - Conditions:
    - When working with <feature area>
    - When modifying <related module>
    - When questions arise about <specific decision>
```

## Operator notes

- The file naming convention `feature-<story_id>-<slug>.md` is what the documenter uses; you can also drop hand-authored entries with any filename so long as they follow the format above.
- This README is part of the pack's `claude-defaults` bootstrap tarball. It explains the convention; remove it once the directory has real entries.
