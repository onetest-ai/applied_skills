---
description: Use when the user asks a factual or analytical question the knowledge base should ground — answers from the Brain with a fully cited response, decomposing into sub-claims, routing each to the right lane, and marking anything the Brain cannot support as "not modeled".
arguments: [question]
---

Answer **$question** grounded in the Brain. The Brain contract below governs; `_shared/doctrine.md` holds worked examples and depth.

1. **Resolve the Brain.** Use the contract's resolution order: identify candidate servers by tool surface, `health` each, use the override if the user named one, ask if several answer, and point to `/kb:connect` if none does. Read `about` from the resolved Brain's `health`: tune this answer's **altitude and vocabulary** to `about.audience`, within `about.goal`'s scope. If `about.audience` is empty, proceed with no persona.

2. **Decompose** the question into sub-claims. Classify each: narrative, number, relation, or visual/table.

3. **Route each sub-claim:**
   - narrative → `search_knowledge`
   - number → `get_metric` (never assert a figure from narrative)
   - relation/classification → `get_taxonomy`
   - a specific section / page figure / table → `get_evidence`

4. **Compose one answer** per the contract's answer format below, shaped to fit and to `about.audience`.

5. **Commit, cite, then qualify** — give the value first, disambiguate after; never refuse a retrievable figure.

## The Brain contract (non-negotiable)

<!-- BRAIN-CONTRACT:START -->
**Resolve one Brain per invocation.** A Brain is any MCP server exposing the tool surface
`health`, `search_knowledge`, `get_metric`, `get_taxonomy`, `get_evidence`,
`find_related_content`, `list_metrics` — identify it by that surface, never by server name
(a later pin in project instructions will slot a step above this order; not yet in effect).

1. **Override.** If the user named a Brain in the request, match it case-insensitively
   against each candidate's server-name segment and its `about.goal`. Use that Brain.
2. **Discover.** Scan available tools for servers carrying the surface and call `health`
   on each candidate.
3. **One healthy Brain.** Use it.
4. **Several.** Ask the user which, listing each as `server-name — about.goal`. Do not guess.
5. **None.** Say so and point to `/kb:connect`.

**One invocation binds to one Brain.** Once resolved, every call in this invocation goes to
that same server. Never blend results from two Brains into one cited answer — a mixed
answer is unverifiable, and its citations point at stores the reader cannot reconcile.

**Numbers only from `get_metric`.** Never assert a figure from narrative; a number comes
only from `get_metric` (a governed `facts` row) or a `get_evidence` extracted table.

**Every claim is cited, or declared "Not modeled: …".** A gap beats a guess.

**Answer format.** Lead with a 1–2 sentence direct answer. Cite each supported claim with a
numbered footnote `[1]`, `[2]`, … Close with a `**Sources**` list mapping each number to
`source_file.md — "Section"`. Keep `[RAG:]`/`[MART:]`/`[GRAPH:]` as internal anchors only —
never print them to the user.
<!-- BRAIN-CONTRACT:END -->

See `_shared/doctrine.md` → **Brain Discovery** for the full discussion, worked examples,
and audience/goal guidance.
