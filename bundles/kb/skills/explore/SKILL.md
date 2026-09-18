---
description: Use when the user wants to explore a topic across the Brain — traverses related documents and taxonomy, surfacing connections and gaps with full citations to source files and vault paths.
arguments: [topic]
---

Explore **$topic** across the Brain. Follow `../_shared/doctrine.md`.

1. **Resolve the Brain.** Follow `../_shared/doctrine.md` → **Brain Discovery**: identify candidate servers by tool surface, `health` each, use the override if the user named one, ask if several answer, and point to `/kb:connect` if none does.

2. **Anchor the exploration.** Use `get_taxonomy(label=...)` to find a taxonomy node, or fall back to the top `search_knowledge` hit.

3. **Walk the graph.** Iterate through `find_related_content` and `get_taxonomy` subclasses across documents. Gather connections, contradictions, and gaps.

4. **Surface a navigable summary.** Lead with a one-line take on the topic, then list each connection with its type (parent, sibling, child) and a one-line insight. Mark anything unsupported as "Not modeled". Follow the house style in `../_shared/doctrine.md` → **Answer format**.

5. **Cite all claims with numbered footnotes.** Mark each connection with `[1]`, `[2]`, … and close with a `**Sources**` list mapping each number to its **source file (and section) / vault path** so the user can navigate to supporting evidence. Keep the `chunk_id` only as an internal anchor for `find_related_content` / `get_evidence` — never print raw ids.
