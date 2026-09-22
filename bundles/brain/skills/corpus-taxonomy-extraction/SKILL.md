---
name: corpus-taxonomy-extraction
description: Use when you need to induce a starting taxonomy (intent classes, entities/dimensions, and a metric inventory) from a heterogeneous document corpus (PDF/PPTX/DOCX/XLSX/MD) under a stated analytical goal — e.g. seeding a local knowledge graph, semantic layer, or classification scheme before building deterministic analytics — or when the user wants to review, refresh, clean up, describe or edit an existing Brain's taxonomy or metric inventory in the local review app. Goal-directed, agentic, runs bulk work on a Sonnet model.
---

# Corpus Taxonomy Extraction

## Overview

Induce a **starting taxonomy** from a document corpus, bottom-up and goal-directed. The taxonomy is the seed that everything downstream keys off: the local graph and section classifier, conformed dimensions, and a governed semantic layer of metrics.

**Core principle — separate the two things by what makes each trustworthy:**
- **Meaning is agentic** (what's a term, what merges, what's in scope) → Sonnet LLM + human gate.
- **Numbers are deterministic** (any actual value) → computed by code, never asserted by a model. This skill only *inventories* metrics and tags where each lives; it never states a numeric answer.

The goal statement is a **noise filter** — research shows business context is the single biggest accuracy lever. Every extracted term is kept **with provenance and confidence**; off-goal terms are *demoted*, never silently dropped.

## When to use

- Bootstrapping analytics/knowledge over a messy corpus where you don't yet have a schema.
- You have a clear analytical goal to scope extraction (e.g. "optimize the call center + introduce an AI workforce").
- You expect to feed the result into a local knowledge graph, semantic layer, or classifier.
- The user wants to review, check, clean up or edit an existing taxonomy (see **Reviewing and editing the taxonomy**).

