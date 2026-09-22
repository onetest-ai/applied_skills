# Brain build-agent runbook

This file is for the **top-level agent that creates or updates a brain**. It is not the prompt for the answering agent that later queries the completed store.

## Role and execution boundary

You are the orchestrator. A human starts you from a coding-agent host and gives you a goal, corpus path, reporting path, and brain-project path. You:

1. load the `knowledge-pipeline` skill and the component skills it names;
2. run deterministic scripts in the consuming brain project;
3. create batch files for judgment work;
4. dispatch Sonnet subagents to read those batches and write result JSON;
5. validate completeness and schema before consuming results;
6. show human gates and failures instead of silently guessing;
7. checkpoint long runs and verify the finished store.

The following do **not** orchestrate builds:

- the `brain` MCP server—it only serves an already-built store;
- `./brain update`—it updates SQLite from final `parsed/` Markdown only;
- `parse_corpus.py`—it is a deterministic text-layer parser, not the visual workflow;
- vision/classification subagents—they process assigned batches and return JSON but do not control later phases.

All commands run on the machine that contains the corpus and brain project. Agent result files must be written into that project’s `vision/` or `classify/` work directory.

## Non-negotiable rules

- **Meaning is agentic; numbers are computed.** Use agents for visual transcription, taxonomy judgment, and semantic classification. Use scripts for hashes, table extraction, embeddings, graph writes, marts, and verification.
- For visual corpora, assemble final VLM-enriched Markdown **before** taxonomy induction, indexing, and classification.
- Never classify the provisional text-only parse unless the human explicitly accepts a lower-quality build.
- Never consume partial agent output. Every requested item must have one result.
- Never use `classify_write --reset` during an incremental update.
- Never silently change taxonomy node labels. Agents only add; renames, merges, moves, splits and removals are human decisions made in the taxonomy review app, which migrates the affected tags.
- Show `brain_sync plan` before `apply`, especially when it reports deletions.
- Preserve the automatic pre-apply SQLite snapshot as operational recovery. Database mutations are committed together; on any failed verification, stop and offer rollback before retrying.
- Do not claim completion until verification passes and row counts are plausible.

## Expected project layout

```text
PROJECT/
  goal.txt
  schema/
    knowledge.sqlite
    families.<corpus>.json
    metrics.<corpus>.json
  taxonomy/
    taxonomy_v0.json               # emitted draft; adopted provisionally, ratified in the first-build review
    taxonomy_vN.json               # ratified versions; immutable
    current.json                   # byte copy of the latest version; every step reads it
    decisions.jsonl                # append-only review decisions; commit it
    reviews/  work/                # frozen review files; agent task dirs and queues
  parsed/
  assets/
  vision/
  classify/
  marts/
  vault/
  sync_plan.json
```

Set these once and use absolute paths in commands and agent prompts:

```bash
PROJECT=/absolute/path/to/brain-project
DOCS=/absolute/path/to/source-docs
REPORTING=/absolute/path/to/reporting       # optional
DB="$PROJECT/schema/knowledge.sqlite"
SKILLS=/absolute/path/to/installed/skills
PY=/absolute/path/to/brain/venv/bin/python
TAX="$PROJECT/taxonomy/current.json"
```

Do not guess paths. Resolve them with the installed launcher/config or ask the human.

## Source intake before building

Read the project’s `brain.toml`; do not search arbitrary filesystem locations. Source roots are named and relocatable. Register existing files with `./brain source adopt --root <key> <relative-path>`. For a chat attachment, materialize it temporarily and run `./brain source import <temp-path> --root incoming` with explicit attachment/conversation provenance. This copies it atomically into the managed `.incoming` root; the original chat temp path is not retained and no source bytes are stored in SQLite.

Run `./brain source plan` before source-driven work. Treat `root_unavailable` as a blocking availability condition, never as mass deletion. `missing` under import is a warning; `remove_candidate` under mirror requires explicit human-approved `source remove`; `corrupt` under managed requires restoration or explicit removal. Keep `source_id` stable across content changes and safe moves.

## Creating a brain from scratch

### Phase 0 — intake and preflight

Before anything else, run the doctor — it reports what this corpus needs without installing
anything:

