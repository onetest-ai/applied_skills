---
name: visual-parse
description: Use when a corpus has slide decks / diagram-heavy pages (flows, timelines, circle process charts, complex tables) that a text extractor mangles. Renders each page to an image, flags the visual ones, has a low-tier VISION model transcribe them to faithful structured Markdown, and extracts real table grids deterministically. Two representations per page — a semantic transcription (for retrieval) and the full factual content (image + verbatim text + table cells, for answering). Feeds the knowledge-index.
---

# visual-parse (the vision lane)

**The scary truth:** slide/diagram pages carry meaning in the LAYOUT — a flow's arrows, a timeline's bars, a circle chart's nesting. Any text extractor (docling/pypdf/PyMuPDF text) returns a bag of fragments: a 10-week timeline becomes `08/31 9/7 … 1 2 3` with no activity↔week link. That wrecks retrieval, classification, embeddings, and the related layer.

## Two representations per page (the core principle)
- **Semantic** — a vision model's faithful **structured Markdown** transcription (flow as an ordered list, timeline as a table, diagram relationships spelled out). This is what gets **chunked + embedded + indexed** → retrieval finds the page by meaning. It is a *simplification*.
- **Factual** — the **full content kept beside it**: the rendered page **image**, the **verbatim text layer**, and **deterministically-extracted table grids** (`PyMuPDF.find_tables()` — a VLM misreads dense tables). This is what the agent pulls at **answer time** (via the MCP `page` tool) so no detail lost in the transcription is lost in the answer.

> **Retrieve on the semantic layer; generate from the factual content.** For any number in a table, cite the **extracted grid**, never the prose paraphrase.

## Pipeline (render → transcribe → assemble)
```
doc (pdf / pptx via soffice→pdf)
  → render_pages.py  (deterministic)   per-page PNG + text sidecar + table grids; FLAG visual pages
  → 🤖 vision agents (low-tier, VLM)    transcribe flagged pages → result_<k>.json {img_sha: markdown}
  → vision_assemble.py                 merge into one parsed .md: each page = a section with an
                                        `<!-- image: … -->` marker (text pages use their text layer)
  → knowledge-index index              chunks it; the marker → chunks.image; siblings hold text+tables
```

### 1. Render — `render_pages.py` (deterministic, no LLM)
`render_pages.py --doc <file> --out <assets> [--dpi 150] [--min-text 400] [--draw-thresh 40] [--cover 0.45] [--all]`
Writes `<assets>/<slug>/p<NN>.png`, `p<NN>.txt` (PyMuPDF text layer), `p<NN>.tables.md` (extracted grids), and `pages.json` (per-page `img_sha`, `text_len`, `n_drawings`, `n_tables`, `img_cover`, `flagged`). A page is flagged visual when text is thin, drawings are many, images cover the page, or `--all` (treat as a deck). PPTX/DOCX are converted to PDF via LibreOffice `soffice` first.

### 2. Transcribe — low-tier VISION subagents
Instantiate `vision_prep.py` to batch the **flagged, uncached** pages (image path + any extracted table + page context) with instructions, then dispatch vision subagents (model: a cheap vision model) that read each page image and emit faithful structured Markdown → `result_<k>.json` keyed by `img_sha`. Cache by `img_sha` (a page whose rendered image is unchanged is never re-transcribed).

### 3. Assemble — `vision_assemble.py`
`vision_assemble.py --render-dir <assets>/<slug> --out <parsed>/<doc>.md [--results <dir>] [--db <db>]`
Per page in order: the VLM Markdown (flagged) or the text layer (text page), under a `## p<NN> · <title>` heading with the image marker. Internal `#`/`##` are demoted so a page stays one section. Writes the `page_render` cache when `--db` is given.

## How the classifier / retrieval change
Nothing in the classifier or retriever changes — they just get **faithful input** instead of fragments. The classify agent now sees `North Star Vision & Service Design Blueprint / Future State Architecture / …` instead of `EPAM Proprietary & Confidential. 4`, so tagging, embeddings, and the related layer all improve for free. For genuinely visual edge cases, the classify/answer agent can also pull the page image (`page` MCP tool) and reason multimodally.

## Deps
`pymupdf` (render + text + `find_tables`) — torch-free. **LibreOffice `soffice`** (system dep) for .pptx/.docx. A cheap vision model for the transcription step (like the taxonomy/classify agents — meaning is agentic).