**Not for:** answering a specific numeric question (that's the deterministic semantic-layer path), or one-off single-doc reading.

## The three metric classes (truthfulness split)

A discovered metric is tagged by how it should later be answered — **format does not decide this**:

| `source_type` | Meaning | How it gets answered later |
|---|---|---|
| `stated` | a figure/target quoted in prose/a slide (targets, exec summaries) | quote **with citation, labeled as reported** from the local RAG/evidence lane |
| `computable` | a measure derived from a data table (branch/day drill-down) | deterministic SQL over the mart |
| `both` | quoted *and* table-backed | compute authoritative value, **reconcile & flag discrepancies** |

## Running the scripts

Run every command from the brain project root. The commands use three shell variables; set them once:

```bash
PY=<BRAIN.md's $PY>                          # the skills' venv from `install.sh --bundle brain --deps`
CTE=<skills>/corpus-taxonomy-extraction      # this skill's installed directory
DB=schema/knowledge.sqlite                   # the store (BRAIN.md's $DB)
```

With no venv, use `uv run --with-requirements <bundle>/requirements.txt python` in place of `"$PY"` (for example `bundles/brain/requirements.txt` in a checkout). Parsing needs `pymupdf` and `openpyxl`, and `.pptx`/`.docx` also need LibreOffice `soffice` (a system dependency). The review, merge, graph and classify scripts (`taxonomy_review.py`, `review_server.py`, `taxonomy_merge.py`, `build_graph.py`, `classify_prep.py`, `classify_write.py`) are stdlib only. They never load `sqlite-vec` and never touch the vector table, so any Python 3.9+ runs them.

The map, adjudication, judge and fix steps assume a subagent mechanism with a model override (e.g. Sonnet). On another harness, use a Sonnet-class model that can read a file and write JSON. Nothing corpus-specific is hardcoded: the goal string and arguments drive everything.

## Pipeline (map → reduce → judge → emit)

```
goal + corpus + optional seed taxonomy
  → parse   (deterministic, no LLM)   parse_corpus.py  → uniform Markdown
  → map     (Sonnet LLM, per doc)     candidate terms + evidence + provenance + confidence  (JSON/doc)
  → reduce  (deterministic + LLM)     consolidate.py clusters near-dupes → LLM adjudicates AMBIGUOUS merges only
  → judge   (Sonnet LLM)              score coverage/coherence, flag low-confidence & unmapped
  → emit    taxonomy_v0.{json,md}     the draft; adopted provisionally, classified, then ratified in the first-build review
```

### 1. Parse — `parse_corpus.py` (deterministic, no LLM)
`"$PY" "$CTE/parse_corpus.py" --corpus <dir> --out <dir> --formats pptx,docx,pdf,md,markdown,txt`
- PDF → PyMuPDF text layer; PPTX/DOCX → soffice→PDF→PyMuPDF; XLSX → openpyxl `read_only` structure dump. (Visual/diagram pages → the `visual-parse` skill.)
- **MD/MARKDOWN/TXT → passthrough.** Markdown is already the parsed-store format, so a pre-processed corpus is copied verbatim under the standard `# SOURCE:` header — no converter, no LibreOffice, no loss.
- **Taxonomy pass = narrative/summary formats only (`--formats pptx,docx,pdf,md,markdown,txt`).** Do NOT parse the big numeric workbooks — they explode into tens of MB of useless number-grid markdown and belong to the deterministic numeric lane, not here.

**VTT/SRT corpora — two-pass approach:**

VTT and SRT (meeting transcripts) are parsed separately from the taxonomy induction pass because raw cue fragments produce noise-heavy taxonomy. Use PDFs/slides for taxonomy induction; use VTT/SRT for the knowledge index:

```bash
# Taxonomy pass — narrative docs only (PDFs, slides)
"$PY" "$CTE/parse_corpus.py" --corpus <docs> --out map_parsed --formats pptx,docx,pdf,md,markdown,txt

# Knowledge-index pass — transcripts (separate output dir, --merge-cues required)
"$PY" "$CTE/parse_corpus.py" --corpus <docs> --out parsed --formats vtt,srt --merge-cues 10
```

`--merge-cues 10` joins up to 10 consecutive same-speaker cues into one speaker-turn paragraph before chunking. **Omitting it produces ~25k single-line chunks averaging 79 chars — classification agents correctly return `[]` for nearly all of them and retrieval quality collapses.** Always pass `--merge-cues N > 1` for VTT/SRT.

### 2. Map — Sonnet subagents, one batch per subagent
Instantiate `map_instructions.template.md` (shipped with this skill): replace `{{GOAL}}` with the run's goal, `{{MAP_DIR}}` with the run's map-output dir, and `{{AUDIENCE}}` with the project audience (`brain.toml` `[project].audience`, or the store's `health().about.audience`; leave it empty if none); write it to the run dir as `map_instructions.md`. The audience is a **secondary** emphasis lens — it re-orders which goal-relevant intents/dimensions to favor and nudges vocabulary; the goal stays the primary filter and audience never drops a goal-relevant term. Dispatch subagents (model: sonnet) that read that instantiated file + their assigned parsed files and write one JSON per source into the map dir. The bulk document context lives and dies inside each subagent — the orchestrator only sees compact JSON. Extract `intent_classes`, `metrics` (with `source_type`), `entities`; each item carries `evidence` (≤200-char quote), `source`, `confidence`. Give any anchor taxonomy doc its own subagent.

### 3. Reduce — `consolidate.py` (deterministic) + LLM adjudication
`"$PY" "$CTE/consolidate.py" --map-dir map --out taxonomy/work/consolidated.json --threshold 0.86`
- Keep this output path: the first-build review reads its evidence from `taxonomy/work/consolidated.json`. If you write it elsewhere, pass `--consolidated <path>` to `plan --mode draft`.
- Pools terms by kind, normalizes names, fuzzy-clusters near-duplicates (stdlib difflib; swap in embeddings if fragmentation is high).
- Flags clusters with >1 surface form as `ambiguous` → a Sonnet subagent adjudicates **only those** ("Chicago" vs "Chicago Branch" vs "CHI" → merge?). This is where the real effort is (entity resolution), but it's bounded to ambiguous clusters.

### 4. Judge — Sonnet LLM-as-judge
Score the draft for coverage (did we miss obvious goal-relevant categories?) and coherence (L1/L2 consistency); flag low-confidence and unmapped terms for human review.

### 5. Emit — `taxonomy_v0.{json,md}`
```bash
"$PY" "$CTE/emit_taxonomy.py" --consolidated taxonomy/work/consolidated.json --map-dir map \
  --out-json taxonomy/taxonomy_v0.json --out-md taxonomy/taxonomy_v0.md --goal "$(cat goal.txt)"
```
The draft: intent hierarchy + entity/dimension candidates + metric inventory (each tagged computed/stated/both, with provenance), category descriptions (when the map agents drafted them), `review_flags` (near-duplicate and off-axis L1s), and a **demoted** list. Nothing is deployed until the user ratifies it, after classification, in the first-build review (procedure A below).

## Companion scripts — populate the local knowledge SQLite
Beyond taxonomy induction, this skill ships the scripts that wire the taxonomy into the one `knowledge.sqlite` store (shared with `knowledge-index` + `tabular-semantic-layer`):
- **`chunking.py`** — the shared heading-aware chunker (a chunk = a section = an Obsidian note = a retrieval unit). Identical copy in `knowledge-index`.
- **`build_graph.py`** — taxonomy → `graph_nodes`/`graph_edges` (L1/L2 vertices with their descriptions, `subclass_of`), always from `taxonomy/current.json`. It first runs the tag migrations of reviewed renames and merges recorded in the taxonomy history, then prunes nodes that left the taxonomy. It refuses (exit 3) to build from a file older than `current.json`, or older than the store's `meta.taxonomy_version`, when that would prune nodes. `--yes-prune` overrides this; pass it only when the user asked for a rollback. **Optional second taxonomy + traceability:** run this skill a second time with a **capability/vision-pillar goal** to induce a `capabilities.json` (same `intent_taxonomy` shape, or under a `capability_taxonomy` key), then `build_graph.py --capabilities capabilities.json --links addressed_by.json` layers in `capability_l1/l2` nodes and **`intent --addressed_by--> capability`** edges. Now a *problem → capability* traceability question resolves as a graph JOIN (see `hybrid-retrieval`) instead of narrative synthesis. `--links` is `{"addressed_by": [{"intent","capability"}, …]}`; a pair whose endpoints aren't known nodes is skipped (never a dangling edge). The script OWNS `subclass_of` + `addressed_by` (rebuilt each run, idempotent) and changes `about` edges only through a reviewed migration or when it prunes a node.
- **`classify_prep.py` → (Sonnet agents) → `classify_write.py`** — per-section taxonomy tags at **L1 AND L2**: prep presents the full L1/L2 vocab (with descriptions) and asks for the *most specific* fit (an L2 when the chunk is specifically about it, else its L1); agents assign (empty when nothing fits — never forced); write resolves each label (or an alias of a renamed/merged label) to its graph node, writes `chunk_topics` with the real `kind` and — for an L2 — **rolls up its parent L1** so L1 filters still catch it. `about` edges (chunk→vertex). `--merge` only adds labels; `--reclassify-done` empties the reclassification queue. *Meaning is agentic; this step is agents, not a script.*
- **`to_obsidian.py`** — emit the Obsidian vault as a **view of the store**: notes = chunks, real per-section tags from `chunk_topics`, `[[topic · …]]` links = graph vertices, topic notes carry the category description.

## Reviewing and editing the taxonomy (the review app)

Every taxonomy change goes through one local review app. **Agents only add**: new categories, descriptions, tags and governed-metric drafts. Renames, merges, moves, splits and removals are the user's decisions, made in the app, and the tags they affect are migrated. Node ids are `slug(label)`, so an unreviewed rename would make `build_graph` prune the node and its tags.

**Files.** `taxonomy/taxonomy_vN.json` are the ratified versions, never edited. `taxonomy/current.json` is a byte copy of the latest one, and every step after the first build reads it. Never edit either by hand. `taxonomy/reviews/review_<id>.json` is a planned review, frozen when planned. `taxonomy/decisions.jsonl` is the append-only record of what people decided; tell the user to commit it with the project. `taxonomy/work/` holds agent task dirs, `reclassify.json`, the redo channel (`requests.jsonl`, `responses.jsonl`) and other transient files. `taxonomy_merge.py --review <review> --apply` is the only writer of a taxonomy version.

**How the app runs.** `taxonomy_review.py serve --review <review> --db "$DB"`:
- binds `127.0.0.1` on a free port (`--port N` to fix one), prints `review app: http://127.0.0.1:<port>/?t=<token>` on stderr and opens the browser (`--no-browser` to skip). The token guards every API call;
- needs no network and no extra dependency. It opens the store read-only and writes only `taxonomy/decisions.jsonl` and, for a health review, `taxonomy/work/requests.jsonl`;
- blocks until the user clicks **Submit** (`status: submitted`, exit 0) or **Close the review without submitting** (`cancelled`, exit 4), or until `--timeout` seconds pass (default 3600: `timeout`, exit 3). It then prints one JSON line on stdout.

Run `serve` with Bash `run_in_background`, tell the user a browser tab is open for them, and end your turn: its exit wakes you. If the browser did not open, read the `review app:` line from the background task's output and give the user that URL. Decisions are saved as they are made, so after `cancelled` or `timeout`, run the same `serve` command again when the user wants to continue. `taxonomy_review.py status --review <review>` prints the counts at any time. User-facing guide: `docs/taxonomy-review-guide.md` in the skills repo.

What the user sees: **Inbox** (the proposals to decide), **Taxonomy** (the whole tree with tag counts, samples and sibling overlap; rename, merge, move, split, remove, describe, or add a category, each with an impact preview), **Metrics** (the metric inventory in tabs: needs a definition, possible duplicates, quoted from documents, governed), and **Your changes** (the user's own edits, which they can withdraw), then **Review & submit**. A new category needs a description. Descriptions go into the classifier's `vocab.md`, the graph, MCP `get_taxonomy` and the vault, so a described taxonomy tags sections more precisely. A computable metric with no governed definition in `schema/metrics.<corpus>.json` is answered "not modeled" by kb.

**No browser** (SSH, other hosts): `"$PY" "$CTE/taxonomy_review.py" export-md --review <review> --out review.md`, ask the user to fill in each `decision:` line (`approve`, `reject: <reason>` or `amend: <op json>`), then `"$PY" "$CTE/taxonomy_review.py" import-md --review <review> --md review.md --submit`. `record` is for scripts and only enters additions; never use it to enter a decision the user did not make.

### Which procedure

| situation | procedure |
|---|---|
| first build, after the draft has been classified | **A. First-build review** |
| refresh, maintenance, or "check / clean up the taxonomy" (the default) | **B. Health review** |
| only new-category proposals for untagged chunks | **C. Refine review** |
| the user wants to see or change the taxonomy or the metric inventory | **D. Browse and edit** |
| only descriptions, when the user asks for nothing else | `"$PY" "$CTE/taxonomy_review.py" describe-prep --taxonomy taxonomy/current.json --db "$DB" --out taxonomy/work/describe`, dispatch one subagent per `batch_k.json` with that dir's `instructions.md`, then `plan --mode describe --taxonomy taxonomy/current.json --proposals taxonomy/work/describe --db "$DB"` and continue as in C from step 4 |
| the user explicitly asked to skip the review | **E. Without review** |
| `current.json` is missing, or `build_graph` stops on a store with no taxonomy version | **F. Upgrade an older Brain**, then the procedure above |

`plan` prints one JSON line, `{review, review_id, mode, items, …}`. `<review>` below is its `review` path.

### A. First-build review
Not a review of the bare draft tree — a review of the draft **after** real per-section classification, so every proposal in the Inbox cites how many sections are actually affected. Until this review is applied, `taxonomy/PROVISIONAL` exists and `onboard.py verify` fails; do not deploy. `<run>` is the work directory for this run, e.g. `health` (reused every run).

1. **You index the corpus** (`knowledge-pipeline` step 3: `knowledge_index.py index --db "$DB" --corpus <project>/parsed --reset`).
2. **You build the graph from the draft, then adopt it as provisional**, before any human sees it. The order matters: `adopt` refuses while the store's graph does not hold the draft's node ids, so on a fresh store `build_graph` runs first.
   ```bash
   "$PY" "$CTE/build_graph.py" --taxonomy taxonomy/taxonomy_v0.json --db "$DB"
   "$PY" "$CTE/taxonomy_review.py" adopt --taxonomy taxonomy/taxonomy_v0.json --db "$DB" --provisional
   ```
   `adopt --provisional` copies the draft to `taxonomy/current.json` and writes the `taxonomy/PROVISIONAL` marker. Every later step reads `current.json`.
3. **You classify** every chunk against the provisional taxonomy (`knowledge-pipeline` step 5: `classify_prep.py` → dispatch Sonnet subagents → `classify_write.py`). Agents may answer `["__no_topic__"]` for a chunk with no topic at all (filler, boilerplate, off-goal); that verdict is stored in `chunk_verdicts` and is a valid, complete answer — not a missing one.
4. **You compute signals and diagnose**, now that real counts exist:
   ```bash
   "$PY" "$CTE/taxonomy_signals.py" --taxonomy taxonomy/current.json --db "$DB" --out taxonomy/work/signals.json
   "$PY" "$CTE/taxonomy_review.py" diagnose --taxonomy taxonomy/current.json --db "$DB" --out taxonomy/work/<run>
   ```
5. **You dispatch one fix subagent per task dir** (`describe`, `notags`, `structure`, `fit`, `untagged`, `metrics`), each reading that dir's `instructions.md` and batches — same contract as step 2 of **B. Health review** below.
6. **You plan the review**: `"$PY" "$CTE/taxonomy_review.py" plan --mode health --taxonomy taxonomy/current.json --db "$DB" --work taxonomy/work/<run>`. It titles the review "First-build review".
7. **You serve it** in the background: `"$PY" "$CTE/taxonomy_review.py" serve --review <review> --db "$DB"`. Tell the user the tab is open and end your turn.
8. **The user decides**: keeps, merges, moves, restructures or edits each proposal in the Inbox, and submits.
9. **When `serve` exits with `submitted`**, you apply it and rebuild the graph:
   ```bash
   "$PY" "$CTE/taxonomy_merge.py" --review <review> --apply    # taxonomy_v1.json + current.json; PROVISIONAL removed
   "$PY" "$CTE/build_graph.py" --taxonomy taxonomy/current.json --db "$DB"   # tags migrate
   ```
   A review that approves no taxonomy change writes no `taxonomy_v1.json` (`taxonomy_changed: false`), but apply still removes `PROVISIONAL`: the user has reviewed the draft.
10. **You reclassify, if needed**: if `taxonomy/work/reclassify.json` exists, run **Reclassifying after a taxonomy change** below before step 11. **You write the approved tags, if any**: `classify_write.py --db "$DB" --results <tags_file's directory> --merge`.
11. Continue the build (`knowledge-pipeline` steps 5b onward: `related`, marts, vault).

### A′. Draft review without an index (fallback)
Use only when the corpus is not indexed yet, so no real classification counts are available — a review of the bare draft tree.
1. **You plan it** after emit: `"$PY" "$CTE/taxonomy_review.py" plan --mode draft --taxonomy taxonomy/taxonomy_v0.json` (evidence from `taxonomy/work/consolidated.json`).
2. **You serve it** in the background as above: `"$PY" "$CTE/taxonomy_review.py" serve --review <review>`. Tell the user the tab is open and end your turn.
3. **The user decides**: keeps or changes each draft category in the Inbox, fixes the tree in the Taxonomy view, and submits.
4. **When `serve` exits with `submitted`**, you apply it: `"$PY" "$CTE/taxonomy_merge.py" --review <review> --apply`. This writes `taxonomy/taxonomy_v1.json` and `taxonomy/current.json`. It refuses if `current.json` already exists; a Brain that has one uses B–D instead.
5. Continue the build from `taxonomy/current.json` (`knowledge-pipeline` steps 3–5: index, `build_graph`, classify).

### B. Health review (the default way to review)
One review for every taxonomy and metric problem. A deterministic `diagnose` finds the problems; Sonnet agents precompute a specific fix for each one that needs judgment; the user decides in a grouped inbox and can send any fix back with **Redo with a note…**; you apply the approved fixes. `<run>` is the work directory for this run, e.g. `health` (reused every run) or `health-20260921-1400` (to keep each run's files).

1. **You run diagnose.** If `taxonomy/current.json` changed since `taxonomy/work/signals.json` was written (any applied review, adopt or rollback), or the store was reclassified, re-run `taxonomy_signals.py` first (it needs the venv); otherwise skip it:
   ```bash
   "$PY" "$CTE/taxonomy_signals.py" --taxonomy taxonomy/current.json --db "$DB" --out taxonomy/work/signals.json
   "$PY" "$CTE/taxonomy_review.py" diagnose --taxonomy taxonomy/current.json --db "$DB" --out taxonomy/work/<run>
   ```
   `diagnose` uses `signals.json` only when its `taxonomy_sha256` matches `current.json`. A stale or unstamped file is ignored with a warning (on stderr and in the output's `warnings`), and near-duplicates fall back to the string detector (`near_duplicate_source: "string"`) with no `misplaced` problems; when you see that warning, re-run `taxonomy_signals.py` and `diagnose`.
   It prints one JSON line: `problems` (a count per kind) and `tasks` (`[{kind, dir, batches}]`, one per kind that needs agents: `describe`, `notags`, `structure`, `fit`, `metrics`, `untagged`). `fit` covers three kinds together — `sparse` (a category with only 1–2 tagged sections), `misplaced` (an L2 closer to another L1) and `overloaded` (an L1 far above the median size). Reusing `--out` is safe: `diagnose` first removes an earlier run's `batch_*.json` and `result_*.json` from its task dirs (it names them on stderr and in `removed_stale`), so `plan` never reads old fixes. `diagnose` also reads open redo requests from `taxonomy/work/requests.jsonl` and puts their notes on the matching entries; a request already answered by `respond`, or on an item accepted in a review that was since applied, is not reopened.
2. **You dispatch Sonnet subagents, one per `batch_k.json`** in each task `dir`. Each subagent reads that dir's `instructions.md` and its `batch_k.json` and writes `result_k.json` in the same dir. The `untagged` dir also has `vocab.md` for the agent to read, and the `metrics` dir may have `families.json`. The `fit` dir's `result_k.json` is `{"fixes": [...]}`, one entry per subject: `{"kind": "sparse", "subject": "<label>", "fix": "merge"|"keep", "into": "<sibling label>", "reason": "..."}`, `{"kind": "misplaced", "subject": "<label>", "fix": "move"|"keep", "new_parent": "<L1 label>", "reason": "..."}`, or `{"kind": "overloaded", "subject": "<label>", "fix": "restructure"|"keep", "add": [{"name": "...", "description": "..."}], "moves": [{"node": "...", "new_parent": "..."}], "reason": "..."}` — exact shapes in `taxonomy/work/<run>/fit/instructions.md` (`FIT_INSTRUCTIONS`). The result formats are defined in each `instructions.md`; do not restate them to the agents, point them at the file. Check that each batch has its result file before going on. A missing or malformed result is not fatal: that problem still reaches the inbox with a safe default, marked `fallback`.
3. **You plan the review.**
   ```bash
   "$PY" "$CTE/taxonomy_review.py" plan --mode health --taxonomy taxonomy/current.json --work taxonomy/work/<run> --db "$DB"
   ```
   Any `skipped_files` in its output are result files that could not be read. Name them to the user; do not re-dispatch silently.
4. **You serve the app and watch for redo requests.** Run this with Bash `run_in_background`:
   ```bash
   "$PY" "$CTE/taxonomy_review.py" serve --review <review> --db "$DB" --watch-hint
   ```
   Then create the requests file if it is missing (`mkdir -p taxonomy/work && touch taxonomy/work/requests.jsonl`) and arm a **Monitor** on:
   ```bash
   tail -n0 -F taxonomy/work/requests.jsonl
   ```
   Set `timeout_ms` to 1800000 (30 minutes). When the Monitor expires while `serve` is still running, arm it again. Tell the user a browser tab is open for them, then end your turn.

   Each Monitor event is one JSON line `{"id", "ts", "review_id", "item_id", "note", "status": "open"}`: the user pressed **Redo with a note…** on an item. For each one:
   1. Prepare the entry:
      ```bash
      "$PY" "$CTE/taxonomy_review.py" redo-prep --review <review> --item <item_id>
      ```
      It prints `{kind, dir, entry, instructions, result, note}`: `entry` is a `batch_0.json` holding the one task entry the item came from, with the note added, next to a copy of that task's `instructions.md`. `plan` never reads this directory.
   2. Have one subagent (or yourself, for a single entry) follow `instructions` for `entry` and write `result`.
   3. Turn the new fix into the item's op shape: one of the item's `op`/`alternatives` in `<review>`, or the same op type on the same subject with edited fields (a different description, a narrower `chunk_ids`). Check it without recording anything:
      ```bash
      "$PY" "$CTE/taxonomy_review.py" respond --review <review> --request <id> --op '<op json>' --check
      ```
      `{"status": "ok"}` means `respond` will accept it; on `refused`, pick an op from the item's alternatives instead.
   4. Record it:
      ```bash
      "$PY" "$CTE/taxonomy_review.py" respond --review <review> --request <id> --op '<op json>' \
        --reason '<one line: what changed and why>'
      ```
   The app picks the response up within seconds and shows it as "Revised by Claude"; the user approves it or not.
5. **When `serve` exits, stop the Monitor** (TaskStop). Go on only if its `status` is `submitted`; on `cancelled` or `timeout`, tell the user and stop.
6. **You apply the review and rebuild the graph.**
   ```bash
   "$PY" "$CTE/taxonomy_merge.py" --review <review> --apply
   "$PY" "$CTE/build_graph.py" --taxonomy taxonomy/current.json --db "$DB"
   ```
   `taxonomy_merge` prints `{status, version, tags_file, governed_drafts_file, taxonomy_changed, …}`. It writes a new taxonomy version only if at least one taxonomy op was approved; run `build_graph` either way.
7. **You reclassify, if needed.** If `taxonomy/work/reclassify.json` exists, run the sequence in **Reclassifying after a taxonomy change** below. Do this before step 8: reclassifying replaces a chunk's tags, so it would drop tags added in step 8.
8. **You write the approved tags, if any.** If `tags_file` is not null:
   ```bash
   "$PY" "$CTE/classify_write.py" --db "$DB" --results <the directory of tags_file> --merge
   ```
   `--merge` only adds labels (an L2 also adds its parent L1); it never removes a tag.
9. **You tell the user about governed-metric drafts, if any.** If `governed_drafts_file` is not null, it holds the approved `metric_govern` drafts (`{review_id, metric, draft:{key, family, unit, desc, grain}}`, appended across reviews; this review's entries carry its `review_id`). Show the user this review's drafts and ask whether to add them to `schema/metrics.<corpus>.json` with the `tabular-semantic-layer` skill. Edit that file only if they agree. Neither the app nor `taxonomy_merge` ever writes it.
10. Refresh `related` and, if the project keeps one, the vault (see **After apply**).

**What the user sees.** Problems are grouped: a group (e.g. "12 categories have no description") is one inbox entry with a row per item and **Accept all remaining**, which leaves `fallback` rows (a safe default, not an agent's recommendation) for the user to decide one by one. **Skip** records nothing; it only moves on, and a skipped problem comes back at the next `diagnose`. A group of labels that differ only by a number or code (a naming pattern) is one item whose default is keep, with no agent work. A near-duplicate cluster of three or more labels is one agent entry and comes back as a group of merge rows. A fix on a node that another approved fix already changes (e.g. describing a label that is merged away) is refused as a conflict. **Accept all remaining** leaves such rows out and says how many ("2 rows skipped: their category is being merged away, renamed or removed"); if a conflict still slips through, nothing is recorded and the app offers to accept the other rows. A naming-pattern item has no **Redo with a note…**; the user changes it in the Taxonomy view.

**Hosts without Monitor.** Skip the Monitor and run `serve` without `--watch-hint`; the app then says requests are "Queued for next run". Open requests stay in `taxonomy/work/requests.jsonl`, and the next `diagnose` puts their notes on the matching entries.

### C. Refine review (new categories for untagged chunks)
Use this only when the user asks for new-category proposals alone; the health review's `untagged` task covers the same ground. The signal is a rising share of **untagged** chunks, e.g. after `visual-parse` starts transcribing diagrams.
1. **You prepare the batches** in a fresh directory:
   ```bash
   "$PY" "$CTE/taxonomy_refine_prep.py" --db "$DB" --taxonomy taxonomy/current.json --out taxonomy/work/refine-<run>
   ```
2. **You dispatch Sonnet subagents, one per `batch_k.json`.** Each reads that dir's `instructions.md`, `vocab.md` and its batch, and writes `result_k.json` there: an existing category the classifier missed, or a proposed new **L2 under a named parent L1** (preferred) or new **L1**, with evidence and a description.
3. **You plan the review**: `"$PY" "$CTE/taxonomy_review.py" plan --mode drift --taxonomy taxonomy/current.json --proposals taxonomy/work/refine-<run> --db "$DB"`. It dedups against the vocabulary and aliases and suppresses proposals rejected before.
4. **You serve it** in the background: `"$PY" "$CTE/taxonomy_review.py" serve --review <review> --db "$DB"`. Tell the user the tab is open and end your turn.
5. **The user approves, rejects (with a reason; rejections stick) or amends** each proposal and submits.
6. **When `serve` exits with `submitted`**, you run **After apply** below.

### D. Browse and edit (the user changes the taxonomy)
1. **You plan and serve an empty review**:
   ```bash
   "$PY" "$CTE/taxonomy_review.py" plan --mode browse --taxonomy taxonomy/current.json --db "$DB"
   "$PY" "$CTE/taxonomy_review.py" serve --review <review> --db "$DB"     # run_in_background
   ```
   Tell the user the tab is open and end your turn.
2. **The user edits in the Taxonomy view**: **+ New category** adds an L1, **+ Add sub-category** under an L1 adds an L2 (each needs a description), and a selected category can be described, renamed, merged, moved, split or removed, each with an impact preview. In the Metrics view they can add a metric or merge duplicates. Every edit collects under **Your changes**; they submit from there.
3. **When `serve` exits with `submitted`**, you run **After apply** below.

### After apply (A is done here; C, D and describe continue)
```bash
"$PY" "$CTE/taxonomy_merge.py" --review <review> --apply
"$PY" "$CTE/build_graph.py" --taxonomy taxonomy/current.json --db "$DB"
```
Then reclassify the queued chunks (next section) if `taxonomy/work/reclassify.json` exists, refresh `related` (`"$PY" <skills>/knowledge-index/knowledge_index.py related --db "$DB"`), and regenerate the vault if the project keeps one (`"$PY" "$CTE/to_obsidian.py" --db "$DB" --out vault --clean --assets assets`).

### Reclassifying after a taxonomy change
If `taxonomy/work/reclassify.json` exists after `build_graph`, run this sequence yourself, where `<N>` is the `version` in that file:

```bash
IDS=$("$PY" -c 'import json;print(",".join(map(str,json.load(open("taxonomy/work/reclassify.json"))["chunk_ids"])))')
"$PY" "$CTE/classify_prep.py" --db "$DB" --taxonomy taxonomy/current.json --chunks "$IDS" --out classify/reclassify-v<N>
# dispatch Sonnet classification agents over classify/reclassify-v<N>/batch_*.json → result_*.json in the same dir
"$PY" "$CTE/classify_write.py" --db "$DB" --results classify/reclassify-v<N> --reclassify-done taxonomy/work/reclassify.json
```

Always use a fresh `classify/reclassify-v<N>` directory with no `result_*.json` in it. Never reuse the first-build `classify/` directory: `classify_write` reads every `result_*.json` there, so stale first-build results would overwrite the new tags and `--reclassify-done` would drop the queued ids as done. `--reclassify-done` removes only the ids it just wrote and deletes the file once it is empty.

### E. Without review (only when the user explicitly asks)
Only when the user has asked in this conversation to skip the review. It applies agent proposals (a `taxonomy_refine_prep` result dir) add-only, and marks the version `without_review` in its history.
```bash
"$PY" "$CTE/taxonomy_merge.py" --taxonomy taxonomy/current.json --proposals <dir>                          # dry run: show the user the diff
"$PY" "$CTE/taxonomy_merge.py" --taxonomy taxonomy/current.json --proposals <dir> --apply --without-review  # after they confirm
```
Then `build_graph` and the reclassification as in **After apply**. `--apply` without `--review` or `--without-review` is refused.

The `taxonomy/PROVISIONAL` marker: the user's explicit instruction to skip review stands in for the review, so `--apply --without-review` deletes it either way — after writing the new version when there is something to add, or with `current.json` left unchanged when nothing is left to apply (every proposal a duplicate, invalid or suppressed). The script prints `removed …/PROVISIONAL` in both cases. The dry run (no `--apply`) never touches it.

### F. Upgrading an older Brain
For a Brain built before `current.json` existed. `browse`, `drift`, `describe` and `health` reviews are refused until this is done.
1. **You tell the user** what you found and which version the store was built from. Adopt it only with their confirmation.
2. **`current.json` is missing**: `"$PY" "$CTE/taxonomy_review.py" adopt --taxonomy taxonomy/taxonomy_v<N>.json --db "$DB"`. It copies that version to `current.json` and records it in the store; it refuses if the version does not match the graph.
3. **`current.json` exists and is ahead of the store** (e.g. `build_graph` reports a store with tags but no `meta.taxonomy_version`): add `--meta-only`. It checks the same node set, records only the store's version and never touches `current.json`. Then rebuild with `"$PY" "$CTE/build_graph.py" --taxonomy taxonomy/current.json --db "$DB"`.
4. Pass `--force` only when the user explicitly says to replace a differing `current.json` or to adopt despite a node mismatch.

## Downstream wiring

- **Intent classes** → the local classification scheme and `graph_nodes`/`graph_edges`, keeping section tags consistent with the reviewed L1/L2 vocabulary.
- **Entities** → conformed dimensions (Region→Division→Branch→RSR) shared by the local graph and marts.
- **Metrics** → governed semantic-layer definitions; `computable` ones get SQL over the marts, `stated` ones stay citation-backed.

## Guardrails / common mistakes

- **Never let the map/judge model emit a numeric answer.** It records *stated* values as quotes-with-provenance only; real computation is deterministic and downstream.
- **Keep provenance + a demoted list** — the goal lens can over-filter; make demotion visible and reversible, never a silent delete.
- **Don't parse giant numeric workbooks here.** Restrict `--formats` to narrative/summary types.
- **Seed-guided beats schema-free** — anchor on any existing taxonomy (e.g. a "Taxonomy Compendium") and the existing knowledge graph; extend rather than invent.
- Build and classify against the draft only through `adopt --provisional`; the first human review happens after classification, and nothing is deployed while `taxonomy/PROVISIONAL` exists. Every later step reads `taxonomy/current.json`, never a versioned file.
