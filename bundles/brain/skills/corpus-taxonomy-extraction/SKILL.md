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
**The default way to refresh is the health review** (next section): its `untagged/` task runs this same refine step, alongside every other taxonomy problem. Use the standalone drift flow below only when the user asks for new-category proposals alone.

The corpus or the parse shifts (e.g. `visual-parse` now transcribes diagrams, surfacing concepts that were invisible before), so the taxonomy under-covers — the signal is a rising share of **untagged** chunks. **Agents only add.** Renames, merges, moves, splits and removals are human decisions made in the taxonomy review app, which migrates the affected tags (node ids are `slug(label)`, so an unreviewed rename would make `build_graph` prune the node and its tags).
- **`taxonomy_refine_prep.py --db --taxonomy --out`** — gather the UNTAGGED chunks + the current L1/L2 vocab; batch them for agents.
- **(low-tier agents)** — per batch, either map a chunk to an existing category the classifier missed, or **propose** a new **L2 under a named parent L1** (preferred) / a new **L1** — with evidence → `result_k.json`.
- **Review in the app** — `taxonomy_review.py plan --mode drift --taxonomy taxonomy/current.json --proposals <dir> --db <db>` freezes a review (dedups against the vocabulary and aliases, suppresses proposals rejected before) and prints its path. Then run `taxonomy_review.py serve --review <path> --db <db>` **in the background** and tell the user a browser tab is open for them; end your turn. It exits when they click Submit, and that exit wakes you.
- **Apply** — `taxonomy_merge.py --review <path> --apply` writes `taxonomy_v<N+1>.json` + `taxonomy/current.json` from exactly what was submitted. Never pass `--without-review` unless the user explicitly asked in this conversation to skip the review.
- Then: `build_graph.py --taxonomy taxonomy/current.json --db <db>` (runs the tag migrations) → reclassify the queued chunks (see **Reclassifying after a taxonomy change** below) → `related` → vault.
- **`to_obsidian.py`** — emit the Obsidian vault as a **view of the store**: notes = chunks, real per-section tags from `chunk_topics`, `[[topic · …]]` links = graph vertices.

## Health review (the default way to review)
One review for every taxonomy and metric problem. A deterministic `diagnose` finds the problems; low-cost agents precompute a specific fix for each one that needs judgment; the user decides in a grouped inbox; the approved fixes are applied. The first-build `draft` review and the `browse` editor stay separate (see the table below).