```bash
"$PY" "$SKILLS/knowledge-pipeline/brain_doctor.py" --corpus "$DOCS"
```

Stop on exit 1. Show the human every missing item and its printed install command (e.g. `!
brew install ffmpeg whisper-cpp`); never install system packages yourself. If it reports
`whisper-cli` REQUIRED, note which recordings lack a transcript and hold the whisper-model
choice until after `scaffold` below writes `brain.toml` — `set-whisper-model` needs it to
exist. Once `brain.toml` exists, run `brain_doctor.py whisper-models`, let the human choose a
model (recommend `small.en`, or `small` for non-English meetings), run the printed `curl`
download only after they approve, then:

```bash
"$PY" "$SKILLS/knowledge-pipeline/brain_doctor.py" set-whisper-model \
  --config "$PROJECT/brain.toml" --model <path> [--language en]
```

Re-run `brain_doctor.py --config "$PROJECT/brain.toml"` to confirm the gap is closed.

Ask for missing values one at a time:

1. analytical goal;
2. audience — who will consume the KB (which roles/personas). Optional; pass `--audience`. It's a secondary lens that refines taxonomy emphasis and drives how the `kb` plugin sets answer altitude and authored-artifact style. Canonical in `brain.toml` `[project].audience`; distinct from the deployment target (distribution/infra);
3. narrative documents directory and whether it is safe `import` or authoritative `mirror`;
4. reporting workbook directory, if any, and its `import`/`mirror` semantics;
5. destination brain project;
6. deployment target — `local` (agent queries the local store) or `hosted-mcp` (governed MCP served to remote clients). Pass `--deploy-target`; it shapes `BRAIN.md` and is recorded in `brain.toml` `[deployment].target`.

Recommend `import` unless the user explicitly says that deleting a file from an available folder should propose deleting it from the Brain. `incoming` is always project-local `managed`. The goal is authoritative in `goal.txt` — a host operator doc (e.g. `AGENTS.md`) never replaces it; if a project's goal lives only in a host doc, write it back to `goal.txt` so maintenance can recover it.

Then scaffold and preflight:

```bash
"$PY" "$SKILLS/knowledge-pipeline/onboard.py" scaffold \
  --project "$PROJECT" --goal "$GOAL" --docs "$DOCS" \
  ${AUDIENCE:+--audience "$AUDIENCE"} \
  --docs-mode "$DOCS_MODE" --reporting-mode "$REPORTING_MODE" \
  ${REPORTING:+--reporting "$REPORTING"}
```

Read the generated `brain.toml` back to the user and confirm:

- root keys and relative paths resolve to the intended directories;
- `incoming` is `managed` and points to `.incoming`;
- `docs` and `reporting` are `import` unless the user explicitly chose authoritative `mirror`;
- include patterns do not unintentionally discover unrelated files.

Do not overwrite a pre-existing config. Then inspect `BRAIN.md`, dependency report, source inventory, and workbook profiles. Configure `families.<corpus>.json` before marts.

Create a durable checkpoint containing inputs, file counts, chosen visual thresholds, taxonomy strategy, and planned batch counts.

Ensure the SQLite file exists before visual assembly so `vision_assemble.py --db` can persist `page_render`. Creating an empty SQLite file is sufficient; the indexer will create its own tables later and `--reset` does not drop `page_render`:

```bash
"$PY" -c 'import sqlite3,sys; sqlite3.connect(sys.argv[1]).close()' "$DB"
```

### Phase 0b — meeting recordings (run before Phase 1, if the corpus has any)

A recording is a continuous medium, so this lane substitutes a frame-selection step in front
of the same transcribe→assemble shape, and produces ONE parsed document per recording:
transcript turns and on-screen frames interleaved by time. Run it before the narrative parse
so a paired Teams `.docx` transcript is consumed by its recording instead of being parsed as
an ordinary document. For each recording (`<rel>` its source-relative path, `<root>` the
source root):

