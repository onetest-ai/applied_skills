---
name: corpus-taxonomy-extraction
description: Use when you need to induce a starting taxonomy (intent classes, entities/dimensions, and a metric inventory) from a heterogeneous document corpus (PDF/PPTX/DOCX/XLSX) under a stated analytical goal — e.g. seeding an ontology, a semantic layer, or classification scheme before building deterministic analytics. Goal-directed, agentic, runs bulk work on a low-tier model.
---

# Corpus Taxonomy Extraction

## Overview

Induce a **starting taxonomy** from a document corpus, bottom-up and goal-directed. The taxonomy is the seed that everything downstream keys off: a Cognee ontology (grounds narrative extraction), conformed dimensions, and a governed semantic layer of metrics.

**Core principle — separate the two things by what makes each trustworthy:**
- **Meaning is agentic** (what's a term, what merges, what's in scope) → low-tier LLM + human gate.
- **Numbers are deterministic** (any actual value) → computed by code, never asserted by a model. This skill only *inventories* metrics and tags where each lives; it never states a numeric answer.

The goal statement is a **noise filter** — research shows business context is the single biggest accuracy lever. Every extracted term is kept **with provenance and confidence**; off-goal terms are *demoted*, never silently dropped.

## When to use

- Bootstrapping analytics/knowledge over a messy corpus where you don't yet have a schema.
- You have a clear analytical goal to scope extraction (e.g. "optimize the call center + introduce an AI workforce").
- You expect to feed the result into an ontology, semantic layer, or classifier.

**Not for:** answering a specific numeric question (that's the deterministic semantic-layer path), or one-off single-doc reading.

## The three metric classes (truthfulness split)

A discovered metric is tagged by how it should later be answered — **format does not decide this**:

| `source_type` | Meaning | How it gets answered later |
|---|---|---|
| `stated` | a figure/target quoted in prose/a slide (targets, exec summaries) | quote **with citation, labeled as reported** (RAG/Cognee is good here) |
| `computable` | a measure derived from a data table (branch/day drill-down) | deterministic SQL over the mart |
| `both` | quoted *and* table-backed | compute authoritative value, **reconcile & flag discrepancies** |

## Portability & dependencies

Self-contained and corpus-agnostic — everything is driven by args + the goal string; no paths are hardcoded. Ships four scripts + one prompt template in this skill dir. Requires a Python (3.9+) with **`docling`, `pypdf`, `openpyxl`** (all torch-free for PPTX/PDF/XLSX; PDF uses pypdf so no ML models needed). Run scripts with any such interpreter, e.g. `uv run --with docling,pypdf,openpyxl python <script>` or a venv that has them. The low-tier map/merge/judge steps assume a subagent mechanism with a model override (e.g. Haiku); on a different harness, substitute any cheap model that can read a file and emit JSON. To apply to a new corpus: pick a goal string, point `parse_corpus.py` at the corpus, instantiate the map template, run the pipeline.

## Pipeline (map → reduce → judge → emit)

```
goal + corpus + optional seed taxonomy
  → parse   (deterministic, no LLM)   parse_corpus.py  → uniform Markdown
  → map     (low-tier LLM, per doc)   candidate terms + evidence + provenance + confidence  (JSON/doc)
  → reduce  (deterministic + LLM)     consolidate.py clusters near-dupes → LLM adjudicates AMBIGUOUS merges only
  → judge   (low-tier LLM)            score coverage/coherence, flag low-confidence & unmapped
  → emit    taxonomy_v0.{json,md}     reviewable, versioned, with a demoted list
```

### 1. Parse — `parse_corpus.py` (deterministic, torch-free)
`python parse_corpus.py --corpus <dir> --out <dir> --formats pptx,docx,pdf`
- PPTX/DOCX → Docling (simple backend, no torch); PDF → pypdf; XLSX small → Docling, large → openpyxl `read_only` structure dump.
- **Taxonomy pass = narrative/summary formats only (`--formats pptx,docx,pdf`).** Do NOT Docling the big numeric workbooks — they explode into tens of MB of useless number-grid markdown and belong to the deterministic numeric lane, not here.

### 2. Map — low-tier subagents (Haiku), one batch per subagent
Instantiate `map_instructions.template.md` (shipped with this skill): replace `{{GOAL}}` with the run's goal and `{{MAP_DIR}}` with the run's map-output dir; write it to the run dir as `map_instructions.md`. Dispatch subagents (model: haiku) that read that instantiated file + their assigned parsed files and write one JSON per source into the map dir. The bulk document context lives and dies inside each subagent — the orchestrator only sees compact JSON. Extract `intent_classes`, `metrics` (with `source_type`), `entities`; each item carries `evidence` (≤200-char quote), `source`, `confidence`. Give any anchor taxonomy doc its own subagent.

### 3. Reduce — `consolidate.py` (deterministic) + LLM adjudication
`python consolidate.py --map-dir map --out consolidated.json --threshold 0.86`
- Pools terms by kind, normalizes names, fuzzy-clusters near-duplicates (stdlib difflib; swap in embeddings if fragmentation is high).
- Flags clusters with >1 surface form as `ambiguous` → a low-tier subagent adjudicates **only those** ("Chicago" vs "Chicago Branch" vs "CHI" → merge?). This is where the real effort is (entity resolution), but it's bounded to ambiguous clusters.

### 4. Judge — low-tier LLM-as-judge
Score the draft for coverage (did we miss obvious goal-relevant categories?) and coherence (L1/L2 consistency); flag low-confidence and unmapped terms for human review.

### 5. Emit — `taxonomy_v0.{json,md}`
Human-reviewable artifact: intent hierarchy + entity/dimension candidates + metric inventory (each tagged computed/stated/both, with provenance), plus a **demoted** list. Versioned — it's a starting point that grows, not ground truth.

## Downstream wiring

- **Intent classes** → classification scheme + Cognee ontology (OWL) to ground future cognify passes (cuts narrative noise too).
- **Entities** → conformed dimensions (Region→Division→Branch→RSR) shared by the graph and the marts — this shared vocabulary IS the Cognee↔DB link.
- **Metrics** → governed semantic-layer definitions; `computable` ones get SQL over the marts, `stated` ones stay citation-backed.

## Guardrails / common mistakes

- **Never let the map/judge model emit a numeric answer.** It records *stated* values as quotes-with-provenance only; real computation is deterministic and downstream.
- **Keep provenance + a demoted list** — the goal lens can over-filter; make demotion visible and reversible, never a silent delete.
- **Don't parse giant numeric workbooks here.** Restrict `--formats` to narrative/summary types.
- **Seed-guided beats schema-free** — anchor on any existing taxonomy (e.g. a "Taxonomy Compendium") and the existing knowledge graph; extend rather than invent.
- Treat `taxonomy_v0` as a draft for human ratification, especially the ambiguous merges.
