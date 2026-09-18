---
description: Use when the user wants a long-form structured report on a topic from the Brain — gathers narrative, metrics, and relations; organizes into sections with a Table of Contents; verifies all claims; then proposes the write to the human for approval, emitting to docs/kb/ with full Sources and a sources.json sidecar.
arguments: [subject]
---

Author a long-form structured report on **$subject** following `../_shared/authoring.md`.

## Steps

1. **Resolve the Brain.** Follow `../_shared/doctrine.md` → **Brain Discovery**: identify candidate servers by tool surface, `health` each, use the override if the user named one, ask if several answer, and point to `/kb:connect` if none does. Read `about` from the resolved Brain's `health` and adapt the report's **tone, depth, section emphasis, and executive-summary-vs-detail balance** to `about.audience`, within `about.goal`'s scope (empty audience → no persona).

2. **Gather.** Call the Brain tools to comprehensively collect narrative, metrics, relations, and evidence on the subject. Route per doctrine (narrative → `search_knowledge`, numbers → `get_metric`, relations → `get_taxonomy`, cited sections → `get_evidence`). Organize findings by theme or sub-topic.

3. **Draft.** Compose a structured report with:
   - **Table of Contents** (auto-generated from section headings)
   - Multiple sections, each with inline citation tags (`[RAG:id]`, `[MART:metric@grain]`, `[GRAPH:node]`)
   - Explicit "Not modeled: …" callouts for unsupported areas
   - A **Sources** section at the end (numbered, with source_file for each)

4. **Verify.** Dispatch the `verifier` subagent, naming the resolved Brain in its dispatch prompt, to re-resolve every citation against the Brain. Return its verdict. Block on unsupported claims; if any fail, revise and re-verify.

5. **Propose.** Show the draft report, the verifier verdict, and the proposed path (`docs/kb/<slug>.md`) to the human. Do **not** write yet — await approval.

6. **Emit** (upon human approval). Write the final Markdown to `docs/kb/<slug>.md` and the sidecar `<slug>.sources.json`.

## Output Format

- **Report:** Markdown with a **Table of Contents**, multiple sections, and a **Sources** section at the end (numbered, with source_file for each).
- **Sidecar:** `<slug>.sources.json` as an array of {tag, kind, chunk_id?, metric?, grain?, source_file?} objects.

Every number shows its source. Gaps are explicit ("Not modeled: …"). The human approves before any file is written.
