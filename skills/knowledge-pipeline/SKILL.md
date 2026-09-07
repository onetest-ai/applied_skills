---
name: knowledge-pipeline
description: Orchestrator — turn a document + data corpus into ONE local knowledge.sqlite (chunks+FTS+vector, numeric marts, taxonomy graph) and answer questions over it truthfully. Use when the user wants to "build the knowledge base", "index this corpus", "set up retrieval", or "answer questions over these docs+spreadsheets". Composes corpus-taxonomy-extraction, knowledge-index, tabular-semantic-layer, hybrid-retrieval. Local, portable, no server.
---

# Knowledge Pipeline (orchestrator)

One entry point that turns a mixed corpus (documents + reporting spreadsheets) into a single portable **`knowledge.sqlite`** and answers questions over it. Composes the build + retrieval skills; the store is one file (no server), so it moves anywhere (.dsh / Claude / Copilot / Codex / CI).

**Core principle (unchanged across the toolchain):** *meaning is agentic, numbers are computed.* RAG never emits a figure; the marts never guess. Every answer is cited or an honest "not modeled."

## The store — one SQLite, three lanes
| Lane | Tables | Built by |
|---|---|---|
| narrative (RAG) | `chunks`, `chunks_fts`, `chunks_vec` | `knowledge-index` |
| numbers | `facts` (+ `metrics.<corpus>.json` catalog) | `tabular-semantic-layer` |
| taxonomy graph | `graph_nodes`, `graph_edges` | `corpus-taxonomy-extraction` (`build_graph.py`) |

## Build (run once per corpus; re-run to refresh)
```bash
DB=<project>/schema/knowledge.sqlite
# 1. parse docs → Markdown (torch-free; Docling/pypdf)
python .../corpus-taxonomy-extraction/parse_corpus.py --corpus <docs> --out <project>/parsed --formats pptx,docx,pdf
# 2. (optional) induce taxonomy → taxonomy_v0.json  [map→reduce→judge→emit; see that skill]
# 3. narrative index — heading-aware sections (shared chunker) → chunks+FTS+vector
python .../knowledge-index/knowledge_index.py index --db "$DB" --corpus <project>/parsed --reset
# 4. taxonomy graph (vertices = L1/L2) into the SAME db
python .../corpus-taxonomy-extraction/build_graph.py --taxonomy <project>/taxonomy/taxonomy_v0.json --db "$DB"
# 5. per-section taxonomy tags — LOW-TIER AGENTS (meaning is agentic), not a script:
python .../corpus-taxonomy-extraction/classify_prep.py --db "$DB" --taxonomy <project>/taxonomy/taxonomy_v0.json --out <project>/classify --batches 5
#    → dispatch N Haiku subagents: each reads classify/{instructions,vocab,batch_k}.md/json → writes classify/result_k.json
python .../corpus-taxonomy-extraction/classify_write.py --db "$DB" --results <project>/classify   # -> chunk_topics + graph 'about' edges
# 6. numeric marts (Excel → facts) into the SAME db
python .../tabular-semantic-layer/build_marts.py --root <reporting> --config <project>/schema/families.<corpus>.json --out-dir <project>/marts --db "$DB"
# 7. Obsidian vault as a VIEW of the store (notes = chunks, real per-section tags, links = graph vertices)
python .../corpus-taxonomy-extraction/to_obsidian.py --db "$DB" --out <project>/vault
```
Result: one `knowledge.sqlite` — `chunks`/`chunks_fts`/`chunks_vec` (a chunk = a section = an Obsidian note), `chunk_topics` (real per-section taxonomy tags via low-tier agents), `facts` (marts), `graph_nodes`/`graph_edges` (taxonomy vertices + `subclass_of` + `about` edges to chunks). Check the `build_marts` audit (`--strict` in CI). The vault is generated from the store, so notes, retrieval chunks, tags, and graph all reference the same ids.

## Answer (per question)
Follow **`hybrid-retrieval`**: decompose → classify each sub-claim (computable→marts / narrative→RAG / relation→graph / both→reconcile) → retrieve against the one `$DB` → compose one cited answer. Tag facts `[MART]` / `[RAG]` / `[GRAPH]`; state unmodeled sub-parts plainly.

## Workspace & checkpointing (for long / multi-step research)
Borrowed from a disk-first research discipline — use it when a question needs many retrieval steps:
```
<project>/.research/<YYYY-MM-DD>/<session>/
  00_plan.md         # sub-questions + which lane answers each — before retrieving
  notes.md           # rolling findings with source citations
  checkpoint_NNN.md  # batch results; drop detail from context after writing
  report.md          # final answer, assembled from disk (never from memory)
```
Rules: plan first; disk is truth, memory is scratch; checkpoint every ~10 steps or ~150K tokens; assemble the final report from checkpoints; on interruption `ls` the workspace and resume from the last checkpoint. For a single-shot question, skip the workspace — just route via `hybrid-retrieval`.

## Corpus-specific vs generic
Generic (these skills): all the code. Project-specific (the consuming repo): `schema/families.<corpus>.json`, `schema/metrics.<corpus>.json`, the goal string, `taxonomy_v0.json`, and the built `knowledge.sqlite`. Keep project data in the project, never in the skills.

## Deps
`sqlite3` (stdlib) + `sqlite-vec`, `fastembed` (RAG), `docling`/`pypdf`/`openpyxl` (parse/marts), `pandas`/`pyarrow` (marts). All pip-installable, torch-free.
