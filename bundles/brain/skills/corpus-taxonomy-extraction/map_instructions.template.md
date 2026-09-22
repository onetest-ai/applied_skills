# MAP task — goal-directed taxonomy & metric extraction

> Instantiate this template per run: replace {{GOAL}} and {{MAP_DIR}}, then hand it to
> each Sonnet map subagent along with its assigned parsed files. Nothing here is
> corpus-specific except the goal you supply.

## Goal lens (use to filter what matters)
"{{GOAL}}"
Keep terms relevant to that goal. Do NOT silently drop off-goal terms — list them in "demoted".

## Audience lens (secondary emphasis — the goal remains the primary filter)
"{{AUDIENCE}}"
Favor intents/dimensions that this audience would ask about, and lean toward their vocabulary
for names/descriptions. This only re-orders emphasis WITHIN what the goal keeps — never a second
filter, and never a reason to drop a goal-relevant term. If the audience line is empty, ignore it.

## For EACH assigned parsed file
The file starts with a `# SOURCE: <path>` line — use that exact value as `source`.
Read the whole file, then emit ONE JSON file (schema below) to:
  {{MAP_DIR}}/<same-basename-with-.json-instead-of-.md>

## JSON schema (emit exactly this shape)
{
  "source": "<value from the # SOURCE line>",
  "intent_classes": [
    {"name": "...", "level": "L1|L2", "parent": "<L1 name or null>",
     "description": "...", "evidence": "<=200-char quote", "confidence": 0.0-1.0}
  ],
  "metrics": [
    {"name": "...", "value": "<stated figure/target or null>", "period": "<e.g. June 2026 or null>",
     "grain": "<overall|region|division|branch|rsr|null>", "definition": "<or null>",
     "source_type": "stated|computable|both", "evidence": "<=200-char quote", "confidence": 0.0-1.0}
  ],
  "entities": [
    {"name": "...", "kind": "region|division|branch|rsr|system|initiative|segment|channel|other",
     "evidence": "<=200-char quote", "confidence": 0.0-1.0}
  ],
  "demoted": ["<off-goal term seen but not extracted>", ...]
}

## Rules
- INTENT CLASSES: name the **reusable category** the discussion belongs to, not the specific
  incident, ticket, or project being discussed. Ask: "what topic area does this belong to?"
  not "what is this document about?".
  WRONG: "Login Page Timeout Bug - Sprint 14 Regression" (a specific incident)
  RIGHT: "Test Environment Management" (the reusable L1 category)
  WRONG: "Project X Automation Scope Template Coverage" (a project artifact)
  RIGHT: "Automation Scope & Coverage" (the reusable L1 category)
  Target: 5–15 L1 categories per file maximum. If you find yourself naming more than 15,
  you are being too specific — merge related topics under a broader L1.
  Exception — explicit taxonomy documents: if the file IS a taxonomy/compendium doc
  (structured table of categories), capture it faithfully — preserve exact names,
  keep parent↔child (L1↔L2) links, and do NOT merge or abstract away nodes.
  The 5-15 cap and merging rules apply only to inferred topics from narrative sources.
- METRICS & the truthfulness split:
  - `stated`     = a figure/target quoted in prose or a slide. Include the `value` and `period`.
  - `computable` = a measure that would be derived from a data table (usually no single value stated here).
  - `both`       = quoted AND clearly table-backed.
- EVIDENCE: a short verbatim quote (<=200 chars). Never invent values; if unsure, lower confidence.
- Output MUST be valid JSON. Write the files (do not print JSON in chat). Report only counts + issues.
