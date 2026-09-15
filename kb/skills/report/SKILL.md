---
description: Author a long-form structured report on a topic from the Brain — gather narrative, metrics, and relations; organize into sections with a Table of Contents; verify all claims; then propose the write to the human for approval. Emit to docs/kb/ with full Sources and a sources.json sidecar.
allowed-tools: mcp__brain__search_knowledge mcp__brain__get_metric mcp__brain__get_taxonomy mcp__brain__find_related_content mcp__brain__get_evidence mcp__brain__list_metrics mcp__brain__health mcp__plugin_applied-skills_brain__search_knowledge mcp__plugin_applied-skills_brain__get_metric mcp__plugin_applied-skills_brain__get_taxonomy mcp__plugin_applied-skills_brain__find_related_content mcp__plugin_applied-skills_brain__get_evidence mcp__plugin_applied-skills_brain__list_metrics mcp__plugin_applied-skills_brain__health
arguments: [subject]
---

Author a long-form structured report on **$subject** following `../_shared/authoring.md`.

## Steps

1. **Gather.** Call the Brain tools to comprehensively collect narrative, metrics, relations, and evidence on the subject. Route per doctrine (narrative → `search_knowledge`, numbers → `get_metric`, relations → `get_taxonomy`, cited sections → `get_evidence`). Organize findings by theme or sub-topic.

2. **Draft.** Compose a structured report with:
   - **Table of Contents** (auto-generated from section headings)
   - Multiple sections, each with inline citation tags (`[RAG:id]`, `[MART:metric@grain]`, `[GRAPH:node]`)
   - Explicit "Not modeled: …" callouts for unsupported areas
   - A **Sources** section at the end (numbered, with source_file for each)

3. **Verify.** Dispatch the `verifier` subagent to re-resolve every citation against the Brain. Return its verdict. Block on unsupported claims; if any fail, revise and re-verify.

4. **Propose.** Show the draft report, the verifier verdict, and the proposed path (`docs/kb/<slug>.md`) to the human. Do **not** write yet — await approval.

5. **Emit** (upon human approval). Write the final Markdown to `docs/kb/<slug>.md` and the sidecar `<slug>.sources.json`.

## Output Format

- **Report:** Markdown with a **Table of Contents**, multiple sections, and a **Sources** section at the end (numbered, with source_file for each).
- **Sidecar:** `<slug>.sources.json` as an array of {tag, kind, chunk_id?, metric?, grain?, source_file?} objects.

Every number shows its source. Gaps are explicit ("Not modeled: …"). The human approves before any file is written.
