---
name: knowledge-index
description: Use to build and query a LOCAL hybrid retrieval index over Markdown — SQLite FTS5 (BM25) + sqlite-vec (embeddings), fused by Reciprocal Rank Fusion. One portable .sqlite file, no server, torch-free (fastembed/onnx). Feed it Docling/plain Markdown; it chunks, embeds, and serves cited recall. The narrative lane of the local knowledge store.
---

# Knowledge Index (local hybrid RAG)

Turn a folder of Markdown into a **hybrid retrieval index in one SQLite file**. Pattern lifted from [arozumenko/wikis](https://github.com/arozumenko/wikis): run **BM25 (FTS5) and vector (sqlite-vec) in parallel and fuse by Reciprocal Rank Fusion (RRF)** — no single-signal weakness, no server, no lock-in.

**Why hybrid, not one:** BM25 nails exact tokens (codes `NW2`, acronyms `FCR`, names `Five9`, metric labels); vectors nail paraphrase/concept ("loyalty" → "relationship NPS"). RRF (rank-based, k=60) merges the two ranked lists without caring that BM25 floats and cosine are on different scales.

## Where it fits
This is the **narrative lane** of a local knowledge store. It writes `chunks`, `chunks_fts`, `chunks_vec` into the SAME `.sqlite` that holds the numeric `facts` (from `tabular-semantic-layer`) and the taxonomy `graph_nodes/graph_edges` (from `corpus-taxonomy-extraction`). One file = the whole store; `hybrid-retrieval` routes across all three.

## Pipeline step: extraction JSON → Markdown

Before indexing, convert extraction JSON files into heading-structured Markdown with `extraction_to_md.py`.
Raw extraction JSON becomes one giant chunk (~20K chars) that overflows the MCP response cap; this script
rewrites each `.json` as `## <axis>` sections so the chunker produces ~1 chunk per record (~200-400 chars).

```bash
# Convert a directory of *.json extraction files → one .md per file
python extraction_to_md.py --input ~/projects/kt-docs/brain/extractions \
                            --out   ~/projects/kt-docs/brain/parsed
# Backs up originals to <out>/orig/ by default; pass --no-backup to skip.
```

Then index the resulting Markdown as usual.

## Commands
```bash
# build/refresh the index (recursively globs *.md; also indexes .vtt/.srt files)
python knowledge_index.py index  --db knowledge.sqlite --corpus <markdown dir> [--reset] [--max-chars 1200] [--model M]
# hybrid recall (BM25 + vector, RRF-fused) — returns cited chunks
python knowledge_index.py search --db knowledge.sqlite --query "why did X change" [--k 8] [--json]
# build semantic 'related' edges across chunks (enables find_related_content cross-session traversal)
python knowledge_index.py related --db knowledge.sqlite [--k 6] [--min-score 0.55] [--within-doc]
```
- **Input = Markdown.** Point it at parser output (`corpus-taxonomy-extraction/parse_corpus.py` / `visual-parse`) or any `.md`. No code→text layer needed — the input is already text.
- **VTT/SRT transcripts** are also supported as narrative input: `onboard.py` routes `.vtt` and `.srt` into the narrative lane alongside `.pdf`/`.docx`/`.md`.
- Chunking: paragraph-merge to ~`--max-chars` (default 1200). Deterministic.
- Fusion knobs (top of the script): `RRF_K=60`, `W_FTS=0.4`, `W_VEC=0.6`, `POOL=30` — the wikis defaults.

## Embeddings (torch-free)
Default `BAAI/bge-small-en-v1.5` (384-dim) via **fastembed/onnxruntime** — no PyTorch. First run downloads the model (~130 MB) then caches. Swap with `--model` (keep `--dim` in sync). Portable: the index is just rows in SQLite; the embedder is only needed at index/query time.

## Cross-session retrieval via `find_related_content`

After building the `related` table (`knowledge_index.py related`), the MCP tool `find_related_content`
lets agents traverse semantic edges between chunks **across conversation sessions**. Supply either a
`chunk_id` (from `search_knowledge` or `get_taxonomy`) or a plain-text `query`; the tool returns the
nearest semantically-related chunks from the `related` table (k=6, min\_score=0.55 defaults). This
enables multi-hop recall: find a chunk, then discover adjacent context the query alone would not surface.

Similarity edges are discovery candidates. They do not prove that one statement
answers, supersedes, or contradicts another. Add typed directional edges with
`knowledge_index.py add-edge`, and use the temporal ledger for current-state logic.

## Second-brain updates and temporal search

Normal `index` runs are incremental. Document and embedding hashes skip unchanged
documents and chunks; removed documents and sections are deleted from FTS, vectors,
tags, and related edges. Use `--reset` only for an intentional full rebuild.

Each chunk stores creation and event dates, validity, lifecycle status, heading
breadcrumb, and optional transcript speaker. Search can safely prefilter these fields:

```bash
python knowledge_index.py search --db knowledge.sqlite --query "budget" \
  --as-of 2025-09-21 --latest-only --source-contains meetings --tag Budget --json
```

Use `supersede --source <old-source> --valid-to <ISO date>` to retire every chunk
from an explicitly replaced source. For assertion-level history and question closure,
load `temporal_fixture.json`-shaped data with `temporal-load`. MCP clients then call
`get_current_fact` and `get_question_status`.

The `related` table retains cosine `SIMILAR` edges and supports directional `ANSWERS`,
`SUPERSEDES`, `REFERENCES`, and `CONTRADICTS` edges. Similarity rebuilds preserve typed
edges.

Run retrieval evals from CSV with `benchmark`; it reports Hit Rate at K and MRR.
This grades evidence retrieval. Synthesized-answer evals must run through the client
agent that composes from MCP results.

## Guarantees / notes
- Every result carries its **`source`** — recall is always citable; the orchestrator/answerer never asserts uncited text.
- One `.sqlite`, copyable anywhere (.dsh / Claude / CI). No daemon.
- Re-index without `--reset` for incremental hash-based updates.
- Deps: `sqlite-vec`, `fastembed` (`pip install sqlite-vec fastembed`). Python's `sqlite3` must allow `enable_load_extension` (true on Homebrew/most builds).
