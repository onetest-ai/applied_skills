---
name: knowledge-index
description: Use to build and query a LOCAL hybrid retrieval index over Markdown — SQLite FTS5 (BM25) + sqlite-vec (embeddings), fused by Reciprocal Rank Fusion. One portable .sqlite file, no server, torch-free (fastembed/onnx). Feed it Docling/plain Markdown; it chunks, embeds, and serves cited recall. The narrative lane of the local knowledge store.
---

# Knowledge Index (local hybrid RAG)

Turn a folder of Markdown into a **hybrid retrieval index in one SQLite file**. Pattern lifted from [arozumenko/wikis](https://github.com/arozumenko/wikis): run **BM25 (FTS5) and vector (sqlite-vec) in parallel and fuse by Reciprocal Rank Fusion (RRF)** — no single-signal weakness, no server, no lock-in.

**Why hybrid, not one:** BM25 nails exact tokens (codes `NW2`, acronyms `FCR`, names `Five9`, metric labels); vectors nail paraphrase/concept ("loyalty" → "relationship NPS"). RRF (rank-based, k=60) merges the two ranked lists without caring that BM25 floats and cosine are on different scales.

## Where it fits
This is the **narrative lane** of a local knowledge store. It writes `chunks`, `chunks_fts`, `chunks_vec` into the SAME `.sqlite` that holds the numeric `facts` (from `tabular-semantic-layer`) and the taxonomy `graph_nodes/graph_edges` (from `corpus-taxonomy-extraction`). One file = the whole store; `hybrid-retrieval` routes across all three.

## Commands
```bash
# build/refresh the index (recursively globs *.md)
python knowledge_index.py index  --db knowledge.sqlite --corpus <markdown dir> [--reset] [--max-chars 1200] [--model M]
# hybrid recall (BM25 + vector, RRF-fused) — returns cited chunks
python knowledge_index.py search --db knowledge.sqlite --query "why did X change" [--k 8] [--json]
#   snippet length: the human view caps each hit at 240 chars; --full prints the whole
#   chunk, --chars N overrides the cap (--json already carries full text).
python knowledge_index.py search --db knowledge.sqlite --query "..." --full
# catalog of indexed source docs (no model needed) — so you don't guess filenames via search
python knowledge_index.py sources --db knowledge.sqlite [--like SUBSTR] [--json]
```
- **Input = Markdown.** Point it at parser output (`corpus-taxonomy-extraction/parse_corpus.py` / `visual-parse`) or any `.md`. No code→text layer needed — the input is already text.
- Chunking: paragraph-merge to ~`--max-chars` (default 1200). Deterministic.
- Fusion knobs (top of the script): `RRF_K=60`, `W_FTS=0.4`, `W_VEC=0.6`, `POOL=30` — the wikis defaults.

## Embeddings (torch-free)
Default `BAAI/bge-small-en-v1.5` (384-dim) via **fastembed/onnxruntime** — no PyTorch. First run downloads the model (~130 MB) then caches. Swap with `--model` (keep `--dim` in sync). Portable: the index is just rows in SQLite; the embedder is only needed at index/query time.

## Guarantees / notes
- Every result carries its **`source`** — recall is always citable; the orchestrator/answerer never asserts uncited text.
- One `.sqlite`, copyable anywhere (.dsh / Claude / CI). No daemon.
- Re-index with `--reset` for a clean rebuild; incremental upsert-by-hash is a future add (see wikis `embedding_content_hash`).
- Deps: `sqlite-vec`, `fastembed` (`pip install sqlite-vec fastembed`). Python's `sqlite3` must allow `enable_load_extension` (true on Homebrew/most builds).
