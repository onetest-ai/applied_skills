---
name: corpus-taxonomy-extraction
description: Use when you need to induce a starting taxonomy (intent classes, entities/dimensions, and a metric inventory) from a heterogeneous document corpus (PDF/PPTX/DOCX/XLSX/MD) under a stated analytical goal — e.g. seeding a local knowledge graph, semantic layer, or classification scheme before building deterministic analytics. Goal-directed, agentic, runs bulk work on a low-tier model.
---

# Corpus Taxonomy Extraction

## Overview

Induce a **starting taxonomy** from a document corpus, bottom-up and goal-directed. The taxonomy is the seed that everything downstream keys off: the local graph and section classifier, conformed dimensions, and a governed semantic layer of metrics.

**Core principle — separate the two things by what makes each trustworthy:**
- **Meaning is agentic** (what's a term, what merges, what's in scope) → low-tier LLM + human gate.
- **Numbers are deterministic** (any actual value) → computed by code, never asserted by a model. This skill only *inventories* metrics and tags where each lives; it never states a numeric answer.

The goal statement is a **noise filter** — research shows business context is the single biggest accuracy lever. Every extracted term is kept **with provenance and confidence**; off-goal terms are *demoted*, never silently dropped.

## When to use

- Bootstrapping analytics/knowledge over a messy corpus where you don't yet have a schema.
- You have a clear analytical goal to scope extraction (e.g. "optimize the call center + introduce an AI workforce").
- You expect to feed the result into a local knowledge graph, semantic layer, or classifier.