```bash
VC="$SKILLS/visual-parse/video_capture.py"
VIDEO_VISION_RUN="$PROJECT/vision/$(date +%Y%m%d-%H%M%S)-video"
mkdir -p "$VIDEO_VISION_RUN"
"$PY" "$VC" probe --video "<root>/<rel>" --rel-to "<root>" \
  --work "$PROJECT/video" --manifest "$PROJECT/parsed/manifest.json"
# only when probe.json says "transcript": "asr":
"$PY" "$VC" transcribe --probe "$PROJECT/video/<slug>/probe.json" --config "$PROJECT/brain.toml"
"$PY" "$VC" frames --video "<root>/<rel>" --rel-to "<root>" --assets-root "$PROJECT/assets"
"$PY" "$SKILLS/visual-parse/vision_prep.py" \
  --render-dir "$PROJECT/assets/<slug>" --out "$VIDEO_VISION_RUN" --db "$DB"
# dispatch vision subagents on <VIDEO_VISION_RUN>/batch_k.json → result_k.json — same contract
# as Phase 2 below, plus: answer <!-- no-content --> for people-only frames.
"$PY" "$VC" assemble --probe "$PROJECT/video/<slug>/probe.json" \
  --render-dir "$PROJECT/assets/<slug>" --results "$VIDEO_VISION_RUN" \
  --parsed "$PROJECT/parsed" --db "$DB"
```

`<slug>` is printed by `probe` and `frames` (`slug=…`). `assemble` **refuses while any kept
frame lacks a VLM result** — never hand-edit around that; validate the same way as the batch
validation checklist below, applied to this run's batches/results. On success, `assemble`
records the sidecar transcript (if any) as `consumed-by-video` in the manifest and deletes its
stale parsed doc, so the narrative parse pass (Phase 1 for docs, or the `vtt,srt,docx` pass in
`knowledge-pipeline`) skips it automatically. Full reference: `visual-parse` → "Meeting
recordings".

### Phase 1 — render and route every narrative document

For every PDF/PPT/PPTX/DOC/DOCX, render into a shared asset root. The renderer now derives the slug from the **full source-relative path** (dir + stem), so `a/report.pdf` and `b/report.pdf` produce distinct render dirs and can no longer overwrite each other — pass the source-relative path as `--doc`. (A bare basename still slugs as before, so pass the path, not just the filename.) Record the resulting render directory:

```bash
ASSET_ROOT="$PROJECT/assets"
"$PY" "$SKILLS/visual-parse/render_pages.py" \
  --doc "$SOURCE_RELATIVE_PATH" --out "$ASSET_ROOT" --dpi 150
# actual render dir: $ASSET_ROOT/<kebab-of-source-relative-path>
```

`render_pages.py` performs the routing decision per page and writes `pages.json` with:

- `img_sha`—identity of the rendered page;
- `text_len`—text-layer length;
- `n_drawings`—vector-layout signal;
- `n_tables`—deterministically extracted grids;
- `flagged` and `why`—whether a VLM is needed.

Default routing:

- ordinary text page → use `pNN.txt`;
- detected data table → preserve `pNN.tables.md`; do not ask a VLM to recreate numeric cells;
- thin-text/drawing-heavy diagram → `flagged=true`, send to a vision model;
- every page retains `pNN.png` as factual evidence.

Summarize total pages, flagged pages, tables, and failures. A failed source conversion is a blocking build error for that source; do not omit it silently.

### Phase 2 — prepare and run vision batches

Use a fresh, run-specific work directory: neither prep nor assembly removes stale `batch_*.json`/`result_*.json`, and consumers load every matching result file. Never reuse a dirty directory.

Pass all render directories to one preparation command:

```bash
VISION_RUN="$PROJECT/vision/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$VISION_RUN"
"$PY" "$SKILLS/visual-parse/vision_prep.py" \
  --render-dir "$PROJECT/assets/path-a/doc-a" \
  --render-dir "$PROJECT/assets/path-b/doc-b" \
  --out "$VISION_RUN" --db "$DB" --batches 8
```

The script emits only **flagged and uncached** pages. It skips any `img_sha` already in SQLite `page_render`.

For every `vision/batch_K.json`, dispatch one vision-capable Sonnet subagent. Its contract:

1. read `vision/instructions.md` and its assigned batch;
2. open each `image` path;
3. transcribe faithfully to structured Markdown;
4. preserve labels, ordering, arrows, containment, dates, and qualifiers;
5. trust `tables_md` for numeric cells;
6. write only `vision/result_K.json` as `{img_sha: markdown}`.

