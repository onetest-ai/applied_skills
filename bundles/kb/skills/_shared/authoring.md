# Shared Authoring Pipeline

This pipeline governs how `/kb:brief` and `/kb:report` gather, draft, verify, and emit authored deliverables — ensuring every claim is cited, verifiable, and grounded in the Brain.

## The Pipeline

The authoring process follows a four-step flow:

1. **Gather** — Route the subject through the Brain's MCP tools (`search_knowledge`, `get_metric`, `get_taxonomy`, `find_related_content`, `get_evidence`) per `../_shared/doctrine.md`. Collect raw claims, numbers, and relations.

2. **Draft** — Compose the memo or report with inline citation tags: `[RAG:<chunk_id>]`, `[MART:<metric>@<grain>]`, `[GRAPH:<node>]`. Mark any unsupported area as an explicit "Not modeled: …" callout.

3. **Verify** — Dispatch the `verifier` subagent (read-only; no edits). It re-resolves every citation against the Brain and returns a per-claim verdict. Block on any unsupported or grain-mismatched claim; demote uncertain ones to caveats.

4. **Human Gate** — Show the draft and verifier verdict to the human. Do **not** write to disk without explicit approval. (Brief and report skills have no `Write` in their allowed-tools.)

5. **Emit** — After approval, write the final Markdown and the sources sidecar.

## Emit Format

### Markdown Body

- Every claim carries its citation tag: `[RAG:chunk_id]`, `[MART:metric@grain]`, or `[GRAPH:node]`.
- Numbers always cite their `source_file` from the Brain's `facts` row or `get_evidence` output.
- Unmodeled areas are rendered as an explicit callout:
  ```
  > **Not modeled:** Why this area is out of scope or unavailable in the current corpus.
  ```

### Numbered Sources Footnote

At the end of the document, a **Sources** section lists every distinct source in order of appearance:

```markdown
## Sources

1. [RAG:chunk_id_1] — "Section title" — `source_file.md` — retrieved via `search_knowledge`
2. [MART:metric_name@grain] — `source_file.csv` — retrieved via `get_metric`
3. [GRAPH:node_label] — relation verified via `get_taxonomy`
```

Each source number shows the `source_file` so readers can trace claims to their origin.

### Sidecar: `<slug>.sources.json`

Alongside the emitted `<slug>.md`, write a `<slug>.sources.json` array documenting every extracted source:

```json
[
  {
    "tag": "RAG:chunk_abc",
    "kind": "narrative",
    "chunk_id": "chunk_abc",
    "source_file": "docs/knowledge/section.md"
  },
  {
    "tag": "MART:revenue@quarterly",
    "kind": "metric",
    "metric": "revenue",
    "grain": "quarterly",
    "source_file": "data/facts.csv"
  },
  {
    "tag": "GRAPH:executive_leadership",
    "kind": "taxonomy",
    "node": "executive_leadership",
    "source_file": null
  }
]
```

This sidecar is the source-of-truth for re-tracing claims and supports audit, refresh, or fact-checking workflows.

## Output Path

- **Default:** `docs/kb/<slug>.md` (e.g., `docs/kb/quarterly-revenue-analysis.md`)
- **Configurable:** Projects may redirect the output path via a `.claude/settings.json` override.
- **Vault option:** Pointing to an Obsidian vault path (e.g., `vault://my-vault/kb/<slug>.md`) is supported; the `source_file` references remain absolute for traceability.

## Doctrine Reference

Follow `../_shared/doctrine.md` for:
- Citation tag forms and when to use each (`[RAG:]`, `[MART:]`, `[GRAPH:]`).
- The "Commit, Cite, Then Qualify" flow (value first, then scope and caveats).
- Source precedence (computed > reported).
- Gaps beat fabrication.