**Not for:** answering a specific numeric question (that's the deterministic semantic-layer path), or one-off single-doc reading.

## The three metric classes (truthfulness split)

A discovered metric is tagged by how it should later be answered — **format does not decide this**:

| `source_type` | Meaning | How it gets answered later |
|---|---|---|
| `stated` | a figure/target quoted in prose/a slide (targets, exec summaries) | quote **with citation, labeled as reported** from the local RAG/evidence lane |
| `computable` | a measure derived from a data table (branch/day drill-down) | deterministic SQL over the mart |
| `both` | quoted *and* table-backed | compute authoritative value, **reconcile & flag discrepancies** |

## Portability & dependencies

Self-contained and corpus-agnostic — everything is driven by args + the goal string; no paths are hardcoded. Ships the taxonomy scripts (parse, consolidate, emit_taxonomy) + store scripts (chunking, build_graph, classify_prep/write, to_obsidian) + the map template in this skill dir. Requires a Python (3.9+) with **`pymupdf`, `openpyxl`** (torch-free). `.pptx/.docx` also need LibreOffice `soffice` (system dep). Run scripts with any such interpreter, e.g. `uv run --with pymupdf,openpyxl python <script>` or a venv that has them. The low-tier map/merge/judge steps assume a subagent mechanism with a model override (e.g. Haiku); on a different harness, substitute any cheap model that can read a file and emit JSON. To apply to a new corpus: pick a goal string, point `parse_corpus.py` at the corpus, instantiate the map template, run the pipeline.

## Pipeline (map → reduce → judge → emit)

```
goal + corpus + optional seed taxonomy
  → parse   (deterministic, no LLM)   parse_corpus.py  → uniform Markdown
  → map     (low-tier LLM, per doc)   candidate terms + evidence + provenance + confidence  (JSON/doc)
  → reduce  (deterministic + LLM)     consolidate.py clusters near-dupes → LLM adjudicates AMBIGUOUS merges only
  → judge   (low-tier LLM)            score coverage/coherence, flag low-confidence & unmapped
  → emit    taxonomy_v0.{json,md}     reviewable, versioned, with a demoted list
```

### 1. Parse — `parse_corpus.py` (deterministic, no LLM)
`python parse_corpus.py --corpus <dir> --out <dir> --formats pptx,docx,pdf,md,markdown,txt`
- PDF → PyMuPDF text layer; PPTX/DOCX → soffice→PDF→PyMuPDF; XLSX → openpyxl `read_only` structure dump. (Visual/diagram pages → the `visual-parse` skill.)
- **MD/MARKDOWN/TXT → passthrough.** Markdown is already the parsed-store format, so a pre-processed corpus is copied verbatim under the standard `# SOURCE:` header — no converter, no LibreOffice, no loss.
- **Taxonomy pass = narrative/summary formats only (`--formats pptx,docx,pdf,md,markdown,txt`).** Do NOT parse the big numeric workbooks — they explode into tens of MB of useless number-grid markdown and belong to the deterministic numeric lane, not here.

**VTT/SRT corpora — two-pass approach:**

VTT and SRT (meeting transcripts) are parsed separately from the taxonomy induction pass because raw cue fragments produce noise-heavy taxonomy. Use PDFs/slides for taxonomy induction; use VTT/SRT for the knowledge index:

```bash
# Taxonomy pass — narrative docs only (PDFs, slides)
python parse_corpus.py --corpus <docs> --out <project>/map_parsed --formats pptx,docx,pdf,md,markdown,txt

# Knowledge-index pass — transcripts (separate output dir, --merge-cues required)
python parse_corpus.py --corpus <docs> --out <project>/parsed --formats vtt,srt --merge-cues 10
```

`--merge-cues 10` joins up to 10 consecutive same-speaker cues into one speaker-turn paragraph before chunking. **Omitting it produces ~25k single-line chunks averaging 79 chars — classification agents correctly return `[]` for nearly all of them and retrieval quality collapses.** Always pass `--merge-cues N > 1` for VTT/SRT.

### 2. Map — low-tier subagents (Haiku), one batch per subagent
Instantiate `map_instructions.template.md` (shipped with this skill): replace `{{GOAL}}` with the run's goal, `{{MAP_DIR}}` with the run's map-output dir, and `{{AUDIENCE}}` with the project audience (`brain.toml` `[project].audience`, or the store's `health().about.audience`; leave it empty if none); write it to the run dir as `map_instructions.md`. The audience is a **secondary** emphasis lens — it re-orders which goal-relevant intents/dimensions to favor and nudges vocabulary; the goal stays the primary filter and audience never drops a goal-relevant term. Dispatch subagents (model: haiku) that read that instantiated file + their assigned parsed files and write one JSON per source into the map dir. The bulk document context lives and dies inside each subagent — the orchestrator only sees compact JSON. Extract `intent_classes`, `metrics` (with `source_type`), `entities`; each item carries `evidence` (≤200-char quote), `source`, `confidence`. Give any anchor taxonomy doc its own subagent.

### 3. Reduce — `consolidate.py` (deterministic) + LLM adjudication
`python consolidate.py --map-dir map --out consolidated.json --threshold 0.86`
- Pools terms by kind, normalizes names, fuzzy-clusters near-duplicates (stdlib difflib; swap in embeddings if fragmentation is high).
- Flags clusters with >1 surface form as `ambiguous` → a low-tier subagent adjudicates **only those** ("Chicago" vs "Chicago Branch" vs "CHI" → merge?). This is where the real effort is (entity resolution), but it's bounded to ambiguous clusters.

### 4. Judge — low-tier LLM-as-judge
Score the draft for coverage (did we miss obvious goal-relevant categories?) and coherence (L1/L2 consistency); flag low-confidence and unmapped terms for human review.

### 5. Emit — `taxonomy_v0.{json,md}`
Human-reviewable artifact: intent hierarchy + entity/dimension candidates + metric inventory (each tagged computed/stated/both, with provenance), plus a **demoted** list. Versioned — it's a starting point that grows, not ground truth.

## Companion scripts — populate the local knowledge SQLite
Beyond taxonomy induction, this skill ships the scripts that wire the taxonomy into the one `knowledge.sqlite` store (shared with `knowledge-index` + `tabular-semantic-layer`):
- **`chunking.py`** — the shared heading-aware chunker (a chunk = a section = an Obsidian note = a retrieval unit). Identical copy in `knowledge-index`.
- **`build_graph.py`** — taxonomy → `graph_nodes`/`graph_edges` (L1/L2 vertices, `subclass_of`). **Optional second taxonomy + traceability:** run this skill a second time with a **capability/vision-pillar goal** to induce a `capabilities.json` (same `intent_taxonomy` shape, or under a `capability_taxonomy` key), then `build_graph.py --capabilities capabilities.json --links addressed_by.json` layers in `capability_l1/l2` nodes and **`intent --addressed_by--> capability`** edges. Now a *problem → capability* traceability question resolves as a graph JOIN (see `hybrid-retrieval`) instead of narrative synthesis. `--links` is `{"addressed_by": [{"intent","capability"}, …]}`; a pair whose endpoints aren't known nodes is skipped (never a dangling edge). The script OWNS `subclass_of` + `addressed_by` (rebuilt each run, idempotent) and still never touches `about` edges.
- **`classify_prep.py` → (low-tier agents) → `classify_write.py`** — per-section taxonomy tags at **L1 AND L2**: prep presents the full L1/L2 vocab and asks for the *most specific* fit (an L2 when the chunk is specifically about it, else its L1); agents assign (empty when nothing fits — never forced); write resolves each label to its graph node, writes `chunk_topics` with the real `kind` and — for an L2 — **rolls up its parent L1** so L1 filters still catch it. `about` edges (chunk→vertex). *Meaning is agentic; this step is agents, not a script.*

## Refreshing the taxonomy (assisted: agent proposes, human decides)
The corpus or the parse shifts (e.g. `visual-parse` now transcribes diagrams, surfacing concepts that were invisible before), so the taxonomy under-covers — the signal is a rising share of **untagged** chunks. **Agents only add.** Renames, merges, moves, splits and removals are human decisions made in the taxonomy review app, which migrates the affected tags (node ids are `slug(label)`, so an unreviewed rename would make `build_graph` prune the node and its tags).
- **`taxonomy_refine_prep.py --db --taxonomy --out`** — gather the UNTAGGED chunks + the current L1/L2 vocab; batch them for agents.
- **(low-tier agents)** — per batch, either map a chunk to an existing category the classifier missed, or **propose** a new **L2 under a named parent L1** (preferred) / a new **L1** — with evidence → `result_k.json`.
- **Review in the app** — `taxonomy_review.py plan --mode drift --taxonomy taxonomy/current.json --proposals <dir> --db <db>` freezes a review (dedups against the vocabulary and aliases, suppresses proposals rejected before) and prints its path. Then run `taxonomy_review.py serve --review <path> --db <db>` **in the background** and tell the user a browser tab is open for them; end your turn. It exits when they click Submit, and that exit wakes you.
- **Apply** — `taxonomy_merge.py --review <path> --apply` writes `taxonomy_v<N+1>.json` + `taxonomy/current.json` from exactly what was submitted. Never pass `--without-review` unless the user explicitly asked in this conversation to skip the review.
- Then: `build_graph.py --taxonomy taxonomy/current.json --db <db>` (runs the tag migrations) → reclassify the ids in `taxonomy/work/reclassify.json` if it exists (`classify_prep --chunks <ids>` → agents → `classify_write --reclassify-done taxonomy/work/reclassify.json`) → `related` → vault.
- **`to_obsidian.py`** — emit the Obsidian vault as a **view of the store**: notes = chunks, real per-section tags from `chunk_topics`, `[[topic · …]]` links = graph vertices.

## Reviewing and editing the taxonomy (the review app)
One local app for every taxonomy decision. `taxonomy_review.py` plans a review and serves it on `127.0.0.1` (token-guarded, stdlib only, the store opened read-only); decisions go to the append-only `taxonomy/decisions.jsonl` (commit it in the consuming project); `taxonomy_merge.py --review … --apply` is the only writer.

| when | plan command |
|---|---|
| first build, after `emit_taxonomy.py` | `plan --mode draft --taxonomy taxonomy/taxonomy_v0.json` (evidence from `taxonomy/work/consolidated.json`) |
| refresh with agent proposals | `plan --mode drift --taxonomy taxonomy/current.json --proposals <dir> --db <db>` |
| the user wants to see or change the taxonomy or metric inventory | `plan --mode browse --taxonomy taxonomy/current.json --db <db>` |

Then `serve --review <path> --db <db>` in the background → end your turn → on exit, `taxonomy_merge.py --review <path> --apply` → `build_graph.py --taxonomy taxonomy/current.json` → reclassify `work/reclassify.json`.

- **Tree** tab: the whole taxonomy with tag counts, samples and sibling overlap; the reviewer renames, merges, moves, splits, removes or adds, each with an impact preview.
- **Metrics** tab: the metric inventory, with whether each computable metric has a governed definition in `schema/metrics.<corpus>.json` (ungoverned first — kb answers those "not modeled").
- **Changes** tab: approve / reject (with a reason; rejections stick) / amend proposals, then submit.
- No browser (SSH, other hosts): `taxonomy_review.py export-md --review <path> --out review.md`, ask the user to fill in the `decision:` lines, then `import-md --review <path> --md review.md --submit`.
- A Brain built before `current.json` existed: run `taxonomy_review.py adopt --taxonomy taxonomy/taxonomy_v<N>.json --db <db>` once (after telling the user), choosing the version the store was built from; it refuses if that version doesn't match the graph.
- `record` exists for scripts and only enters additions; never use it to enter a decision the user did not make.

## Downstream wiring

- **Intent classes** → the local classification scheme and `graph_nodes`/`graph_edges`, keeping section tags consistent with the reviewed L1/L2 vocabulary.
- **Entities** → conformed dimensions (Region→Division→Branch→RSR) shared by the local graph and marts.
- **Metrics** → governed semantic-layer definitions; `computable` ones get SQL over the marts, `stated` ones stay citation-backed.

## Guardrails / common mistakes

- **Never let the map/judge model emit a numeric answer.** It records *stated* values as quotes-with-provenance only; real computation is deterministic and downstream.
- **Keep provenance + a demoted list** — the goal lens can over-filter; make demotion visible and reversible, never a silent delete.
- **Don't parse giant numeric workbooks here.** Restrict `--formats` to narrative/summary types.
- **Seed-guided beats schema-free** — anchor on any existing taxonomy (e.g. a "Taxonomy Compendium") and the existing knowledge graph; extend rather than invent.
- Treat `taxonomy_v0` as a draft: ratify it in the review app (`plan --mode draft`) before `build_graph`. Every later step reads `taxonomy/current.json`, never a versioned file.