Before continuing, validate:

- every batch item’s `img_sha` occurs in exactly one result;
- there are no unknown hashes;
- every value is a non-empty Markdown string;
- result JSON parses successfully;
- obvious errors or refusals are re-run only for their affected batch/pages.

If no batches are emitted, that is valid: all visual pages were cached or no pages were flagged.

### Phase 3 — assemble final parsed documents

For every rendered document, choose a stable output name based on the source-relative path, not only its basename. Then run:

```bash
"$PY" "$SKILLS/visual-parse/vision_assemble.py" \
  --render-dir "$PROJECT/assets/$SLUG" \
  --out "$PROJECT/parsed/$DOC_ID.md" \
  --results "$VISION_RUN" --db "$DB" --assets-rel "$ASSET_REL"
```

This produces one top-level `## pNN · title` page section with an image marker. Visual pages use VLM Markdown; ordinary pages use the text layer. VLM `###`–`######` subheadings may produce multiple downstream chunks/notes for that page; those sibling chunks inherit its image. Fresh transcriptions are persisted to `page_render`.

`pNN.tables.md` is **not appended to parsed Markdown** by the current assembler. It remains a factual sidecar under `assets/` and is returned by MCP `get_evidence`; preserve assets and use that evidence path for table figures.

Treat any “visual pages still need VLM transcription” message as incomplete unless the human explicitly authorizes a text-only fallback.

### Phase 4 — taxonomy induction and provisional build (no human gate yet)

Induce the taxonomy from the **final enriched `parsed/` corpus**, following `corpus-taxonomy-extraction`:

```text
map agents per document
  → deterministic consolidate
  → agents adjudicate ambiguous merges
  → judge coverage/coherence
  → emit versioned taxonomy JSON/Markdown
```

Prefer seed-guided induction when the corpus contains an authoritative taxonomy. Keep the approved taxonomy under `PROJECT/taxonomy/`.

Write the reduce output to `taxonomy/work/consolidated.json` and emit to `taxonomy/taxonomy_v0.json`. There is no review here: adopt the draft as a **provisional** `current.json` and build and classify against it, so the first human review can be grounded in real per-section counts instead of the bare draft tree.

The narrative index can run after final assembly; do it once, before the graph:

```bash
"$PY" "$SKILLS/knowledge-index/knowledge_index.py" index \
  --db "$DB" --corpus "$PROJECT/parsed" --reset
```

```bash
cd "$PROJECT"
"$PY" "$SKILLS/corpus-taxonomy-extraction/build_graph.py" \
  --taxonomy "$PROJECT/taxonomy/taxonomy_v0.json" --db "$DB"
"$PY" "$SKILLS/corpus-taxonomy-extraction/taxonomy_review.py" adopt \
  --taxonomy "$PROJECT/taxonomy/taxonomy_v0.json" --db "$DB" --provisional
```

`adopt --provisional` copies the draft to `$TAX` (`taxonomy/current.json`) and writes `taxonomy/PROVISIONAL`. While that marker exists, `onboard.py verify` fails and the store must not be deployed — the provisional build exists only to get real classification counts in front of the human.

### Phase 5 — classify against the provisional taxonomy

Prepare batches in a fresh run-specific directory. `classify_write.py` reads every matching result file and does not enforce completeness; with `--reset`, stale or partial results could erase valid classifications. Classifying against a provisional taxonomy, agents may answer `["__no_topic__"]` for a chunk that carries no topic at all (filler, boilerplate, off-goal) — that is a valid verdict, not a missing one.

```bash
CLASSIFY_RUN="$PROJECT/classify/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$CLASSIFY_RUN"
"$PY" "$SKILLS/corpus-taxonomy-extraction/classify_prep.py" \
  --db "$DB" --taxonomy "$TAX" --out "$CLASSIFY_RUN" --batches 8
```

For each `classify/batch_K.json`, dispatch one Sonnet text subagent. Its contract:

