---
paths:
  - "**/*llm*.go"
  - "**/*schema*.go"
---

# Go LLM boundary (structured output)

The typed contract between your code and the model — the single place this boundary's reliability is bought. Sources: the model provider's Go SDK, `invopop/jsonschema`, `go-playground/validator`, and `encoding/json`. The model is called through its official Go SDK; the model id is configured per call site.

> See `go-types.md` for the output struct as a typed contract, `go-concurrency.md` for the context timeout on every model call, `go-errors.md` for surfacing validation failures, and `craft-abstraction.md` for the schema as a specification.

> **Verify SDK specifics against the version pinned in `go.mod`.** The SDK evolves; treat the API names below as a guide and confirm them against the pinned source/examples before relying on them — do not compose SDK calls from memory.

## The struct is the contract

- **Define one Go struct as the single source of truth for each LLM output schema, and derive both the wire schema and the parsed result from it.** Everything else — schema JSON, validation, parsing — is generated from the struct so they can't drift.
- **Generate the JSON Schema from the struct with a reflector, never hand-written.** Reflect with `invopop/jsonschema` via a generic helper with `AllowAdditionalProperties: false` (reject unexpected fields) and `DoNotReference: true` (inline definitions). Drive property names and required/optional from `json` struct tags — an exported field with no `omitempty` is required.
- **Annotate every field with a `jsonschema:"description=..."`** — the description is the model's instruction for that field; an undescribed field invites garbage.

## Bound everything

- **Bound every string and array in the schema** — `minLength`/`maxLength` on strings, `minItems`/`maxItems` on slices, `enum` on closed sets, `minimum`/`maximum` on numbers — and set `AllowAdditionalProperties: false`. These are the typed guardrails that keep output finite and in range; an unbounded `[]string` lets the model return an arbitrarily large list.

## Validate at the boundary, retry bounded

- **Validate the parsed struct at the boundary with `go-playground/validator` before trusting it**, layering `validate` tags on top of the schema (the schema constrains generation, but the model can still violate it; `validate` also covers cross-field rules JSON Schema can't express). Initialize once with `validator.New(validator.WithRequiredStructEnabled())`.
- **On a schema or validation mismatch, retry the call with the validation error fed back as context, up to a small bounded retry count.** Don't accept malformed output and don't retry unboundedly — the bound prevents runaway token spend. Feed the *full, located* validation/parse error back (causal-first, un-summarized) so the model forms the right first repair — the gate-as-feedback discipline.

```go
// The struct IS the schema and the contract. Tags carry the constraints.
type Verdict struct {
    Decision string   `json:"decision" jsonschema:"enum=pass,enum=block,description=Review outcome" validate:"required,oneof=pass block"`
    Reasons  []string `json:"reasons"  jsonschema:"minItems=1,maxItems=10,description=One reason per finding" validate:"required,min=1,max=10,dive,required"`
    Severity int      `json:"severity" jsonschema:"minimum=0,maximum=5"  validate:"min=0,max=5"`
}
```

## Calling the model

- **Use the SDK's first-class structured-output path for a pure typed result, and the tool-input-schema path for a tool definition** — recent SDKs expose a structured-output format that auto-generates the wire schema and parses the response, gated by a beta header; build a tool's `InputSchema` from the same reflector otherwise. Confirm the exact API against the pinned SDK version.
- **Put a `context.WithTimeout` on every model call** and set the SDK's request timeout and max-retries explicitly via request options rather than relying on defaults — the per-call latency and cost budget belong to the caller. (`go-concurrency.md`.)
- **Pin `invopop/jsonschema` to a version compatible with the SDK** — there was a known incompatibility from a certain version onward; verify the resolved version against the SDK's `go.mod` and don't float it. (`go-modules.md`.)

## Keep the call bounded

- **One call, one typed result — no open-ended agent loop, no unbounded tool surface in a focused call site.** The leverage of a focused structured call over an open-ended agent is exactly this: a bounded, structured, validated call. Keep the prompt and the rubric fed whole (don't pre-digest), and keep the schema as the narrow typed boundary.
