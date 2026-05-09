---
paths:
  - "README.md"
  - "docs/**"
  - "plans/**"
  - "reviews/**"
  - "specs/**"
---
# Writing register

These documents are read by senior and junior practitioners in the relevant field. Write closer to a senior IC's design doc than to a consultative chat: direct, position-taking, structurally argued, spare on tone-markers.

## Voice

"The team" rather than "your team" — the writer is part of the team. "We" for recommendations and joint conclusions. First-person singular ("I") is reserved for specific self-corrections where it earns its place — flagging that a prior position was wrong, naming a mistake, or distinguishing personal judgment from team consensus.

When writing for a specific external audience (a customer, a stakeholder group), prefer impersonal phrasing — "the firm's infrastructure," "the operations team" — over us/them framing.

## Tone markers

Avoid these as filler:

- "honest answer," "honestly," "the honest truth," "to be candid," "let me be straight," "frankly," "in all candor," "real talk"
- Phrases that gesture at confidence rather than demonstrating it: "trust me," "the real answer is," "I want to flag that..."
- Marketing intensifiers: "substantially," "seamlessly," "robust," "powerful," "best-in-class," "cutting-edge," "industry-leading"
- Throat-clearing: "It's worth noting that," "A few things to call out," "I want to point out"

When candor is genuinely needed — flagging a self-correction, naming a position that deviates from a default, or acknowledging a mistake — do the work in the structure of the argument: present the default, name why it doesn't fit, then arrive at the deviation. The reader does not need to be told the answer is honest; they should be able to see that it is by how it is supported.

Hedge only where genuine uncertainty exists. Confidence is shown by taking a position and supporting it, not by adding "I think" before every claim. Uncertainty is named explicitly when it matters: "I don't know," "this needs verification," "the source is silent on X."

## Mechanical conventions

US English spelling throughout (color, behavior, recognize, favorable, organize). Closed compounds for "non-" prefixes where unambiguous (nonpublic, nontrivial, nonstandard); hyphenate where the term is established as such in the field (non-functional requirement, non-deterministic).

En dashes (–) for numeric and date ranges: "8–10 deals," "2010–2015," "pages 14–22." Hyphens reserved for compound modifiers and hyphenated words: "high-confidence field," "audit-logged event." Suspended hyphens for compound modifiers across a range: "2- to 4-hour window."

Serial (Oxford) comma in lists of three or more: "credit agreements, structure charts, and funds flow."

Em dashes (—) with surrounding spaces for parenthetical breaks: "the system — including its audit trail — runs on Azure." Reserve em dashes for genuine breaks in thought; commas or parentheses suffice for tighter asides.

Introduce abbreviations on first use: "material nonpublic information (MNPI)," then use the abbreviation thereafter.

Numerals for quantitative claims, percentages, and counts where scannability matters (8 hours, 90%, 100+ pages). Spell out numbers only when they begin a sentence or appear in formal/non-quantitative prose ("three reasons follow," "two questions remain").

## Formatting

Headings in sentence case unless the convention of the genre calls for headline case.

Bullets for parallel items where order does not matter or where visual scan helps. Prose for arguments, explanations, and anything where the connections between ideas matter. Default to prose; reach for bullets when the structure earns them.

Tables for comparison along consistent dimensions. Avoid tables when prose would be clearer.

Code in fenced blocks with language identifiers. API identifiers preserved in their canonical form (`claude-sonnet-4-6` in code, "Claude Sonnet 4.6" in prose).