1. read `instructions.md`, `vocab.md`, and its batch;
2. assign 0–3 exact L1/L2 labels per chunk, or `["__no_topic__"]` when the chunk carries no topic at all;
3. prefer a specific L2 when justified;
4. use `[]` rather than force a weak match;
5. write only `classify/result_K.json` as `{chunk_id: [exact labels]}`.

Validate complete ID coverage, JSON shape, and exact-vocabulary membership (`__no_topic__` is the one non-vocabulary value allowed, and only alone). Then write a full-build classification:

```bash
"$PY" "$SKILLS/corpus-taxonomy-extraction/classify_write.py" \
  --db "$DB" --results "$CLASSIFY_RUN" --reset
```

### Phase 5b — the first human review, grounded in counts

This is the human gate: not the bare draft tree, but the draft **after** real classification, so every proposal cites how many sections are actually affected. It is runnable end to end. Compute signals, diagnose problems, dispatch one fix subagent per task dir, then plan, serve, and apply:

```bash
"$PY" "$SKILLS/corpus-taxonomy-extraction/taxonomy_signals.py" \
  --taxonomy "$TAX" --db "$DB" --out "$PROJECT/taxonomy/work/signals.json"
"$PY" "$SKILLS/corpus-taxonomy-extraction/taxonomy_review.py" diagnose \
  --taxonomy "$TAX" --db "$DB" --out "$PROJECT/taxonomy/work/health"
#   → dispatch one fix subagent per task dir (describe, notags, structure, fit, untagged, metrics)
"$PY" "$SKILLS/corpus-taxonomy-extraction/taxonomy_review.py" plan --mode health \
  --taxonomy "$TAX" --db "$DB" --work "$PROJECT/taxonomy/work/health"
"$PY" "$SKILLS/corpus-taxonomy-extraction/taxonomy_review.py" serve \
  --review <the "review" path it printed>       # run in the background; the user reviews "First-build review"
"$PY" "$SKILLS/corpus-taxonomy-extraction/taxonomy_merge.py" \
  --review <the same review path> --apply       # writes taxonomy_v1.json and current.json; deletes PROVISIONAL
"$PY" "$SKILLS/corpus-taxonomy-extraction/build_graph.py" \
  --taxonomy "$TAX" --db "$DB"                  # tags migrate
```

A review that approves no taxonomy change writes no `taxonomy_v1.json` (the apply output says `taxonomy_changed: false`), but it still deletes `PROVISIONAL`.

`serve` binds `127.0.0.1` on a free port, prints `review app: <url>` on stderr and opens the browser; if no browser opened, give the user that URL from the background task's output. It exits on submit, on "close without submitting" (`cancelled`) or after `--timeout` seconds (default 3600); decisions are saved, so run `serve` again to continue. The review scripts are stdlib only. Applying deletes `taxonomy/PROVISIONAL`, so `onboard.py verify` can pass and the store can be deployed. If `taxonomy/work/reclassify.json` exists after `build_graph`, reclassify those chunk ids before writing any approved tags, then run `classify_write.py --merge` for the approved-tags result directory the review printed. Full procedure: `corpus-taxonomy-extraction` → "A. First-build review".

**Until this review is applied, `taxonomy/PROVISIONAL` exists and `onboard.py verify` fails; do not deploy.** The old draft-only review (plan/serve/apply on the bare `taxonomy_v0.json`, no classification) remains documented as a fallback for the case where the corpus is not indexed yet — see `corpus-taxonomy-extraction` → "A′. Draft review without an index (fallback)".

### Phase 6 — deterministic derived layers

```bash
"$PY" "$SKILLS/knowledge-index/knowledge_index.py" related --db "$DB"

# Only when reporting workbooks exist and the family config has been reviewed:
"$PY" "$SKILLS/tabular-semantic-layer/build_marts.py" \
  --root "$REPORTING" \
  --config "$PROJECT/schema/families.<corpus>.json" \
  --out-dir "$PROJECT/marts" --db "$DB" --strict

# Optional human-readable view:
"$PY" "$SKILLS/corpus-taxonomy-extraction/to_obsidian.py" \
  --db "$DB" --out "$PROJECT/vault" --clean --assets "$PROJECT/assets"
```

Read the marts audit. `--strict` failures are failures, not warnings to suppress.

