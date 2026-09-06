# MAP task — goal-directed taxonomy & metric extraction

> Instantiate this template per run: replace {{GOAL}} and {{MAP_DIR}}, then hand it to
> each low-tier map subagent along with its assigned parsed files. Nothing here is
> corpus-specific except the goal you supply.

## Goal lens (use to filter what matters)
"{{GOAL}}"
Keep terms relevant to that goal. Do NOT silently drop off-goal terms — list them in "demoted".

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
- INTENT CLASSES: if the corpus contains an explicit taxonomy (e.g. a taxonomy/compendium doc),
  capture it faithfully from tables — preserve exact names, keep parent↔child (L1↔L2) links.
- METRICS & the truthfulness split:
  - `stated`     = a figure/target quoted in prose or a slide. Include the `value` and `period`.
  - `computable` = a measure that would be derived from a data table (usually no single value stated here).
  - `both`       = quoted AND clearly table-backed.
- EVIDENCE: a short verbatim quote (<=200 chars). Never invent values; if unsure, lower confidence.
- Output MUST be valid JSON. Write the files (do not print JSON in chat). Report only counts + issues.
