---
paths:
  - "**/*.py"
---

# Python security

The defenses for a Python system that shells out to subprocesses, parses untrusted JSON/YAML/XML and tool output, persists to a database, makes outbound and model calls, runs schema migrations, and extracts archives. Untrusted-input validation, command and SQL injection, insecure deserialization, path traversal, weak crypto and secret handling, error-message leakage, and the model-output boundary are the load-bearing classes, but this is a general Python-security rule. Sources, all primary: the bandit rule catalog (`PyCQA/bandit`, with every CWE here read from bandit's own plugin pages and its `issue.Cwe` map), the CPython stdlib security docs (`subprocess`, `pickle`, `yaml`/PyYAML, `xml`/`defusedxml`, `sqlite3`, `secrets`, `hmac`, `hashlib`, `ssl`, `tempfile`, `tarfile`), the PyPA supply-chain tooling (`pip-audit`, the Python Packaging Advisory Database, OSV, pip hash-checking mode), the OWASP Cheat Sheet Series, OWASP Top 10:2025, the OpenSSF Secure Coding Guide for Python, OWASP LLM Top 10:2025, and the CISA/MITRE 2024 CWE Top 25 — corroborated by ruff's `flake8-bandit` S-rules and the Snyk, Semgrep, and Datadog Python cheat sheets.

> This rule is the Python mechanics of `craft-tdd`'s "security-critical code carries property tests" and the language-neutral trust-boundary discipline. See `python-llm.md` for the bounded structured-output schema as the typed model boundary — this rule is the input side of it (treat model output as untrusted, validate at the schema, feed back sanitized secret-free errors); `python-types.md` for runtime-enforced validation (pydantic over unenforced dataclass/`NamedTuple` hints) so bad input can't construct a bad value; `python-errors.md` for never swallowing an exception (bandit B110/B112, CWE-703) and never leaking secrets into messages; `python-concurrency.md` for the timeout on every external call (CWE-400); `python-modules.md` for pinning and hash-verifying dependencies; `python-testing.md` for fuzzing the parsing boundary; `architecture-config.md` for the `sensitive_files` routing that complements (does not replace) this rule; and `python-style.md` for the idiom baseline these sit on.

## Run the security toolchain in the gate

- **Run `bandit -r <pkg>` as the static-security linter and fail on findings** — treat a new bandit finding the way the differential gate treats a new lint finding: a regression to fix, not to `# nosec`. bandit is the canonical Python security catalog organized by test-ID prefix (B1xx misc, B2xx framework/app, B3xx blacklisted calls, B4xx blacklisted imports, B5xx crypto, B6xx injection, B7xx templating/XSS); the rules below are keyed to its Bxxx and the CWE bandit assigns. bandit catches the mechanical patterns; this rule covers the *why* and the logic-level patterns bandit cannot reason about.
- **Run `pip-audit` as the dependency scanner in the same gate and fail on findings** — maintained by PyPA, it scans the declared and installed tree against the Python Packaging Advisory Database and OSV; `--strict` fails on an unresolvable dependency. `safety` is a corroborating alternative. Review before bumping — an unreviewed dependency bump can pull in malicious code. (`python-modules.md`.)
- **Pin dependencies with hashes and install in hash-checking mode** — per-requirement `--hash=sha256:...` lines plus `pip install --require-hashes` (generate with `pip-compile --generate-hashes` or `uv`). pip's hash-checking mode "protect[s] against remote tampering" and is all-or-nothing, forcing every transitive dependency pinned with `==`. Set the internal index with `--index-url`, not `--extra-index-url`, so a higher-versioned public package can't shadow an internal name (dependency confusion). (`python-modules.md`.)
- **Run ruff's `flake8-bandit` `S` rules in-editor** — a fast Rust reimplementation of bandit (S102 exec, S104 bind-all, S105–S107 hardcoded password, S301 pickle, S324 hashlib, S506 yaml, S602 subprocess-shell, S608 SQL). Enable the `S` rule group so the same checks fire on every save before the bandit gate.

## Trust boundaries and untrusted input (the boundary that governs the rest)

- **Validate at every trust boundary into a typed structure before any logic touches it.** Data crossing from outside the trust zone — an HTTP request, file, env var, IPC, external API, subprocess stdout, or model output — is parsed into a runtime-enforced schema (a pydantic model, since stdlib `dataclass`/`NamedTuple` hints are not enforced at runtime) before it reaches any sink. Downstream code consumes the typed object, never the raw input, so a raw dict can't flow inward. Then never interpolate that output into a shell command, a SQL string, a file path, an `eval`/`exec`, or an unescaped template. This is the input side of the boundary `python-llm.md` applies at the structured-output schema (OWASP LLM05 Improper Output Handling; CWE-20, CWE-94).
- **Fail closed.** Error paths default to deny, not allow. Authorization checks must affirmatively grant; absence of a grant is denial. (CWE-755.)
- **Least privilege.** Code requests the minimum capability it needs. Prefer a scoped capability primitive (a permission scope passed in) over ambient access. (CWE-272.)
- **Bound every externally-triggerable resource.** Any externally-reachable I/O, computation, decode, or model call carries an explicit bound — a timeout, a byte cap, a token cap — against resource exhaustion (CWE-400). Cap the decode size and reject oversized input before parsing.
- **Bound string lengths and array sizes at the trust boundary.** Any string field crossing into the system from a request, document, or model response carries an explicit length cap; without it, structured-output mode guarantees shape but not size, and a hallucinated or adversarial response lands verbatim in storage. Cap arrays at the schema layer too — every untrusted array carries an explicit element-count limit, and the consumer re-checks defensively.
- **Bound the fan-out.** Any concurrent batch whose size depends on request input runs behind a semaphore; long-running orchestrators carry a module-level cap on concurrent runs. Unbounded fan-out × paid external calls = cost amplification and gateway denial-of-service. (`python-concurrency.md`.)
- **Never use `assert` for a security or runtime invariant.** `python -O`/`-OO` strips every `assert` from the optimized bytecode, so an assert-based input-validation or auth check silently vanishes in production — `raise ValueError(...)`/`PermissionError(...)` instead. bandit B101 (ruff S101, CWE-617/CWE-703) flags asserts outside test files, where they remain legitimate.
- **Don't swallow exceptions with a bare `except: pass`, `except: continue`, or a broad `except Exception:` that silences errors.** A silenced exception hides a failed security check or corrupted state — catch the specific exception you can handle, log it, and handle or re-raise the rest. bandit B110 (`try_except_pass`) and B112 (`try_except_continue`) map to CWE-703; the broad-silence pattern maps to CWE-396. (`python-errors.md`.)

## Command execution and subprocess (bandit B602–B607 / CWE-78)

- **Pass arguments as a list to `subprocess.run`/`Popen` and leave `shell=False` (the default); never build a shell string.** `subprocess.run(["git", "commit", "-m", msg])` — the module does not implicitly invoke a system shell, so all characters including shell metacharacters pass to the child as data. With `shell=True` the docs make it "the application's responsibility to ensure that all whitespace and metacharacters are quoted" (subprocess Security Considerations). bandit B602 escalates from low to high severity as the command goes from a static string to a string-built one; B604/B605 flag `os.system`/`os.popen` and any helper called with `shell=True`. OS command injection (CWE-78) is #7 in the 2024 CWE Top 25.
- **Keep the executable a fixed constant with a resolved path; never let the program name be user-controlled.** bandit B607 (`start_process_with_partial_path`, CWE-426 untrusted search path) flags a partial path that lets `PATH` be hijacked — pass an absolute path or resolve via `shutil.which`. Put a `timeout=` on every shell-out so a hung child can't wedge the process (CWE-400). (`python-concurrency.md`.)
- **When a shell is genuinely unavoidable, escape every interpolated value with `shlex.quote()`** — but the subprocess docs frame it as a fallback, not a guarantee; the list form is the safe target. Never feed model output into a shell string at all.
- **Never `eval`/`exec`/`compile` input derived from an untrusted source.** Both run arbitrary Python — bandit B307 (`eval`) and B102 (`exec_used`) map to code injection (CWE-94, #11 in the 2024 CWE Top 25). To turn a string into a Python literal use `ast.literal_eval()`, which evaluates only literals and cannot call functions; for dynamic dispatch use an explicit dict of callables / allowlist, not `eval` of a name.

## Deserialization and parsing (CWE-502)

- **Never unpickle data from an untrusted or tamperable source.** The pickle docs warn at the top: "The pickle module is not secure. Only unpickle data you trust" — malicious pickle data "will execute arbitrary code during unpickling." Use `json` (or `msgpack`) for data crossing a trust boundary; for a binary IPC channel within a single trust zone, a format like Arrow IPC is appropriate. If a binary Python-object format must round-trip across a boundary, sign it with `hmac` and verify with `hmac.compare_digest` before unpickling. The same applies to `marshal` (B302), `shelve` (pickle-backed), `dill`, and `pandas.read_pickle`; bandit B301/B302/B403 map to CWE-502 (#16 in the 2024 CWE Top 25). For ML weights prefer `safetensors` over `torch.load` (B614).
- **Parse untrusted YAML with `yaml.safe_load()` / `SafeLoader`, never `yaml.load()` with the default or `FullLoader`.** PyYAML's docs: "It is not safe to call `yaml.load` with any data received from an untrusted source… as powerful as `pickle.load` and so may call any Python function" via `!!python/object` tags; `safe_load` "limits this ability to simple Python objects." bandit B506 (`yaml_load`) maps to CWE-502 and names `yaml.safe_load` as the fix.
- **Parse untrusted XML with `defusedxml`, never the stdlib `xml.*` parsers.** Use the drop-in replacements (`from defusedxml.ElementTree import parse`) or call `defusedxml.defuse_stdlib()`. The Python XML-vulnerabilities section warns the stdlib parsers "are not secure against maliciously constructed data" — billion-laughs and quadratic-blowup entity expansion, external-entity expansion (XXE giving local-file read and network access), DTD retrieval, and decompression bombs (bandit B313–B320, CWE-611). Keep `expat` ≥ 2.6.0 for CVE-2023-52425 (large-token quadratic runtime).

## Injection — SQL and template (CWE-89 #3, CWE-79 #1 in the 2024 CWE Top 25)

- **Build SQL with DB-API parameter substitution, never Python string operations.** Pass values as the second argument to `cursor.execute()`/`executemany()` with `?`/`:name` (sqlite3) or `%s` (psycopg) placeholders, or use SQLAlchemy bound parameters — `cur.execute("SELECT * FROM orders WHERE symbol = ?", (sym,))` — so the driver sends statement and values separately. The sqlite3 docs show the exact `' OR TRUE; --` injection that f-strings, `%`, `.format()`, and `+` enable. bandit B608 (`hardcoded_sql_expressions`) maps to CWE-89.
- **Filter identifiers against an allow-list before interpolation.** Driver parameter substitution binds *values*, not *identifiers* — schema, table, and column names that come from request data must be validated against a fixed allow-list before they enter the statement. (CWE-89.)
- **In Django, parameterize `raw()`/`extra()`/`RawSQL`.** Querysets parameterize automatically; the risk is reintroduced by `Model.objects.raw()`/`extra()`/`RawSQL` with interpolated input — pass `params=` rather than building the string (bandit B610/B611, CWE-89).
- **Keep Jinja2 autoescaping on; never wrap untrusted data in `Markup`/`|safe`/`mark_safe`.** Construct `Environment`s with `autoescape=select_autoescape()`; raw Jinja2 defaults to off, which bandit B701 (`jinja2_autoescape_false`, CWE-94) flags. Flask's `render_template()` already autoescapes `.html`/`.xml` — the footgun is a hand-built `Environment`, or reintroducing XSS via `markupsafe.Markup`, the `|safe` filter, or Django `mark_safe` (B703/B308/B704). XSS (CWE-79) is #1 in the 2024 CWE Top 25.

## Path traversal and archives (CWE-22 #5)

- **Confine an externally-supplied filename under a base directory before opening it.** Resolve with `Path(base, name).resolve()` and verify `base` is a parent — `os.path.commonpath([base, resolved]) == base` — before opening; reject absolute paths and `..` components, restrict to an allowed character subset, and prefer a server-generated name (UUID) over a user-supplied one (OWASP File Upload Cheat Sheet; CWE-22 is #5 in the 2024 CWE Top 25).
- **Extract untrusted tar archives only with the data extraction filter.** Pass `filter="data"` to `TarFile.extractall()`/`extract()` (Python 3.12+): it refuses absolute paths, `..` escapes, symlinks pointing outside the destination, and device files, and clamps permissions — closing the 20-year-old CVE-2007-4559 traversal class (bandit B202, CWE-22). `"data"` became the default in 3.14; on 3.12/3.13 pass it explicitly. For zip, validate each member name resolves under the destination before extracting (`zipfile` has no equivalent filter). "Never extract archives from untrusted sources without prior inspection" (tarfile docs).
- **Create temp files atomically with `tempfile`, never a hardcoded `/tmp` path or `tempfile.mktemp()`.** `mkstemp()`/`NamedTemporaryFile()`/`TemporaryFile()` for files and `mkdtemp()`/`TemporaryDirectory()` for dirs create in one step with `O_EXCL` and an unpredictable name. `tempfile.mktemp()` is deprecated (bandit B306, CWE-377): "a different process may create a file with this name in the time between the call to `mktemp()` and the subsequent attempt to create the file" — a TOCTOU symlink race. Never hardcode `/tmp/foo` (bandit B108, CWE-377).
- **Never decompress an untrusted gzip/zip/zlib stream unbounded.** For gzip/zlib read with a capped chunk count and a running output-size ceiling; for `zipfile` inspect `ZipInfo.file_size`/`compress_size` and the compression ratio before extracting, and bound total extracted bytes. A few-KB archive can expand to gigabytes (CWE-409 decompression bomb).

## Cryptography and secrets (bandit B3xx blacklist + B5xx crypto)

- **Use the `secrets` module for tokens, keys, salts, nonces, and session IDs; never `random`.** `secrets.token_bytes`/`token_hex`/`token_urlsafe`/`choice`/`randbelow` generate cryptographically strong values; `random`'s Mersenne Twister "is designed for modelling and simulation, not security or cryptography" (the secrets docs) and is predictable from observed output. bandit B311 maps to CWE-330.
- **Compare secrets, MACs, tokens, and digests with `hmac.compare_digest()`, never `==`.** It "uses an approach designed to prevent timing analysis by avoiding content-based short-circuiting behaviour"; `==` returns early on the first differing byte, leaking length/prefix through timing (CWE-208). Re-exported as `secrets.compare_digest`.
- **Don't use MD5, MD4, or SHA-1 in a security context; use SHA-256 or better.** bandit B303/B324 (`hashlib`, CWE-327): "Use of weak MD4, MD5, or SHA1 hash for security. Consider `usedforsecurity=False`" — pass that keyword (Python 3.9+) only for a genuine non-security use (a cache key/checksum). For symmetric crypto avoid DES/ARC4/Blowfish/3DES and ECB mode (B304/B305, CWE-327); use an authenticated cipher from `pyca/cryptography` — `AESGCM` or `ChaCha20Poly1305` with a unique 12-byte nonce per message.
- **Store passwords only with a salted, memory-hard KDF — Argon2id first.** OWASP's first choice is Argon2id (minimum m=19 MiB, t=2, p=1) via `argon2-cffi`'s `PasswordHasher.hash`/`.verify`. Fallbacks: `scrypt` (N=2¹⁷, r=8, p=1; `hashlib.scrypt`), `bcrypt` (work factor ≥ 10, and enforce the 72-byte input limit because bcrypt truncates), or PBKDF2-HMAC-SHA256 (≥ 600,000 iterations; `hashlib.pbkdf2_hmac`) where FIPS is required. A fast general-purpose hash — even SHA-256 — is far too fast for password storage (CWE-916).
- **Never hardcode passwords, API keys, or tokens in source.** bandit B105/B106/B107 flag literal secrets at three call sites and map to the hardcoded-credentials class (CWE-259/CWE-798, in the 2024 CWE Top 25). Load secrets from the environment (`os.environ`) or an injected config/secret store, and read API keys at call time, never a literal.
- **Never log secret values, and watch for accidental exposure via `repr()`/`str()` on credentialed objects.** Sanitize before logging; keep secrets out of logs and exception messages, and pair with a pre-commit secret scanner. (CWE-532.)

## Error-message sanitization (CWE-209)

- **Error-message fields that flow out via an HTTP endpoint, audit log, or operator-visible surface carry only the exception class name (plus a correlation ID), never the exception's full string.** SDK exception strings carry sensitive content: gateway URLs, request bodies, DSNs, query parameters, retrieved document fragments. The full detail goes to a server-side logger with `exc_info`; the surfaced field carries just `type(exc).__name__`.
- **If operators need to correlate the surfaced message with the full log line, generate a correlation ID at error time and store *that* in the visible field.** Don't pipe the exception string through.

## TLS and transport (CWE-295)

- **Never pass `verify=False` to requests/httpx; keep certificate verification on (the default).** The requests docs: with `verify=False`, "requests will accept any TLS certificate presented by the server, and will ignore hostname mismatches and/or expired certificates, which will make your application vulnerable to man-in-the-middle (MitM) attacks." bandit B501 is High severity and maps to CWE-295. Never use `ssl._create_unverified_context()` (B323) or set `CERT_NONE`/`check_hostname=False` on a client context (B502–B504/B507).
- **Build TLS contexts with `ssl.create_default_context()`, not a hand-set old protocol.** It returns `CERT_REQUIRED` + `check_hostname` enabled, default CAs loaded, and SSLv2/SSLv3 disabled; `PROTOCOL_TLS_CLIENT` "enables `CERT_REQUIRED` and `check_hostname` by default." Don't pin an outdated protocol version (bandit B502/B503/B504, CWE-327); set `minimum_version` to TLS 1.2+.
- **Pass an explicit `timeout=` to every external call; assume the SDK default is wrong until you've checked.** "By default, requests do not time out unless a timeout value is set explicitly. Without a timeout, your code may hang for minutes or more" (the requests docs); default SDK timeouts are often measured in minutes. bandit B113 maps to CWE-400 (#24 in the 2024 CWE Top 25); `requests.get(url, timeout=5)`. Don't set `timeout=None`. Per-unit-of-work coroutines wrap the call with a deadline matching the operational SLA, not the SDK default. Cap retry attempts and add a cooldown gate so a doomed input doesn't loop forever consuming budget; the retry layer reuses the same idempotency key across attempts. (`python-concurrency.md`.)
- **Validate against SSRF before any outbound request built from untrusted input.** Don't accept full URLs from callers; allowlist the permitted host, then resolve the hostname and confirm the resolved IP is not private/internal with `ipaddress.ip_address(resolved).is_private` (rejecting 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8). Disable redirect-following (`allow_redirects=False`) so a redirect chain can't bypass validation, and don't return raw upstream errors (CWE-918).
- **When verifying a JWT, pass an explicit `algorithms` allowlist and never disable verification.** `jwt.decode(token, key, algorithms=["ES256"])` — PyJWT's docs: "Do not compute the `algorithms` parameter based on the `alg` from the token itself… hard-code a fixed value" (RFC 8725 §2.1), and don't mix symmetric and asymmetric algorithms (HS\* vs RS\*). Never set `options={"verify_signature": False}`; use `require` to mandate `exp`. This blocks the `alg=none` downgrade and the RS256→HS256 confusion attack (CWE-347).

## Framework and runtime footguns (bandit B1xx/B2xx)

- **Never run Flask with `debug=True` reachable in production.** Debug mode enables the Werkzeug interactive debugger, which "allows executing arbitrary Python code from the browser… Do not run the development server or debugger in a production environment." bandit B201 (`flask_debug_true`) rates this High/High (CWE-94). Drive `debug` from an env var that is false in prod and serve behind a real WSGI server (gunicorn/uwsgi).
- **Never deploy Django with `DEBUG=True`; keep `SECRET_KEY` secret and set `ALLOWED_HOSTS`.** `DEBUG=True` leaks source excerpts, local variables, settings, SQL queries, and file paths, and Django remembers every query (memory growth). A known `SECRET_KEY` "can lead to privilege escalation and remote code execution," so load it from the environment. Django "validates Host headers against the `ALLOWED_HOSTS` setting"; run `manage.py check --deploy` (the deployment checklist).
- **Bind a listener to `127.0.0.1`, not `0.0.0.0`, unless deliberately exposing it.** bandit B104 (`hardcoded_bind_all_interfaces`, CWE-605): binding to all interfaces "can potentially open up a service to traffic on unintended interfaces." Avoid cleartext protocols — `telnetlib` (B312) and `ftplib` (B321) transmit credentials in the clear (CWE-319); use SSH/SFTP/HTTPS.
- **Set least-privilege modes on created files and dirs.** bandit B103 (`set_bad_file_permissions`, CWE-276) flags an overly permissive `os.chmod` mask. Use `0o600` for secret-bearing files and `0o700` for private dirs; never world/group-writable or `0o777`.
- **Avoid catastrophic-backtracking regexes on untrusted input.** "Evil regex" with nested or overlapping quantifiers — `(a+)+$`, `([a-zA-Z]+)*$` — cause exponential backtracking (ReDoS, CWE-1333/CWE-400). Python's stdlib `re` is a backtracking engine with no match timeout, so don't run attacker-controlled patterns, bound input length before matching, and prefer simple anchored patterns or the non-backtracking `google-re2` binding for untrusted input.

## Migrations are symmetric

Upgrade and downgrade are equal-weight code paths. Both get the same rigor.

- Every foreign key carries an explicit `ondelete` policy (cascade / restrict). No default-without-thinking.
- Every drop-table or destructive downgrade against a table that may hold operator-corrected, audit-trail, or regulatory-mandated data queries for that data first and refuses with a clear message if non-zero rows exist. An operator-visible refusal is better than silent data loss.
- Every migration that adds a new table appears in the migration-registry / expected-set the validation script consults. A mismatch is a CI failure, not a deployment surprise.
- Run upgrade-head + downgrade-base end-to-end on a fresh local store before pushing — this verifies the downgrade is real, not a stub.

## Disabled safeguards carry a visible re-enable marker

Any production safeguard that ships disabled or commented-out carries a marker the reviewer and tooling can grep for. Examples: a commented-out auth check, an RBAC default-allow placeholder, an open-CORS default, a stub permission decorator. The marker names the safeguard, names the condition under which it gets re-enabled, and the ticket or story that re-enables it. Undecorated commented-out safeguards drift into permanent residue — treat an undecorated disabled-safeguard pattern as BLOCKING in review, regardless of severity.

## LLM applications (OWASP LLM Top 10:2025)

These apply wherever the codebase calls an LLM or processes LLM-emitted data; `python-llm.md` carries the broader LLM-call mechanics.

- **LLM output is untrusted data.** Validate it against a schema before acting on it. Never `eval`/`exec`/auto-run LLM-emitted code, and never pass LLM output directly into SQL, shell, or file paths. (LLM05 Improper Output Handling.)
- **External content fed *into* the LLM is a potential injection vector.** A fetched web page, document, or user note passed into a prompt can contain instructions. Use system-prompt hardening and constrained output formats. (LLM01 Prompt Injection.)
- **Bound LLM agency.** Any LLM call that *acts* on the world — places an order, modifies state — must be capability-scoped and audited. (LLM06 Excessive Agency.)
- **Audit trail for LLM-driven decisions.** Every LLM call that affects production state writes a row to a durable audit log naming the model, prompt hash, response hash, and resulting action. (LLM07 adjacent.)
- **Cost and token budgets enforced.** No unbounded LLM call — a budget tracker is the enforcement mechanism. (LLM10 Unbounded Consumption.)

## Worker discipline at write time

When writing code that consumes external data:

1. Define a typed dataclass (or pydantic model) for the validated input shape.
2. Write the parser / validator first, with tests.
3. Make the consuming function take the validated type, not the raw input.
4. The boundary between raw and validated is the boundary between unsafe and safe.

When writing code that calls an LLM:

1. Schema for the response (JSON shape, allowed values).
2. Validator that rejects malformed responses.
3. Bounded budget at the call site.
4. Audit-log entry on every action taken from the response.

## What this rule does NOT cover

- bandit's mechanical patterns themselves — those run at the tester phase. This rule explains the rationale; bandit enforces them at output time.
- Rig-specific concerns (broker credentials, money values, domain capability-gate frameworks) — those live in the project's own overlay rules under `.claude/rules/project/`.
- Operational secrets management (Vault, AWS Secrets Manager, etc.) — out of scope for code-level discipline.

## References

- OWASP Top 10:2025 — https://owasp.org/Top10/2025/
- OpenSSF Secure Coding Guide for Python — https://best.openssf.org/Secure-Coding-Guide-for-Python/
- OWASP LLM Top 10:2025 — https://owasp.org/www-project-top-10-for-large-language-model-applications/
- bandit documentation — https://bandit.readthedocs.io/