### Phase 7 — baseline and verification

Only after the final enriched parsed corpus has been indexed:

```bash
"$PY" "$SKILLS/knowledge-pipeline/brain_sync.py" seed \
  --db "$DB" --parsed "$PROJECT/parsed"

"$PY" "$SKILLS/knowledge-pipeline/onboard.py" verify --db "$DB"
```

Report:

- source documents and rendered/flagged/cached visual pages;
- `documents` and `page_render` counts;
- chunks and image-linked chunks;
- classification coverage and assignments;
- graph node/edge counts;
- related edges;
- facts and marts audit status;
- vault notes, if exported;
- smoke-query result.

## Updating an existing brain

### Understand the two-stage delta

There is no current monolithic source-aware command. The orchestrator bridges two deltas:

1. **Source/page delta:** source SHA/path identifies changed documents; rendered `img_sha` identifies changed pages; `page_render` prevents repeated VLM transcription.
2. **Parsed/store delta:** `brain_sync` hashes final `parsed/*.md`; the `documents` table determines added/changed/unchanged/deleted docs for embedding and chunk classification.

`./brain plan` and `./brain update` cover only stage 2.

### Update phase 0 — inventory and source delta

Record relative source path + SHA-256 for the current corpus and compare it with the previous source manifest/checkpoint. If no durable source manifest exists, compare against known prior inventory and treat uncertainty explicitly.

Classify changes as:

- added source;
- changed source content;
- unchanged source;
- deleted source;
- changed reporting workbook.

Show this plan to the human. Confirm deletions before removing derived files. `brain_sync plan` requires an existing seeded store and opens it read-only; a missing store or `documents` table is an error rather than something plan creates.

### Update phase 1 — refresh only changed visual documents

For each added/changed narrative source:

1. clear or replace its stable collision-free asset output so removed pages cannot linger;
2. run `render_pages.py` using the same source-relative asset-root mapping as the original build;
3. create a fresh run-specific vision directory and run `vision_prep.py --db "$DB"` over those changed render directories;
4. dispatch only emitted vision batches;
5. validate results;
6. run `vision_assemble.py --results ... --db "$DB"` into the stable parsed path.

For deleted sources, remove their final parsed Markdown and corresponding assets. Do not globally delete `parsed/` unless intentionally rebuilding everything.

If a changed source produces identical final parsed Markdown, `brain_sync` correctly marks it unchanged. If the rendered page is identical, VLM is skipped by `img_sha` even when the enclosing file changed.

### Update phase 1b — refresh changed meeting recordings

A recording has no single "changed" signal — the video, its sidecar transcript, or both can
change independently — so what to re-run depends on which changed:

| What changed | Re-run |
|---|---|
| Video content | `probe`, `transcribe` (only if `probe.json` says `asr`), `frames`, VLM pass, `assemble` |
| Sidecar `.vtt`/`.srt`/`.docx` (incl. a Teams `.docx` paired by its title) changed or added | `probe`, `assemble` |
| Sidecar removed | `probe`, `transcribe`, `assemble` |
| Video removed | `./brain source remove <source-id> --yes` (tombstone), then `video_capture.py forget --source <rel> --parsed "$PROJECT/parsed" --assets-root "$PROJECT/assets" --work "$PROJECT/video"` |

Existing projects must have the video globs (e.g. `"**/*.mp4"`) in `brain.toml`'s `include`
list — `brain_doctor.py` warns when it finds videos in the corpus that no registered root's
`include` matches. `brain_sync plan` reports a `superseded_by_video` key for transcript
documents retired because their video's parsed document now supersedes them: expected, not an
unexpected removal.

### Update phase 2 — inspect and apply parsed delta

```bash
"$PY" "$SKILLS/knowledge-pipeline/brain_sync.py" plan \
  --db "$DB" --parsed "$PROJECT/parsed"
```

Show counts and filenames. After human confirmation:

```bash
"$PY" "$SKILLS/knowledge-pipeline/brain_sync.py" apply \
  --db "$DB" --parsed "$PROJECT/parsed" --out "$PROJECT"
```

