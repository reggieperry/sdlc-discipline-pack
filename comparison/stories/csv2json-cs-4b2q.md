# csv2json: --tab as a shorthand for --delimiter $'\t'

Original v1.3 final-validation rerun story. Bead `cs-4b2q` on rig `csv2json`.
Filed: 2026-05-09T17:24:18Z. Closed: 2026-05-09T17:49:37Z. PR #15 merged 2026-05-09T17:55:08Z.

---

## Outcome

Users can pass `--tab` as a shorthand for `--delimiter $'\t'`, so reading a TSV file from a shell where escaping `\t` is awkward becomes ergonomic.

## Acceptance criteria

- [ ] `--tab` flag exists in the CLI and appears in `--help`.
- [ ] Without the flag, current behavior is preserved (existing tests continue to pass).
- [ ] With the flag, the parser uses tab as the field delimiter and emits the same JSON the equivalent `--delimiter $'\t'` invocation would.
- [ ] `--tab` and `--delimiter` together is treated as a usage error (the flags are mutually exclusive); exits non-zero with a stderr message naming the conflict.

## Scope

**In:** csv2json CLI surface, one new Click option, mutual-exclusion check, tests for the new option.
**Out:** other delimiter shortcuts (`--pipe`, `--semicolon`, etc.); `--delimiter` semantics changes; auto-detection of TSV input.

## Sensitive files

None.

## Notes

This is a v1.3 final-validation re-run of the sdlc-discipline pack with the gascity#1893 workaround in main. The chain should reset its phase-agent sessions in the kickoff worker before reassigning the story, then run end-to-end on a feature branch that was cut by the operator before slinging.
