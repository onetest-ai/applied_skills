---
description: Use when the user wants a long-form structured report on a topic from the Brain — gathers narrative, metrics, and relations; organizes into sections with a Table of Contents; verifies all claims; then proposes the write to the human for approval, emitting to docs/kb/ with full Sources and a sources.json sidecar.
arguments: [subject]
---

Author a long-form structured report on **$subject**. The Brain contract below governs
citation and sourcing; `../_shared/authoring.md` holds the full pipeline write-up and
worked examples.

## Steps

1. **Resolve the Brain.** Use the contract's resolution order: identify candidate servers by tool surface, `health` each, use the override if the user named one, ask if several answer, and point to `/kb:connect` if none does. Read `about` from the resolved Brain's `health` and adapt the report's **tone, depth, section emphasis, and executive-summary-vs-detail balance** to `about.audience`, within `about.goal`'s scope (empty audience → no persona).

2. **Gather.** Call the Brain tools to comprehensively collect narrative, metrics, relations, and evidence on the subject. Route per the contract (narrative → `search_knowledge`, numbers → `get_metric`, relations → `get_taxonomy`, cited sections → `get_evidence`). Organize findings by theme or sub-topic.

3. **Draft.** Compose a structured report with:
   - **Table of Contents** (auto-generated from section headings)
   - Multiple sections, each with inline citation tags (`[RAG:id]`, `[MART:metric@grain]`, `[GRAPH:node]`)
   - Explicit "Not modeled: …" callouts for unsupported areas
   - A **Sources** section at the end (numbered, with source_file for each)

4. **Verify.** Dispatch the `verifier` subagent, naming the resolved Brain in its dispatch prompt, to re-resolve every citation against the Brain. Return its verdict. Block on unsupported claims; if any fail, revise and re-verify.

5. **Human gate.** Show the draft report, the verifier verdict, and the proposed path (`docs/kb/<slug>.md`) to the human. Do **not** write to disk without explicit approval.

6. **Emit** (upon approval). Write the final Markdown to `docs/kb/<slug>.md` and a sidecar
   `<slug>.sources.json` — an array of `{tag, kind, chunk_id?, metric?, grain?, source_file?}`
   objects, one per distinct source, documenting every claim's origin for audit and re-trace.

## Output Format

- **Report:** Markdown with a **Table of Contents**, multiple sections, and a **Sources** section at the end (numbered, with source_file for each).
- **Sidecar:** `<slug>.sources.json` as an array of {tag, kind, chunk_id?, metric?, grain?, source_file?} objects.

Every number shows its source. Gaps are explicit ("Not modeled: …"). The human approves before any file is written.

## The Brain contract (non-negotiable)

<!-- BRAIN-CONTRACT:START -->
**Resolve one Brain per invocation.** A Brain is any MCP server exposing the tool surface
`health`, `search_knowledge`, `get_metric`, `get_taxonomy`, `get_evidence`,
`find_related_content`, `list_metrics` — identify it by that surface, never by server name.

1. **Override.** If the user named a Brain in the request, match it case-insensitively
   against each candidate's server-name segment and its `about.goal`. Use that Brain.
2. **Discover.** Scan available tools for servers carrying the surface and call `health`
   on each candidate.
3. **One healthy Brain.** Use it. Name it in one short line, then answer.
4. **Several.** Ask the user which, listing each as `server-name — about.goal` (prefer an
   advertised `about.name` over the goal when the Brain provides one). Do not guess.
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

See `../_shared/doctrine.md` → **Brain Discovery** and `../_shared/authoring.md` for the
full pipeline discussion and worked examples.