`apply` creates a timestamped SQLite snapshot, deletes removed docs, re-embeds added/changed docs, updates `documents`, rebuilds related unless disabled, and writes `sync_plan.json`. Its database mutations commit together; retain the snapshot so a failed downstream classification/verification can still be rolled back deliberately.

Read `sync_plan.json`; do not infer changed chunk IDs yourself.

### Update phase 3 — classify only changed chunks

Extract `reclassify_chunk_ids` from `sync_plan.json` and pass them to:

```bash
"$PY" "$SKILLS/corpus-taxonomy-extraction/classify_prep.py" \
  --db "$DB" --taxonomy "$TAX" --out "$CLASSIFY_UPDATE_RUN" \
  --chunks <comma-separated-ids> --batches <N>
```

`CLASSIFY_UPDATE_RUN` must be a new empty directory for this update. Dispatch and validate classification subagents as above, then:

```bash
"$PY" "$SKILLS/corpus-taxonomy-extraction/classify_write.py" \
  --db "$DB" --results "$CLASSIFY_UPDATE_RUN"
```

No `--reset`: incremental mode replaces tags only for IDs present in the results.

Then reconcile derived views:

```bash
"$PY" "$SKILLS/corpus-taxonomy-extraction/build_graph.py" --db "$DB" --taxonomy "$TAX"
"$PY" "$SKILLS/knowledge-index/knowledge_index.py" related --db "$DB"
"$PY" "$SKILLS/corpus-taxonomy-extraction/to_obsidian.py" \
  --db "$DB" --out "$PROJECT/vault" --clean --assets "$PROJECT/assets"
```

Re-run `build_marts.py --strict` only if reporting workbooks or their schema config changed. It is a full idempotent recomputation, not a row-level delta.

### Update phase 4 — taxonomy gate

Reuse the existing taxonomy by default. If `$TAX` is missing, or `build_graph` stops because the store has tags and no `meta.taxonomy_version`, tell the human and follow `corpus-taxonomy-extraction` → "F. Upgrading an older Brain" with their confirmation. Then check the share of untagged chunks and whether the taxonomy has descriptions. A chunk the classifier verdicted `__no_topic__` (filler, boilerplate, off-goal — stored in `chunk_verdicts`, not `chunk_topics`) is not untagged; exclude it, and report it separately. Older stores predate `chunk_verdicts`, so the query guards for the table's existence:

```bash
"$PY" -c 'import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);t=c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0];has_v=bool(c.execute("SELECT 1 FROM sqlite_master WHERE type=\"table\" AND name=\"chunk_verdicts\"").fetchone());nt=c.execute("SELECT COUNT(*) FROM chunk_verdicts WHERE verdict=\"no_topic\" AND chunk_id IN (SELECT id FROM chunks)").fetchone()[0] if has_v else 0;q="SELECT COUNT(*) FROM chunks WHERE id NOT IN (SELECT chunk_id FROM chunk_topics)"+(" AND id NOT IN (SELECT chunk_id FROM chunk_verdicts WHERE verdict=\"no_topic\")" if has_v else "");u=c.execute(q).fetchone()[0];print(u,"of",t,"chunks untagged (excludes",nt,"no-topic)")' "$DB"
"$PY" -c 'import json,sys;print(len(json.load(open(sys.argv[1])).get("descriptions") or {}),"categories described")' "$TAX"
```

If the update introduced concepts the vocabulary cannot express (a rising untagged share, counted as above, excluding no-topic chunks), the taxonomy has no descriptions, or the human asks to refresh or clean up the taxonomy, tell the human and offer the **health review**. Run it only if they agree, following `corpus-taxonomy-extraction` → "B. Health review" end to end: `taxonomy_review.py diagnose` → fix subagents per task dir → `plan --mode health --work …` → `serve --watch-hint` in the background with a Monitor on `taxonomy/work/requests.jsonl` for redo requests → on submit `taxonomy_merge.py --review <review> --apply` → `build_graph.py` → reclassify (below) → `classify_write.py --merge` for approved tags → offer governed-metric drafts. When the human wants only new-category proposals for untagged chunks, use "C. Refine review" (`taxonomy_refine_prep.py` → proposal subagents → `plan --mode drift`) instead; when they want to edit categories themselves, "D. Browse and edit".

