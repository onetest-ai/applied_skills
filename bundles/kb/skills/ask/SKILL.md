---
description: Use when the user asks a factual or analytical question the knowledge base should ground — answers from the Brain with a fully cited response, decomposing into sub-claims, routing each to the right lane, and marking anything the Brain cannot support as "not modeled".
arguments: [question]
---

Answer **$question** grounded in the Brain. Follow `../_shared/doctrine.md`.

1. **Resolve the Brain.** Follow `../_shared/doctrine.md` → **Brain Discovery**: identify candidate servers by tool surface, `health` each, use the override if the user named one, ask if several answer, and point to `/kb:connect` if none does. Read `about` from the resolved Brain's `health`: tune this answer's **altitude and vocabulary** to `about.audience`, within `about.goal`'s scope. If `about.audience` is empty, proceed with no persona.

2. **Decompose** the question into sub-claims. Classify each: narrative, number, relation, or visual/table.

3. **Route each sub-claim:**
   - narrative → `search_knowledge`
   - number → `get_metric` (never assert a figure from narrative)
   - relation/classification → `get_taxonomy`
   - a specific section / page figure / table → `get_evidence`

4. **Compose one answer** in the house style (see `../_shared/doctrine.md` → **Answer format**): lead with a 1–2 sentence direct answer, then support shaped to fit and to `about.audience`, cite each claim with a numbered footnote `[1]`, `[2]`, … and close with a `**Sources**` list mapping each number to `source_file.md — "Section"`. Keep the raw `[RAG:]`/`[MART:]`/`[GRAPH:]` anchors internal — never print them. Anything unsupported is stated as "Not modeled: …".

5. **Commit, cite, then qualify** — give the value first, disambiguate after; never refuse a retrievable figure.
