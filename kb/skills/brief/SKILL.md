---
description: Author a short cited memo on a topic from the Brain — gather narrative, metrics, and relations; verify claims; then propose the write to the human for approval. Emit to docs/kb/ with a Sources section and a sources.json sidecar.
allowed-tools: mcp__brain__search_knowledge mcp__brain__get_metric mcp__brain__get_taxonomy mcp__brain__find_related_content mcp__brain__get_evidence mcp__brain__list_metrics mcp__brain__health mcp__plugin_applied-skills_brain__search_knowledge mcp__plugin_applied-skills_brain__get_metric mcp__plugin_applied-skills_brain__get_taxonomy mcp__plugin_applied-skills_brain__find_related_content mcp__plugin_applied-skills_brain__get_evidence mcp__plugin_applied-skills_brain__list_metrics mcp__plugin_applied-skills_brain__health
arguments: [subject]
---

Author a brief, cited memo on **$subject** following `../_shared/authoring.md`.

## Steps

1. **Detect the Brain.** Call `health` (see ../_shared/doctrine.md for namespace detection); if none, suggest `/kb:connect` and stop.

2. **Gather.** Call the Brain tools to collect narrative, metrics, and relations on the subject. Route per doctrine (narrative → `search_knowledge`, numbers → `get_metric`, relations → `get_taxonomy`, cited sections → `get_evidence`).

3. **Draft.** Compose a 200–400-word memo with inline citation tags (`[RAG:id]`, `[MART:metric@grain]`, `[GRAPH:node]`). Mark any unsupported claim as "Not modeled: …".

4. **Verify.** Dispatch the `verifier` subagent to re-resolve every citation against the Brain. Return its verdict. Block on unsupported claims; if any fail, revise and re-verify.

5. **Propose.** Show the draft memo, the verifier verdict, and the proposed path (`docs/kb/<slug>.md`) to the human. Do **not** write yet — await approval.

6. **Emit** (upon human approval). Write the final Markdown to `docs/kb/<slug>.md` and the sidecar `<slug>.sources.json`.

## Output Format

- **Memo:** Markdown with a **Sources** section at the end (numbered, with source_file for each).
- **Sidecar:** `<slug>.sources.json` as an array of {tag, kind, chunk_id?, metric?, grain?, source_file?} objects.

Each number shows its source. Gaps are explicit ("Not modeled: …"). The human approves before any file is written.