After any applied review, run `build_graph.py --taxonomy "$TAX" --db "$DB"`, then, if `$PROJECT/taxonomy/work/reclassify.json` exists, reclassify its chunk ids into a fresh directory named after the file's `version` `<N>`:

```bash
IDS=$("$PY" -c 'import json,sys;print(",".join(map(str,json.load(open(sys.argv[1]))["chunk_ids"])))' \
  "$PROJECT/taxonomy/work/reclassify.json")
"$PY" "$SKILLS/corpus-taxonomy-extraction/classify_prep.py" \
  --db "$DB" --taxonomy "$TAX" --chunks "$IDS" --out "$PROJECT/classify/reclassify-v<N>"
# dispatch and validate classification subagents over that directory, then:
"$PY" "$SKILLS/corpus-taxonomy-extraction/classify_write.py" \
  --db "$DB" --results "$PROJECT/classify/reclassify-v<N>" \
  --reclassify-done "$PROJECT/taxonomy/work/reclassify.json"
```

Never reuse the first-build `classify/` directory (or any directory that already holds `result_*.json`): `classify_write` reads every result file there, so stale results would overwrite the new tags and `--reclassify-done` would drop the queue as done.

Never apply taxonomy changes outside a submitted review, except with `taxonomy_merge.py --without-review` when the human explicitly asked to skip the review ("E. Without review"). `taxonomy/decisions.jsonl` is the approval record and belongs in the project's git.

### Update phase 5 — verify or rollback

Run verification and compare counts with the pre-update checkpoint. Spot-check:

- one changed visual page retrieves its VLM structure;
- one unchanged page reused its cache;
- deleted docs have no chunks or vault notes;
- changed chunks have fresh taxonomy tags;
- exact numbers still come from `facts`;
- marts audit is clean when marts changed.

If apply or downstream validation fails and the store cannot be completed safely:

```bash
"$PY" "$SKILLS/knowledge-pipeline/brain_sync.py" rollback --db "$DB"
```

A rollback restores SQLite only. Also restore/reconcile `parsed/`, assets, and the vault from the checkpoint or rerun their deterministic steps.

## Batch validation checklist

Before consuming vision results (this applies identically to a recording's video-vision run
directory):

- expected hashes = union of all `vision/batch_*.json` `img_sha` values;
- actual hashes = union of all `vision/result_*.json` keys;
- expected equals actual;
- no duplicate keys across result files;
- values are non-empty strings.

Before running `video_capture.py assemble` for a recording: every **kept** (non-`dropped`)
frame in `pages.json` must have a matching VLM result — `assemble` itself refuses and lists
the missing `img_sha` values, so treat that refusal as validation failing, not as a bug to
route around; rerun `vision_prep.py` and dispatch the missing batch instead.

Before consuming classification results:

- expected IDs = union of all `classify/batch_*.json` IDs;
- actual IDs = union of all `classify/result_*.json` keys converted to integers;
- expected equals actual;
- no duplicate IDs across result files;
- values are lists of 0–3 labels;
- every label exactly matches `vocab.md`.

If validation fails, rerun only missing/invalid items. Never fill missing results with guesses.

## Checkpointing and resumability

For a long build, keep a small execution log in the brain project, for example:

```text
.build/<timestamp>/
  plan.md
  source_manifest.json
  phase-01-render.json
  phase-02-vision-validation.json
  phase-04-taxonomy-review.md
  phase-05-classification-validation.json
  final-verification.md
```

After each phase record commands, inputs, counts, failures, and next action. On resume, inspect disk and the last checkpoint before launching more subagents. Do not duplicate a batch already represented by a valid result file.

## Human-facing completion report

End with a concise report containing:

1. what corpus and goal were used;
2. source/page/visual counts and cache reuse;
3. added/changed/deleted parsed delta;
4. agent batches run and any retries;
5. taxonomy decision and classification coverage;
6. store row counts and marts audit;
7. verification result and known gaps;
8. exact project paths for `knowledge.sqlite`, taxonomy, parsed corpus, assets, and vault.

Do not say “incremental update complete” if only `brain_sync apply` ran while changed visual sources were never rendered and assembled.
