---
name: visual-parse
description: Use when a corpus has slide decks / diagram-heavy pages (flows, timelines, circle process charts, complex tables) that a text extractor mangles. Renders each page to an image, flags the visual ones, has a low-tier VISION model transcribe them to faithful structured Markdown, and extracts real table grids deterministically. Two representations per page — a semantic transcription (for retrieval) and the full factual content (image + verbatim text + table cells, for answering). Feeds the knowledge-index.
---

# visual-parse (the vision lane)

**The scary truth:** slide/diagram pages carry meaning in the LAYOUT — a flow's arrows, a timeline's bars, a circle chart's nesting. Any text extractor (docling/pypdf/PyMuPDF text) returns a bag of fragments: a 10-week timeline becomes `08/31 9/7 … 1 2 3` with no activity↔week link. That wrecks retrieval, classification, embeddings, and the related layer.

## Two representations per page (the core principle)
- **Semantic** — a vision model's faithful **structured Markdown** transcription (flow as an ordered list, timeline as a table, diagram relationships spelled out). This is what gets **chunked + embedded + indexed** → retrieval finds the page by meaning. It is a *simplification*.
- **Factual** — the **full content kept beside it**: the rendered page **image**, the **verbatim text layer**, and **deterministically-extracted table grids** (`PyMuPDF.find_tables()` — a VLM misreads dense tables). At answer time, MCP `get_evidence` returns the text/grids and the private image path so a capable local client can inspect the image.

> **Retrieve on the semantic layer; generate from the factual content.** For any number in a table, cite the **extracted grid**, never the prose paraphrase.

## Pipeline (render → transcribe → assemble)
```
doc (pdf / pptx via soffice→pdf)
  → render_pages.py  (deterministic)   per-page PNG + text sidecar + table grids; FLAG visual pages
  → 🤖 vision agents (low-tier, VLM)    transcribe flagged pages → result_<k>.json {img_sha: markdown}
  → vision_assemble.py                 merge into one parsed .md: each page starts a top-level section
                                        with an `<!-- image: … -->` marker (text pages use text layer)
  → knowledge-index index              headings/size may split a page into sibling chunks; each inherits
                                        the page marker → chunks.image. Table grids stay asset sidecars.
```

### 1. Render — `render_pages.py` (deterministic, no LLM)
`render_pages.py --doc <file> --out <assets> [--dpi 150] [--min-text 220] [--hi-draw 60] [--mid-draw 28] [--mid-text 1000] [--all]`
Writes `<assets>/<slug>/p<NN>.png`, `p<NN>.txt` (PyMuPDF text layer), `p<NN>.tables.md` (extracted grids), and `pages.json` (per-page `img_sha`, `text_len`, `n_drawings`, `n_tables`, `flagged`, `why`). The `<slug>` is derived from the **full source-relative `--doc` path** (each path component kebab-cased, joined by `__`), so same-named files in different folders no longer collide; a bare filename slugs exactly as before. **Migration:** a brain whose assets were rendered under the old basename-only slugs will not match the new slugs — re-render the affected documents (the `page_render` VLM cache is keyed by `img_sha`, so unchanged pages are not re-transcribed) or keep the old asset dirs until the next rebuild. A page is flagged visual (→ VLM) when the text layer likely misses the meaning: **thin text with no extracted table** (`why=thin-text`), **many drawings** (`why=dense-draw`, a timeline/diagram even with fragmented labels), or a **lighter diagram with modest text** (`why=diagram`). Image-area `cover` is NOT used (full-bleed backgrounds make it meaningless); a pure data table we already extracted is NOT flagged (we have the grid). Thresholds are tunable per corpus; `--all` forces every page. PPTX/DOCX → PDF via LibreOffice `soffice` first. On a representative sample this flags ~15–25% of deck pages (vs ~80% before tuning).

### 2. Transcribe — low-tier VISION subagents
Instantiate `vision_prep.py` to batch the **flagged, uncached** pages (image path + any extracted table + page context) with instructions, then dispatch vision subagents (model: a cheap vision model) that read each page image and emit faithful structured Markdown → `result_<k>.json` keyed by `img_sha`. Cache by `img_sha` (a page whose rendered image is unchanged is never re-transcribed).

### 3. Assemble — `vision_assemble.py`
`vision_assemble.py --render-dir <assets>/<slug> --out <parsed>/<doc>.md [--results <dir>] [--db <db>]`
Per page in order: the VLM Markdown (flagged) or the text layer (text page), under a `## p<NN> · <title>` heading with the image marker. Internal `#`/`##` are demoted; deeper VLM headings and max-size splitting may still yield multiple downstream chunks for one page, all inheriting its image. Writes the `page_render` cache when `--db` is given. Extracted `p<NN>.tables.md` grids remain factual asset sidecars (served by `get_evidence`) and are not appended to parsed Markdown.

## How the classifier / retrieval change
Nothing in the classifier or retriever changes — they just get **faithful input** instead of fragments. The classify agent now sees `North Star Vision & Service Design Blueprint / Future State Architecture / …` instead of `Confidential — Page 4`, so tagging, embeddings, and the related layer all improve for free. For genuinely visual edge cases, `get_evidence` returns the page asset path for a capable local client to open and reason over multimodally.

## Deps
`pymupdf` (render + text + `find_tables`) — torch-free. **LibreOffice `soffice`** (system dep) for .pptx/.docx. A cheap vision model for the transcription step (like the taxonomy/classify agents — meaning is agentic).
