---
description: Use when a claim needs adversarial testing against the Brain — finds contradicting evidence, verifies metrics at the stated grain, and surfaces silent gaps marked as "not modeled".
arguments: [claim]
---

Adversarially test **$claim** against the Brain. Follow `../_shared/doctrine.md`.

1. **Resolve the Brain.** Follow `../_shared/doctrine.md` → **Brain Discovery**: identify candidate servers by tool surface, `health` each, use the override if the user named one, ask if several answer, and point to `/kb:connect` if none does.

2. **Decompose the claim.** Break it into sub-claims: narratives, numbers, relations, and visual references.

3. **Search for contradictions.** Use `search_knowledge` to find sections that contradict or refine each claim, and `get_evidence` to pull a specific cited section directly. Look for caveats, conditions, and dissenting evidence.

4. **Verify all numbers.** For each metric claim, call `get_metric` to confirm it exists at the stated **grain**. Flag any grain mismatches (e.g., claimed quarterly but only monthly available).

5. **Surface silent gaps.** Mark anything unsupported as "Not modeled: …" — gaps are as important as confirmations.

6. **Render a verdict per sub-claim.** Lead each with its verdict — CONFIRMED, REFINED, CONTRADICTED, or NOT MODELED — then the evidence, cited with a numbered footnote `[1]`, `[2]`, … Close with a `**Sources**` list mapping each number to its **source file (and section) or metric `source_file`** — never the raw chunk_id. Follow `../_shared/doctrine.md` → **Answer format**.
