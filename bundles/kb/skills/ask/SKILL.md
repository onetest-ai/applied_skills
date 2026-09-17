---
description: Answer a question from the Brain with a fully cited response — decompose into sub-claims, route each to the right lane, and mark anything the Brain cannot support as "not modeled". Use whenever the user asks a factual/analytical question that the knowledge base should ground.
allowed-tools: mcp__brain__search_knowledge mcp__brain__get_metric mcp__brain__get_taxonomy mcp__brain__find_related_content mcp__brain__get_evidence mcp__brain__list_metrics mcp__brain__health mcp__plugin_brain_brain__search_knowledge mcp__plugin_brain_brain__get_metric mcp__plugin_brain_brain__get_taxonomy mcp__plugin_brain_brain__find_related_content mcp__plugin_brain_brain__get_evidence mcp__plugin_brain_brain__list_metrics mcp__plugin_brain_brain__health
arguments: [question]
---

Answer **$question** grounded in the Brain. Follow `../_shared/doctrine.md`.

1. **Detect the Brain.** Call `health`. If no Brain answers, tell the user and suggest `/kb:connect`; stop. Read `about` from the result (see `../_shared/doctrine.md`): tune this answer's **altitude and vocabulary** to `about.audience`, within `about.goal`'s scope. If `about.audience` is empty, proceed with no persona.

2. **Decompose** the question into sub-claims. Classify each: narrative, number, relation, or visual/table.

3. **Route each sub-claim:**
   - narrative → `search_knowledge`
   - number → `get_metric` (never assert a figure from narrative)
   - relation/classification → `get_taxonomy`
   - a specific section / page figure / table → `get_evidence`

4. **Compose one answer.** Cite each claim in the **visible text by its source file and section** (from the retrieval hit's `source`); numbers show `source_file`. Keep the internal anchor — `[RAG:<chunk_id>]`, `[MART:<metric>@<grain>]`, `[GRAPH:<node>]` — for your own follow-up/verify calls only; **never print those tags to the user** (see `../_shared/doctrine.md` → Displaying citations). When several sources back the answer, close with a short **Sources** list of the distinct files. Anything unsupported is stated as "Not modeled: …".

5. **Commit, cite, then qualify** — give the value first, disambiguate after; never refuse a retrievable figure.
