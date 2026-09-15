---
description: Answer a question from the Brain with a fully cited response — decompose into sub-claims, route each to the right lane, and mark anything the Brain cannot support as "not modeled". Use whenever the user asks a factual/analytical question that the knowledge base should ground.
allowed-tools: mcp__brain__search_knowledge mcp__brain__get_metric mcp__brain__get_taxonomy mcp__brain__find_related_content mcp__brain__get_evidence mcp__brain__list_metrics mcp__brain__health mcp__plugin_brain_brain__search_knowledge mcp__plugin_brain_brain__get_metric mcp__plugin_brain_brain__get_taxonomy mcp__plugin_brain_brain__find_related_content mcp__plugin_brain_brain__get_evidence mcp__plugin_brain_brain__list_metrics mcp__plugin_brain_brain__health
arguments: [question]
---

Answer **$question** grounded in the Brain. Follow `../_shared/doctrine.md`.

1. **Detect the Brain.** Call `health`. If no Brain answers, tell the user and suggest `/kb:connect`; stop.

2. **Decompose** the question into sub-claims. Classify each: narrative, number, relation, or visual/table.

3. **Route each sub-claim:**
   - narrative → `search_knowledge`
   - number → `get_metric` (never assert a figure from narrative)
   - relation/classification → `get_taxonomy`
   - a specific section / page figure / table → `get_evidence`

4. **Compose one answer.** Every claim carries its tag — `[RAG:<chunk_id>]`, `[MART:<metric>@<grain>]`, or `[GRAPH:<node>]`. Numbers show `source_file`. Anything unsupported is stated as "Not modeled: …".

5. **Commit, cite, then qualify** — give the value first, disambiguate after; never refuse a retrievable figure.
