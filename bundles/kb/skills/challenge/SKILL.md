---
description: Adversarially test a claim against the Brain — find contradicting evidence, verify metrics at the stated grain, and surface silent gaps marked as "not modeled".
allowed-tools: mcp__brain__search_knowledge mcp__brain__get_metric mcp__brain__get_taxonomy mcp__brain__find_related_content mcp__brain__get_evidence mcp__brain__list_metrics mcp__brain__health mcp__plugin_brain_brain__search_knowledge mcp__plugin_brain_brain__get_metric mcp__plugin_brain_brain__get_taxonomy mcp__plugin_brain_brain__find_related_content mcp__plugin_brain_brain__get_evidence mcp__plugin_brain_brain__list_metrics mcp__plugin_brain_brain__health
arguments: [claim]
---

Adversarially test **$claim** against the Brain. Follow `../_shared/doctrine.md`.

1. **Detect the Brain.** Call `health`. If no Brain answers, tell the user and suggest `/kb:connect`; stop.

2. **Decompose the claim.** Break it into sub-claims: narratives, numbers, relations, and visual references.

3. **Search for contradictions.** Use `search_knowledge` to find sections that contradict or refine each claim. Look for caveats, conditions, and dissenting evidence.

4. **Verify all numbers.** For each metric claim, call `get_metric` to confirm it exists at the stated **grain**. Flag any grain mismatches (e.g., claimed quarterly but only monthly available).

5. **Surface silent gaps.** Mark anything unsupported as "Not modeled: …" — gaps are as important as confirmations.

6. **Render a verdict per sub-claim.** Each cites its **source file (and section) or metric `source_file`** — not the raw chunk_id (see `../_shared/doctrine.md` → Displaying citations). Output: CONFIRMED, REFINED, CONTRADICTED, or NOT MODELED.