Every command below runs from the project root, with `<skills>` the skills directory and `<db>` the store. `<run>` is the work directory for this run, e.g. `health` (reused every run) or `health-20260921-1400` (to keep each run's files).

1. **You run diagnose.**
   ```bash
   python <skills>/corpus-taxonomy-extraction/taxonomy_review.py diagnose \
     --taxonomy taxonomy/current.json --db <db> --out taxonomy/work/<run>
   ```
   It prints one JSON line: `problems` (a count per kind) and `tasks` (`[{kind, dir, batches}]`, one per kind that needs agents: `describe`, `notags`, `structure`, `metrics`, `untagged`). Reusing `--out` is safe: `diagnose` first removes an earlier run's `batch_*.json` and `result_*.json` from its task dirs (it names them on stderr and in `removed_stale`), so `plan` never reads old fixes. A fresh `--out` per run is optional, for keeping history. `diagnose` also reads open redo requests from `taxonomy/work/requests.jsonl` and puts their notes on the matching entries; a request already answered by `respond`, or on an item accepted in a review that was since applied, is not reopened.
2. **You dispatch low-cost subagents (e.g. Haiku), one per `batch_k.json`** in each task `dir`. Each subagent reads that dir's `instructions.md` and its `batch_k.json` and writes `result_k.json` in the same dir. The `untagged` dir also has `vocab.md` for the agent to read, and the `metrics` dir may have `families.json`. The result formats are defined in each `instructions.md`; do not restate them to the agents, point them at the file. Check that each batch has its result file before going on. A missing or malformed result is not fatal: that problem still reaches the inbox with a safe default, marked `fallback`.
3. **You plan the review.**
   ```bash
   python <skills>/corpus-taxonomy-extraction/taxonomy_review.py plan --mode health \
     --taxonomy taxonomy/current.json --work taxonomy/work/<run> --db <db>
   ```
   It prints `{review, review_id, items, …}`; `<review>` below is that `review` path. Any `skipped_files` are result files that could not be read. Name them to the user; do not re-dispatch silently.
4. **You serve the app and watch for redo requests.** Run this with Bash `run_in_background`:
   ```bash
   python <skills>/corpus-taxonomy-extraction/taxonomy_review.py serve --review <review> --db <db> --watch-hint
   ```
   Then create the requests file if it is missing (`mkdir -p taxonomy/work && touch taxonomy/work/requests.jsonl`) and arm a **Monitor** on:
   ```bash
   tail -n0 -F taxonomy/work/requests.jsonl
   ```
   Set `timeout_ms` to 1800000 (30 minutes). When the Monitor expires while `serve` is still running, arm it again. Tell the user a browser tab is open for them, then end your turn.

   Each Monitor event is one JSON line `{"id", "ts", "review_id", "item_id", "note", "status": "open"}`: the user pressed **Redo with a note…** on an item. For each one:
   1. Prepare the entry:
      ```bash
      python <skills>/corpus-taxonomy-extraction/taxonomy_review.py redo-prep --review <review> --item <item_id>
      ```
      It prints `{kind, dir, entry, instructions, result, note}`: `entry` is a `batch_0.json` holding the one task entry the item came from, with the note added, next to a copy of that task's `instructions.md`. `plan` never reads this directory.
   2. Have one subagent (or yourself, for a single entry) follow `instructions` for `entry` and write `result`.
   3. Turn the new fix into the item's op shape: one of the item's `op`/`alternatives` in `<review>`, or the same op type on the same subject with edited fields (a different description, a narrower `chunk_ids`). Check it without recording anything:
      ```bash
      python <skills>/corpus-taxonomy-extraction/taxonomy_review.py respond --review <review> \
        --request <id> --op '<op json>' --check
      ```
      `{"status": "ok"}` means `respond` will accept it; on `refused`, pick an op from the item's alternatives instead.
   4. Record it:
      ```bash
      python <skills>/corpus-taxonomy-extraction/taxonomy_review.py respond --review <review> \
        --request <id> --op '<op json>' --reason '<one line: what changed and why>'
      ```
   The app picks the response up within seconds and shows it as "Revised by Claude"; the user approves it or not.
5. **When `serve` exits, stop the Monitor** (TaskStop). `serve` prints one JSON line. Go on only if its `status` is `submitted`; on `cancelled` or `timeout`, tell the user and stop.
6. **You apply the review and rebuild the graph.**
   ```bash
   python <skills>/corpus-taxonomy-extraction/taxonomy_merge.py --review <review> --apply
   python <skills>/corpus-taxonomy-extraction/build_graph.py --taxonomy taxonomy/current.json --db <db>
   ```
   `taxonomy_merge` prints `{status, version, tags_file, governed_drafts_file, taxonomy_changed, …}`. It writes a new taxonomy version only if at least one taxonomy op was approved; run `build_graph` either way.
7. **You reclassify, if needed.** If `taxonomy/work/reclassify.json` exists, run the sequence in **Reclassifying after a taxonomy change** below. Do this before step 8: reclassifying replaces a chunk's tags, so it would drop tags added in step 8.
8. **You write the approved tags, if any.** If `tags_file` is not null:
   ```bash
   python <skills>/corpus-taxonomy-extraction/classify_write.py --db <db> --results <the directory of tags_file> --merge
   ```
   `--merge` only adds labels (an L2 also adds its parent L1); it never removes a tag.
9. **You tell the user about governed-metric drafts, if any.** If `governed_drafts_file` is not null, it holds the approved `metric_govern` drafts (`{review_id, metric, draft:{key, family, unit, desc, grain}}`, appended across reviews; this review's entries carry its `review_id`). Show the user this review's drafts and ask whether to add them to `schema/metrics.<corpus>.json` with the `tabular-semantic-layer` skill. Edit that file only if they agree. Neither the app nor `taxonomy_merge` ever writes it.

**What the user sees.** Problems are grouped: a group (e.g. "12 categories have no description") is one inbox entry with a row per item and **Accept all remaining**, which leaves `fallback` rows (a safe default, not an agent's recommendation) for the user to decide one by one. **Skip** records nothing; it only moves on, and a skipped problem comes back at the next `diagnose`. A group of labels that differ only by a number or code (a naming pattern) is one item whose default is keep, with no agent work. A near-duplicate cluster of three or more labels is one agent entry and comes back as a group of merge rows. A fix on a node that another approved fix already changes (e.g. describing a label that is merged away) is refused as a conflict. **Accept all remaining** leaves such rows out and says how many ("2 rows skipped: their category is being merged away, renamed or removed"); if a conflict still slips through, nothing is recorded and the app offers to accept the other rows. A naming-pattern item has no **Redo with a note…**; change it in the Taxonomy view.

**Hosts without Monitor.** Skip the Monitor and run `serve` without `--watch-hint`; the app then says requests are "Queued for next run". Open requests stay in `taxonomy/work/requests.jsonl`, and the next `diagnose` puts their notes on the matching entries. No browser: `export-md` / `import-md` as below (health decisions are `approve`, `reject: <reason>` or `amend: <op json>`).

## Reviewing and editing the taxonomy (the review app)
One local app for every taxonomy decision. `taxonomy_review.py` plans a review and serves it on `127.0.0.1` (token-guarded, stdlib only, the store opened read-only); decisions go to the append-only `taxonomy/decisions.jsonl` (commit it in the consuming project); `taxonomy_merge.py --review … --apply` is the only writer.

| when | plan command |
|---|---|
| **refresh, maintenance, or "check / clean up the taxonomy" (the default)** | the **Health review** sequence above (`diagnose` → agents → `plan --mode health --work …`) |
| first build, after `emit_taxonomy.py` | `plan --mode draft --taxonomy taxonomy/taxonomy_v0.json` (evidence from `taxonomy/work/consolidated.json`) |
| only new-category proposals for untagged chunks (the health review includes these) | `plan --mode drift --taxonomy taxonomy/current.json --proposals <dir> --db <db>` |
| only descriptions, when the user asks for nothing else (the health review includes these) | `describe-prep --taxonomy taxonomy/current.json --db <db> --out <dir>`, dispatch agents on `<dir>/batch_k.json` with `<dir>/instructions.md` → `plan --mode describe --taxonomy taxonomy/current.json --proposals <dir> --db <db>` |
| the user wants to see or change the taxonomy or metric inventory | `plan --mode browse --taxonomy taxonomy/current.json --db <db>` |

For the non-health modes: `serve --review <path> --db <db>` in the background → end your turn → on exit, `taxonomy_merge.py --review <path> --apply` → `build_graph.py --taxonomy taxonomy/current.json --db <db>` → reclassify the queued chunks as below.

**Reclassifying after a taxonomy change.** If `taxonomy/work/reclassify.json` exists after `build_graph`, run this sequence yourself, where `<N>` is the `version` in that file:

```bash
IDS=$(python -c 'import json;print(",".join(map(str,json.load(open("taxonomy/work/reclassify.json"))["chunk_ids"])))')
python classify_prep.py --db <db> --taxonomy taxonomy/current.json --chunks "$IDS" --out classify/reclassify-v<N>
# dispatch low-tier classification agents over classify/reclassify-v<N>/batch_*.json → result_*.json in the same dir
python classify_write.py --db <db> --results classify/reclassify-v<N> --reclassify-done taxonomy/work/reclassify.json
```

Always use a fresh `classify/reclassify-v<N>` directory with no `result_*.json` in it. Never reuse the first-build `classify/` directory: `classify_write` reads every `result_*.json` there, so stale first-build results would overwrite the new tags and `--reclassify-done` would drop the queued ids as done. `--reclassify-done` removes only the ids it just wrote and deletes the file once it is empty.

- **Inbox**: the proposals and flags to decide — approve, reject (with a reason; rejections stick), amend, or Redo with a note (health reviews).
- **Taxonomy**: the whole tree with tag counts, samples and sibling overlap. This is where a person renames, merges, moves, splits, removes, describes or **adds** a category, each with an impact preview. A new category needs a description.
- **Metrics**: the metric inventory in tabs by what each means (needs a definition, possible duplicates, quoted from documents, governed). A computable metric with no governed definition in `schema/metrics.<corpus>.json` is answered "not modeled" by kb.
- **Your changes**: the user's own proposals, which they can withdraw, then Submit.
- **Descriptions matter for classification.** A category's description goes into the classifier's `vocab.md`, the graph, `get_taxonomy` and the vault, so a described taxonomy tags sections more precisely.
- No browser (SSH, other hosts): `taxonomy_review.py export-md --review <path> --out review.md`, ask the user to fill in the `decision:` lines, then `import-md --review <path> --md review.md --submit`.
- A Brain built before `current.json` existed: run `taxonomy_review.py adopt --taxonomy taxonomy/taxonomy_v<N>.json --db <db>` once (after telling the user), choosing the version the store was built from; it refuses if that version doesn't match the graph. `browse`/`drift` reviews are refused until this is done.
  - If `taxonomy/current.json` already exists and is ahead of the store (e.g. `build_graph` reports a store with tags but no `meta.taxonomy_version`), add `--meta-only`: it checks the same node set, records only the store's version, and never touches `current.json`; then rebuild with `build_graph.py --taxonomy taxonomy/current.json --db <db>`.
  - Pass `--force` only when the user explicitly says to replace a differing `current.json` or to adopt despite a node mismatch.
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
