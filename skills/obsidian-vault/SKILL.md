---
name: obsidian-vault
description: Use to navigate and answer from an Obsidian vault — follow [[wikilinks]], filter by #tags, traverse MOCs/topic notes, read a note's Related sections and embedded images. For a brain built by this toolkit, the vault is a VIEW of the knowledge.sqlite store (note = section = retrieval chunk). Complements the brain MCP tools: numbers still come from the store (never vault prose); the vault is for browsing relationships, reading full context, and working without the MCP server.
---

# obsidian-vault (navigate & answer from the vault)

An Obsidian vault is plain Markdown notes linked by `[[wikilinks]]` and organized by
`#tags` and folders — so it is fully navigable with `grep`/`ls`/file reads, no app
needed. For a brain built by this toolkit the vault is a **view of the store**
(`to_obsidian.py`): **one note = one section = one retrieval chunk**, so everything you
read here maps 1:1 to what the MCP tools return.

## When to use the vault vs the MCP tools
- **Numbers / exact values** → the **brain MCP `sql`/`metric`** over `facts`. NEVER quote a figure from vault prose (it may be a lossy transcription); the vault is meaning, not the numeric source of truth.
- **Precise semantic recall / a table on a visual page** → MCP `search` / `page` (full 384-dim hybrid + the extracted grid).
- **The vault is best for:** browsing how things relate (`[[links]]`, topic notes, Related sections), reading a note's full context and its embedded slide image, filtering a theme by tag, and answering when no MCP server is connected.

## This vault's conventions (built by the toolkit)
```
<vault>/
  <Parent>/<Document>/          # folder tree mirrors the source path
      <Document>.md             #   the doc index (MOC): Topics + a list of its sections
      NN <section>.md           #   one note per section (= a retrieval chunk)
  _topics/<L1>.md · <L2>.md     # taxonomy vertices (== graph_nodes); L1 lists its L2 children
  _assets/<doc>/pNN.png         # rendered page/slide images (embedded in visual-page notes)
```
Inside a section note:
- **frontmatter tags** — `source/<family>` (which doc set) and `intent/<L1>` (taxonomy categories from classification). Filter by these.
- **`![[_assets/…png]]`** — the rendered slide/page image (for a visual page: flow, timeline, diagram).
- **`## Related sections`** — the top semantic neighbors (cosine kNN, cross-document) with scores — the non-obvious "this connects to that" links.
- **`↩ [[<Document>]]`** — backlink to the doc index; **`· topics: [[topic · <L1>]]`** — links to the taxonomy vertices this section is about.

## How to retrieve / answer from the vault (grep-first)
```bash
# find notes about a theme (by taxonomy tag)
grep -rl "intent/billing-payments" vault/
# what a taxonomy topic covers → open the topic note, then read its backlinks
sed -n '1,40p' "vault/_topics/Billing & Payments.md"
# a section's cross-doc neighbors → its "## Related sections" block
awk '/## Related sections/{p=1} p; /^---/{if(p)exit}' "vault/<Parent>/<Doc>/NN ....md"
# follow a [[wikilink]] → the target file is the link text (+ .md), path-qualified links resolve directly
# read a visual page's real content: open its ![[_assets/…png]] image, and for numbers use the brain `page` tool
```
Answering discipline (same as the whole toolkit): **retrieve on meaning, answer from content, cite the source.** Every note names its document (backlink) and its section title — cite those. For any number, switch to the MCP `sql`/`metric`/`page` path and cite the `facts` row or the extracted table grid.

## Generic Obsidian (any vault)
The same moves work on any Obsidian vault: `[[wikilinks]]` (and `[[note#heading]]`, `[[note|alias]]`), `#tags` / nested `#a/b` tags, MOC/index notes, and `![[embeds]]`. Prefer `grep -rl` over a tag/phrase to locate notes, then read and follow links. Dataview/Bases queries you can't execute — emulate them with `grep`/`awk` over the frontmatter.
