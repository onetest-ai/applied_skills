---
description: Explore a topic across the Brain — traverse related documents and taxonomy, surfacing connections and gaps with full citations to chunk_id and vault paths.
allowed-tools: mcp__brain__search_knowledge mcp__brain__get_metric mcp__brain__get_taxonomy mcp__brain__find_related_content mcp__brain__get_evidence mcp__brain__list_metrics mcp__brain__health mcp__plugin_brain_brain__search_knowledge mcp__plugin_brain_brain__get_metric mcp__plugin_brain_brain__get_taxonomy mcp__plugin_brain_brain__find_related_content mcp__plugin_brain_brain__get_evidence mcp__plugin_brain_brain__list_metrics mcp__plugin_brain_brain__health
arguments: [topic]
---

Explore **$topic** across the Brain. Follow `../_shared/doctrine.md`.

1. **Detect the Brain.** Call `health`. If no Brain answers, tell the user and suggest `/kb:connect`; stop.

2. **Anchor the exploration.** Use `get_taxonomy(label=...)` to find a taxonomy node, or fall back to the top `search_knowledge` hit.

3. **Walk the graph.** Iterate through `find_related_content` and `get_taxonomy` subclasses across documents. Gather connections, contradictions, and gaps.

4. **Surface a navigable summary.** List each chunk_id / vault path with its connection type (parent, sibling, child) and a one-line insight. Mark anything unsupported as "Not modeled".

5. **Cite all claims.** Every connection carries a `chunk_id` tag so the user can navigate directly to supporting evidence.
