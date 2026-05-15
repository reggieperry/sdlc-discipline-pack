---
paths:
  - "**/*.py"
---
# Security standards

Discipline for writing code that resists the failure modes cataloged by OWASP Top 10:2025, the OpenSSF Python Secure Coding Guide (2026), and OWASP LLM Top 10:2025. Bandit catches mechanical patterns at the tester phase; this rule covers the *why* and the logic-level patterns Bandit cannot reason about.

## Trust boundaries

- Validate at every trust boundary. Data crossing from outside the trust zone (HTTP request, file, env var, IPC, external API, LLM output) is parsed into a typed structure before any logic touches it. Downstream code consumes the typed object, never the raw input. (CWE-20)
- Fail closed. Error paths default to deny, not allow. Authorization checks must affirmatively grant; absence is denial. (CWE-755)
- Least privilege. Code requests the minimum capability it needs. Use the pipeline's capability primitive (`StageCoordinator` permission scope) rather than ambient access. (CWE-272)
- Resource limits. Any externally-triggerable I/O, computation, or LLM call has an explicit bound: timeout, byte cap, token cap. (CWE-400)

## Secrets

- No secrets in source. Environment variables only — never hard-code keys, tokens, or passwords. (CWE-798)
- Never log secret values. Sanitize before logging. Watch for accidental exposure via `repr()` or `str()` on credentialed objects. (CWE-532)

## Databases

- Parameterized queries only. Never f-string or `.format()` into SQL — use the driver's parameter substitution (`psycopg`'s `%s`, SQLAlchemy bound parameters, etc.). (CWE-89)

## Python anti-patterns

Bandit catches these mechanically at the tester phase. The rule explains why.

- No `eval`, `exec`, or `compile` on untrusted input. If you need dynamic dispatch, use a dict of callables. (CWE-94, bandit B102)
- `subprocess` with list args, never `shell=True` on untrusted data. (CWE-78, bandit B602–B609)
- `yaml.safe_load`, not `yaml.load`. (CWE-502, bandit B506)
- `defusedxml` for untrusted XML, never stdlib `xml`. (CWE-611)
- `tempfile.mkstemp`, not `tempfile.mktemp`. (CWE-377, bandit B306)
- SSL verification always on. Never `verify=False`, never `ssl.CERT_NONE`. (CWE-295, bandit B501–B504, B507)
- No `pickle` for data crossing a trust boundary. Use JSON or msgpack. Arrow IPC for process-pool boundaries within a trust zone. (CWE-502)
- `assert` is for tests, not production checks. `python -O` strips assertions. Raise an explicit exception instead. (CWE-617, bandit B101)
- No broad `except Exception:` that silences errors. Catch what you can handle; re-raise the rest. (CWE-396; see also `tdd.md`)

## Cryptography

- Randomness for security: `secrets` module, never `random`. (CWE-330, bandit B311)
- Hashing for security: SHA-256 or stronger, never MD5 or SHA-1. (CWE-327, bandit B303 for direct `hashlib.md5/sha1`, B324 for `hashlib.new("md5", ...)`)
- Token comparison: `secrets.compare_digest`, never `==`. (CWE-208 — timing attack)
- Password storage: `bcrypt` or `argon2`, never plain hash. (CWE-916)

## LLM applications

From OWASP LLM Top 10:2025. These apply wherever the codebase calls an LLM or processes LLM-emitted data.

- LLM output is untrusted data. Validate against a schema before acting on it. Never `eval`/`exec`/auto-run LLM-emitted code. Never pass LLM output directly into SQL, shell, or file paths. (LLM05 Improper Output Handling)
- External content fed *into* the LLM is a potential injection vector. If you pass a fetched web page, document, or user note into a prompt, the content can contain instructions. Use system-prompt hardening and constrained output formats. (LLM01 Prompt Injection)
- Bound LLM agency. Any LLM call that *acts* on the world (places an order, modifies state) must be capability-scoped and audited. (LLM06 Excessive Agency)
- Audit trail for LLM-driven decisions. Every LLM call that affects production state writes a row to a durable audit log naming the model, prompt hash, response hash, and resulting action. (LLM07 adjacent)
- Cost and token budgets enforced. No unbounded LLM call — the pipeline's budget tracker is the enforcement mechanism. (LLM10 Unbounded Consumption)

## Worker discipline at write time

When writing code that consumes external data:

1. Define a typed dataclass for the validated input shape.
2. Write the parser / validator first, with tests.
3. Make the consuming function take the dataclass, not the raw input.
4. The boundary between raw and validated is the boundary between unsafe and safe.

When writing code that calls an LLM:

1. Schema for the response (JSON shape, allowed values).
2. Validator that rejects malformed responses.
3. Bounded budget at the call site.
4. Audit log entry on every action taken from the response.

## What this rule does NOT cover

- Bandit's mechanical patterns themselves — those run at the tester phase. This rule explains the rules' rationale; Bandit enforces them at output time.
- Rig-specific concerns (broker credentials, money values, capability-gate framework, etc.) — those live in the rig's own overlay rules under `.claude/rules/project/`.
- Operational secrets management (Vault, AWS Secrets Manager, etc.) — out of scope for code-level discipline.

## References

- `python.md` — general Python conventions; the `except Exception:` and assert-in-production guidance there aligns with this rule's security framing.
- `tdd.md` — testing discipline; security-critical code must carry property tests.
- `architecture-config.md` — `sensitive_files` complements but does not replace this rule; the routing protects specific files, while this rule applies to all Python edits.
- OWASP Top 10:2025 — https://owasp.org/Top10/2025/
- OpenSSF Secure Coding Guide for Python — https://best.openssf.org/Secure-Coding-Guide-for-Python/
- OWASP LLM Top 10:2025 — https://owasp.org/www-project-top-10-for-large-language-model-applications/
- Bandit documentation — https://bandit.readthedocs.io/
