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

## HTML decks — the capture step

HTML is a continuous medium, not a paginated one: there is no print page to render, so the
unit of capture comes from the DOM. This lane substitutes a **capture step** in front of the
same render→transcribe→assemble pipeline above; nothing downstream of `pages.json` changes.

### Providers and their fidelity

| Provider | Fidelity | When |
|---|---|---|
| Playwright MCP | full | Headless, scriptable — batch runs and CI, where no human needs to watch it happen. |
| Claude in Chrome | full | Drives the user's own Chrome — desktop/Cowork sessions, or when a browser connection already exists and spinning up a second one is wasteful. |
| PyMuPDF (no browser) | degraded | No browser available at all. DOM text only — no JavaScript execution, no images, no screenshots. This is `parse_corpus.py`'s existing `.html`/`.htm` branch, not a new script. |

Full fidelity requires an actual browser (Playwright MCP or Claude in Chrome) because only a
browser executes the page's JavaScript and lets you screenshot the result. The degraded path
never touches the vision lane: it produces text and a `# fidelity: degraded` header in the
parsed Markdown, and nothing else — no `pages.json`, no images, no VLM transcription. A
JS-rendered deck run through the degraded path yields almost no text; `parse_corpus.py`
detects this (mirroring `render_pages.py`'s `--min-text` threshold) and returns
`skipped-js-rendered` rather than storing a near-empty document as if it were the real deck.

### Sequence (full-fidelity path)

1. **Segment.** Evaluate `html_segments.js` in the page through the provider (Playwright
   MCP's `browser_evaluate`, or Claude in Chrome's `javascript_tool`). It returns one entry
   per logical segment — an explicit slide container when the deck has one, otherwise a
   heading-led section — with each segment's bounding box, verbatim text, and any DOM
   tables. Write that payload to `segments.json`; it must pass `html_capture.py`'s
   `validate_segments`.
2. **Plan.**
   `html_capture.py plan --segments segments.json --out plan.json [--max-px 1600] [--overlap 0.1]`
   Writes one capture instruction per image the provider must take. A segment shorter than
   `--max-px` is one capture; a taller one is tiled with `--overlap` (default 10%) shared
   with its neighbour so a line of text straddling a seam still appears whole in at least
   one tile — vision models downscale large images, and an illegible capture yields a
   confident, wrong transcription. Every plan entry carries a `segment` index; tiles of one
   segment share it.
3. **Capture.** For each entry in `plan.json`, screenshot exactly the entry's `clip` region
   through the same provider and save it to `<assets>/<slug>/p<NN>.png` (`NN` = the entry's
   `page` number, zero-padded, matching the plan in order). One screenshot per plan entry —
   this is the one step in the sequence a script cannot do, because only the provider can
   render and capture pixels.
4. **Assemble.**
   `html_capture.py assemble --segments segments.json --plan plan.json --outdir <assets>/<slug> [--dpi 96]`
   Hashes each PNG that step 3 wrote, writes the `p<NN>.txt` verbatim-text sidecar and (once
   per segment) `p<NN>.tables.md` from the segment's DOM tables, and writes `pages.json` in
   **exactly** the shape `render_pages.py` produces — plus an additive `segment` field
   grouping a tall segment's tiles. Because the shape matches, `vision_prep.py` needs no
   change to consume it.
5. **Continue unchanged.** From here the pipeline is identical to a PPTX/PDF deck's: run
   `vision_prep.py` against `<assets>/<slug>`, dispatch the vision subagents, then
   `vision_assemble.py` — which merges a segment's tiles into ONE section before emitting the
   parsed Markdown, so a citation resolves to "the segment", never to an arbitrary vertical
   slice of it.

```bash
python <skills>/visual-parse/html_capture.py plan --segments segments.json --out plan.json
# provider takes one screenshot per plan entry into <assets>/<slug>/pNN.png
python <skills>/visual-parse/html_capture.py assemble --segments segments.json --plan plan.json --outdir <assets>/<slug>
```

HTML's DOM tables extract more reliably than a rendered PDF's: `render_pages.py` infers a
grid from `find_tables()` over a raster image, while `html_segments.js` reads real `<table>`
cells directly, and a table is never split across tiles because the grid comes from the DOM,
not from the image.

### Security position — this pipeline does not mitigate this risk

Rendering an HTML document **executes its JavaScript**. Playwright MCP and Claude in Chrome
own the browser, so file-access and network controls (what a page is allowed to read or
reach) are **the provider's configuration**, not this pipeline's — `html_segments.js` and
`html_capture.py` are deterministic and have no say over what the browser was permitted to
do before they ever ran.

The concrete risk: an untrusted HTML document can call `fetch('file:///…')` (or embed a local
path) from its own JavaScript, and whatever it gets back is rendered on the page, screenshotted,
transcribed by the VLM, and indexed as an ordinary citable chunk in the knowledge store —
turning "ingest this deck" into a read primitive whose output lands in the corpus. Do not
render HTML from a source you would not otherwise trust to execute code, and configure the
provider's own file-access and network policy before pointing it at that source. This skill
does not sanitize HTML before rendering (stripping `<script>` breaks the JS-rendered decks
that are much of the corpus, and a partial sanitizer only gives false confidence), so there is
no mitigation here to rely on.

## How the classifier / retrieval change
Nothing in the classifier or retriever changes — they just get **faithful input** instead of fragments. The classify agent now sees `ProjectAlpha Vision & Service Design Blueprint / Future State Architecture / …` instead of `Confidential — Page 4`, so tagging, embeddings, and the related layer all improve for free. For genuinely visual edge cases, `get_evidence` returns the page asset path for a capable local client to open and reason over multimodally.

## Deps
`pymupdf` (render + text + `find_tables`) — torch-free. **LibreOffice `soffice`** (system dep) for .pptx/.docx. A cheap vision model for the transcription step (like the taxonomy/classify agents — meaning is agentic).
