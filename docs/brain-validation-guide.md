# Brain Validation Guide — Testing VTT Indexing with knowledge-pipeline

> Goal: create a fresh brain from your transcripts folder and confirm that VTT parsing,
> indexing, and retrieval all work correctly after any changes to the repo.

---

## Before you start — load your environment

### Step A — repo variables (`.env`)

The repo `.env` holds machine-specific paths. Load it once at the start of each session:

```bash
cd /Users/Karen_Florykian/projects/aipods/applied_skills-clean
set -a && source .env && set +a
```

Verify:

```bash
echo $VENV && echo $SKILLS && echo $SOURCES && echo $PROJECT
```

### Step B — AWS Bedrock credentials (`~/projects/kt-docs/brain/.env`)

Required for TC-4 (taxonomy classification) and TC-5 (evals). Contains three variables:

```bash
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
```

Load and verify:

```bash
set -a && source ~/projects/kt-docs/brain/.env && set +a
unset AWS_SESSION_TOKEN          # IAM user key — must NOT have a session token

echo "Key:    ${AWS_ACCESS_KEY_ID:0:8}..."
echo "Region: $AWS_REGION"
echo "Token:  '${AWS_SESSION_TOKEN:-UNSET}'"   # must print UNSET
```

### Step C — taxonomy path (per-session, per-corpus)

`TAXO` is not in any `.env` — it changes per project and must never be committed.
Set it by hand each session before running TC-4 or TC-5.

For the VTT corpus (7 categories: QualityRisk, ActionItem, TestStrategy, …):

```bash
TAXO=/Users/Karen_Florykian/projects/kt-docs/brain/taxonomy_v0.json
```

For a different corpus, point at its own taxonomy JSON instead.

**Why the split:** credentials belong in `~/projects/kt-docs/brain/.env` (sensitive,
reused across projects). The taxonomy path belongs in the terminal (changes per session,
project-specific). Steps TC-1 through TC-3 need only the repo `.env`; TC-4 and TC-5 need
all three.

---

## Step 1 — Scaffold the project

```bash
$VENV $SKILLS/knowledge-pipeline/onboard.py scaffold \
  --project "$PROJECT" \
  --goal    "Validate VTT/SRT corpus indexing after evals changes" \
  --docs    "$SOURCES"
```

### What "scaffold" actually means

Before you can build a brain, every downstream tool needs to know three things: where the
source files live, where to write its outputs, and what the brain is for. "Scaffold" is the
step that answers all three questions by creating the **project folder structure** and the
**configuration files** that every later command reads.

Think of it like setting up a new project in an IDE before you write any code — you are not
building anything yet, you are just making sure the workspace is ready and all the tools agree
on where things go.

**Concretely, scaffold does five things:**

1. **Creates the folder layout** at `$PROJECT/`:

   ```
   schema/       ← knowledge.sqlite will be written here
   parsed/       ← Markdown files go here after parse_corpus.py
   taxonomy/     ← taxonomy_v0.json goes here after induction
   classify/     ← batch files for the classification agents
   vision/       ← rendered page images for visual-parse
   marts/        ← Excel → facts output
   vault/        ← Obsidian-format view of the store
   .incoming/    ← managed drop folder for chat attachments
   ```

   None of the later scripts create these folders themselves — they assume they exist. If you
   skip scaffold and run `parse_corpus.py` directly, you have to create them manually.

2. **Writes `goal.txt`** — one sentence describing what the brain is for. This is the
   "noise filter": the taxonomy induction step reads it to decide which categories matter and
   which to demote. Without a goal, the taxonomy captures everything equally and retrieval
   becomes diffuse.

3. **Writes `brain.toml`** — the source registry. It records *where your source folders
   live* (as paths relative to the project, so the brain stays portable if you move it) and
   *how to treat them*:
   - `import` mode: new files are discovered on each run, but a file that goes missing is
     never automatically deleted from the brain. Safe default.
   - `mirror` mode: the folder is authoritative — a missing file becomes a removal candidate
     (still requires human approval before deletion).
   - `managed` mode: the `.incoming` folder is brain-owned storage for chat attachments.

4. **Writes config templates** — `schema/families.<corpus>.json` and
   `schema/metrics.<corpus>.json`. These are for the numeric marts lane (Excel workbooks).
   You do not need to edit them for a VTT-only validation run.

5. **Writes `BRAIN.md`** — a filled-in build plan with the exact commands for your specific
   paths and interpreter. This is your runbook: every command uses real paths, not
   placeholders.

### What scaffold does NOT do

Scaffold does not touch your source files, parse anything, or write to the SQLite database.
It is purely a setup step — completely safe to re-run. If `brain.toml` already exists, it
will not overwrite it.

### Do you need to create `goal.txt` or `brain-maintenance.toml` manually?

**`goal.txt` — no, scaffold creates it for you** from the `--goal` argument you pass. You
only need to edit it manually if you want to change the goal on an already-scaffolded project:

```bash
echo "New goal string" > $PROJECT/goal.txt
```

**`brain-maintenance.toml` — not needed for this validation run.** This file belongs to the
`brain-maintenance` skill, which handles *ongoing updates* to an already-built brain
(incremental re-indexing, source refresh, deployment). For a one-off validation (build once,
verify retrieval works) you do not need it.

You only create it when you move from "validate once" to "maintain this brain ongoing":

```bash
cp $SKILLS/brain-maintenance/profile.example.toml $PROJECT/brain-maintenance.toml
```

Then edit three lines to match your project paths:

```toml
[runtime]
python = "/Users/Karen_Florykian/projects/aipods/applied_skills-clean/.claude/venv/bin/python"
skills = "/Users/Karen_Florykian/projects/aipods/applied_skills-clean/bundles/brain/skills"
```

**Summary of what scaffold creates vs what you create:**

| File | Who creates it | When |
|---|---|---|
| `goal.txt` | `scaffold --goal "..."` | Automatically on first scaffold |
| `brain.toml` | `scaffold` | Automatically on first scaffold |
| `BRAIN.md` | `scaffold` | Automatically on first scaffold |
| `brain-maintenance.toml` | You copy from `profile.example.toml` | Only when setting up ongoing maintenance |

### What to check

```bash
# Folder layout was created
ls $PROJECT/

# goal.txt has your goal string
cat $PROJECT/goal.txt

# brain.toml points at your sources folder
cat $PROJECT/brain.toml

# BRAIN.md exists and contains real paths (not <placeholders>)
head -30 $PROJECT/BRAIN.md
```

---

## Step 2 — Parse sources → Markdown

VTT/SRT and narrative documents must be parsed in **two separate passes** — `--merge-cues`
applies only to transcripts and must not be passed for PDF/PPTX/DOCX.

**Step 2a — parse transcripts (skip if corpus has no VTT/SRT files):**

```bash
$VENV $SKILLS/corpus-taxonomy-extraction/parse_corpus.py \
  --corpus     "$SOURCES" \
  --out        "$PROJECT/parsed" \
  --formats    vtt,srt \
  --merge-cues 10
```

> **`--merge-cues 10` is required for VTT/SRT corpora.** Without it every individual
> cue becomes its own chunk (~79 chars average). With `--merge-cues 10`, consecutive
> same-speaker cues are joined into speaker-turn paragraphs (~800 chars), which are
> large enough to classify and retrieve meaningfully. Omitting this flag produces
> ~25k tiny chunks that the classification agents correctly label as `[]` (nothing
> to classify), and retrieval quality degrades severely.

**Step 2b — parse narrative documents:**

```bash
$VENV $SKILLS/corpus-taxonomy-extraction/parse_corpus.py \
  --corpus  "$SOURCES" \
  --out     "$PROJECT/parsed" \
  --formats pptx,docx,pdf
```

> Do **not** pass `--merge-cues` here — the flag is only meaningful for VTT/SRT.

**What happens:** each VTT file is converted to a Markdown file with `## [SPEAKER NAME]`
sections. Each PPTX goes through LibreOffice → PDF → PyMuPDF and becomes `## [page N]`
sections. One `.md` per source file, written to `$PROJECT/parsed/`.

**What to check:**

```bash
ls $PROJECT/parsed/*.md | wc -l   # should equal your source file count

# Open one VTT output — should show speaker sections with real dialogue
head -40 $PROJECT/parsed/$(ls $PROJECT/parsed/*.md | head -1 | xargs basename)
```

---

## Step 3 — Index into SQLite

```bash
$VENV $SKILLS/knowledge-index/knowledge_index.py index \
  --db     "$PROJECT/schema/knowledge.sqlite" \
  --corpus "$PROJECT/parsed" \
  --reset
```

**What happens:** each `.md` is split into chunks of ≤1200 characters, a float32 embedding
is generated per chunk (fastembed/onnx, no GPU), and everything is written to the SQLite
file — `chunks` table for text, `chunks_vec` virtual table for vectors, `chunks_fts` for
full-text search.

**What to check — smoke retrieval:**

```bash
$VENV $SKILLS/knowledge-index/knowledge_index.py search \
  --db   "$PROJECT/schema/knowledge.sqlite" \
  --query "action item" \
  --k 3 \
  --json | python3 -m json.tool | head -40
```

You should see 3 results with `score > 0` and `text` containing real phrases from your
transcripts.

---

## Step 4 — Verify the store

```bash
$VENV $SKILLS/knowledge-pipeline/onboard.py verify \
  --db   "$PROJECT/schema/knowledge.sqlite" \
  --query "action item"
```

**What a healthy result looks like:**

```
== store: /tmp/vtt-validation-brain/schema/knowledge.sqlite ==
  narrative (RAG)    chunks=N  chunks_fts=N  chunks_vec=N
  taxonomy graph     graph_nodes=—  graph_edges=—  chunk_topics=—   ⚠ EMPTY
  numbers (marts)    facts=—                                         ⚠ EMPTY

  unclassified       N/N chunks (100.0%)

  smoke query «action item» → fts=3 vec=5, top: «some real phrase»

⚠ empty lane(s): taxonomy graph, numbers (marts)
```

**Reading the result:**

| Signal | Expected? | Meaning |
|--------|-----------|---------|
| `chunks=N` non-zero | Yes | VTT files were parsed and chunked — narrative lane works |
| `chunks_vec=N` non-zero | Yes | Embeddings generated — vector search will work |
| `smoke query fts>0 vec>0` | Yes | Both BM25 and vector retrieval fire against your content |
| `top: «real phrase»` | Yes | Retrieval returns actual content from your transcripts |
| `taxonomy graph ⚠ EMPTY` | Yes — expected | We skipped classification (not needed for this validation) |
| `numbers (marts) ⚠ EMPTY` | Yes — expected | We skipped mart build (no Excel processing needed here) |
| `unclassified 100%` | Yes — expected | No classification was run — not a problem for this test |

**Failure signals to watch for:**

| Signal | What it means |
|--------|--------------|
| `chunks=0` | VTT parsing or indexing is broken — go back to Step 2 |
| `chunks_vec=0` | Embedding generation failed — sqlite-vec or fastembed dep issue |
| `smoke query fts=0 vec=0` | Both retrieval modes broken — check that `--reset` ran in Step 3 |
| `smoke query no hits` | Query term not in corpus — try a word you know is in a transcript |

---

## Step 5 — Taxonomy (`taxonomy_v0.json`)

TC-4 and TC-5 need a taxonomy JSON. Before running either, set `$TAXO`.

### Do you need to rebuild it?

| Situation | Action |
|-----------|--------|
| Validation run (same corpus as before) | **Reuse** — set `TAXO` below and skip to TC-4 |
| Added a few new VTT files | **Reuse** — minor additions don't shift categories |
| Added a new topic area or document type | Rebuild from Stage 2 |
| Starting with a brand-new corpus | Full rebuild from Stage 1 |

**For this validation run — reuse the existing file:**

```bash
TAXO=/Users/Karen_Florykian/projects/kt-docs/brain/taxonomy_v0.json
echo $TAXO   # must NOT be empty — verify before running classify_prep
```

> **`$TAXO` is a shell variable, not in `.env`.** It is lost when you open a new terminal.
> Always set it at the start of each session before running TC-4 or TC-5.

Skip the rebuild stages below and go straight to **TC-4**.

---

### Building taxonomy from scratch (only if needed)

Scaffold must run first (Step 1) — Stage 2 reads `goal.txt` which scaffold writes.

**Stage 1 — Parse narrative docs only (pptx/docx/pdf, not VTT):**

```bash
$VENV $SKILLS/corpus-taxonomy-extraction/parse_corpus.py \
  --corpus "$SOURCES" \
  --out    "$PROJECT/map_parsed" \
  --formats pptx,docx,pdf
```

> VTT/XLSX excluded deliberately: VTT adds noise; XLSX explodes into number-grids.
> This parse is separate from Step 2 — it writes to `map_parsed/`, not `parsed/`.

**Stage 2 — Map subagents (agentic — Haiku reads docs, writes JSON):**

Prepare the instructions file (reads `goal.txt` written by scaffold):

```bash
mkdir -p "$PROJECT/map"
GOAL=$(cat "$PROJECT/goal.txt")
sed \
  -e "s|{{GOAL}}|$GOAL|g" \
  -e "s|{{MAP_DIR}}|$PROJECT/map|g" \
  -e "s|{{AUDIENCE}}||g" \
  "$SKILLS/corpus-taxonomy-extraction/map_instructions.template.md" \
  > "$PROJECT/map/map_instructions.md"
```

Dispatch in a new Claude Code session — paste this prompt:

```
Read /tmp/vtt-validation-brain/map/map_instructions.md, then read each .md file in
/tmp/vtt-validation-brain/map_parsed/ and write one JSON per file to
/tmp/vtt-validation-brain/map/ following the schema in the instructions.
```

Check: `ls "$PROJECT/map/"*.json | wc -l` must equal file count in `map_parsed/`.

**Stage 3 — Reduce (deterministic):**

```bash
$VENV $SKILLS/corpus-taxonomy-extraction/consolidate.py \
  --map-dir "$PROJECT/map" \
  --out     "$PROJECT/consolidated.json" \
  --threshold 0.86
```

All zeros? `consolidate.py` silently returns zeros if `map/*.json` is empty — Stage 2
was not run. Check: `ls "$PROJECT/map/"*.json 2>/dev/null | wc -l`

**Stage 4 — Emit:**

```bash
mkdir -p "$PROJECT/taxonomy"
$VENV $SKILLS/corpus-taxonomy-extraction/emit_taxonomy.py \
  --consolidated "$PROJECT/consolidated.json" \
  --map-dir      "$PROJECT/map" \
  --out-json     "$PROJECT/taxonomy/taxonomy_v0.json" \
  --out-md       "$PROJECT/taxonomy/taxonomy_v0.md" \
  --goal         "$(cat $PROJECT/goal.txt)"
```

Review `$PROJECT/taxonomy/taxonomy_v0.md` — the `review_flags` section is advisory only.

Set `TAXO` to the new file:

```bash
TAXO="$PROJECT/taxonomy/taxonomy_v0.json"
```

---

## Quick reference — full sequence

```bash
# 0. Load environment (once per session)
cd /Users/Karen_Florykian/projects/aipods/applied_skills-clean
set -a && source .env && set +a

# 1. Scaffold the project workspace
$VENV $SKILLS/knowledge-pipeline/onboard.py scaffold \
  --project "$PROJECT" \
  --goal    "Validate VTT/SRT corpus indexing after evals changes" \
  --docs    "$SOURCES"

# 2a. Parse transcripts (VTT/SRT only — omit if no transcripts)
$VENV $SKILLS/corpus-taxonomy-extraction/parse_corpus.py \
  --corpus     "$SOURCES" \
  --out        "$PROJECT/parsed" \
  --formats    vtt,srt \
  --merge-cues 10

# 2b. Parse narrative documents (no --merge-cues)
$VENV $SKILLS/corpus-taxonomy-extraction/parse_corpus.py \
  --corpus  "$SOURCES" \
  --out     "$PROJECT/parsed" \
  --formats pptx,docx,pdf

# 3. Index Markdown → SQLite
$VENV $SKILLS/knowledge-index/knowledge_index.py index \
  --db     "$PROJECT/schema/knowledge.sqlite" \
  --corpus "$PROJECT/parsed" \
  --reset

# 4. Verify the store
$VENV $SKILLS/knowledge-pipeline/onboard.py verify \
  --db   "$PROJECT/schema/knowledge.sqlite" \
  --query "action item"

# 5. Set taxonomy (reuse existing — no rebuild needed for validation)
TAXO=/Users/Karen_Florykian/projects/kt-docs/brain/taxonomy_v0.json
# To rebuild from scratch: see "Step 5 — Taxonomy" section above
```

**Pass criteria (Steps 1–4):** `chunks` non-zero, `chunks_vec` non-zero, smoke query
`fts>0 vec>0`, `top:` shows a real phrase from your transcripts. The taxonomy and marts
warnings are expected and do not indicate a failure.

---

## Test scenarios

Three distinct scenarios, each with a different purpose. Run them in order — each builds on the
previous one.

---

### TC-1 — Fresh build from scratch

**What it tests:** the complete pipeline works end-to-end on a clean project with no prior state.

**Precondition:** `$PROJECT` does not exist yet, or you delete it first:
```bash
rm -rf "$PROJECT"
```

**Steps:** run the full sequence in the Quick reference above (Steps 1–4).

**Pass criteria:**
- `ls $PROJECT/` shows all 8 subdirectories after scaffold
- `ls $PROJECT/parsed/*.md | wc -l` equals your source file count
- `verify` shows `chunks=N` non-zero, `chunks_vec=N` non-zero
- `smoke query fts>0 vec>0`, `top:` shows a real phrase

**Fail signal:** any of the above is zero or missing → go back to the step that produced it.

---

### TC-2 — Incremental update (add a new VTT file)

**What it tests:** dropping a new file into the sources folder adds it to the brain without
destroying existing chunks. This is the most important update scenario — it proves `--reset`
is not required for normal corpus growth.

**Precondition:** TC-1 passed. Record the current chunk count:
```bash
python3 -c "
import sqlite3
c = sqlite3.connect('$PROJECT/schema/knowledge.sqlite')
print('chunks before:', c.execute('SELECT COUNT(*) FROM chunks').fetchone()[0])
"
```

**Steps:**

1. Copy one new VTT file into `$SOURCES`:
   ```bash
   cp /path/to/new_meeting.vtt "$SOURCES/"
   ```

2. Re-parse (all source files are always re-parsed — this is expected):
   ```bash
   # VTT/SRT pass (skip if no transcripts)
   $VENV $SKILLS/corpus-taxonomy-extraction/parse_corpus.py \
     --corpus     "$SOURCES" \
     --out        "$PROJECT/parsed" \
     --formats    vtt,srt \
     --merge-cues 10

   # Narrative docs pass
   $VENV $SKILLS/corpus-taxonomy-extraction/parse_corpus.py \
     --corpus  "$SOURCES" \
     --out     "$PROJECT/parsed" \
     --formats pptx,docx,pdf
   ```
   Output will show all files processed. `parse_corpus.py` has no incremental cache — it
   re-parses every source file on every run. This is fast (pure text conversion) and
   correct. The incremental skip happens at the next step.

3. Re-index **without `--reset`** — the indexer skips unchanged `.md` files by content hash,
   adds chunks only for the 2 new ones:
   ```bash
   $VENV $SKILLS/knowledge-index/knowledge_index.py index \
     --db     "$PROJECT/schema/knowledge.sqlite" \
     --corpus "$PROJECT/parsed"
   ```
   Watch for `skipped N unchanged doc(s)` in the output — that's the incremental skip.

4. Verify chunk count increased:
   ```bash
   python3 -c "
   import sqlite3
   c = sqlite3.connect('$PROJECT/schema/knowledge.sqlite')
   print('chunks after:', c.execute('SELECT COUNT(*) FROM chunks').fetchone()[0])
   "
   ```

**Pass criteria:**
- Chunk count after > chunk count before
- `verify` still shows all lanes healthy
- Smoke query still returns hits (existing content not destroyed)

**Fail signal:** chunk count unchanged → the new file was not parsed or not indexed. Check
`$PROJECT/parsed/` for the new `.md` file.

---

### TC-3 — Re-run unchanged corpus (idempotency)

**What it tests:** running the index step again on an unchanged corpus produces the same result
and does not duplicate chunks. This proves the pipeline is safe to re-run.

**Precondition:** TC-1 or TC-2 passed. Record the chunk count:
```bash
python3 -c "
import sqlite3
c = sqlite3.connect('$PROJECT/schema/knowledge.sqlite')
print('chunks before:', c.execute('SELECT COUNT(*) FROM chunks').fetchone()[0])
"
```

**Steps:** re-run the index step without `--reset`:
```bash
$VENV $SKILLS/knowledge-index/knowledge_index.py index \
  --db     "$PROJECT/schema/knowledge.sqlite" \
  --corpus "$PROJECT/parsed"
```

Watch the output — it should report `skipped N unchanged doc(s)`.

**Pass criteria:**
- Output says `skipped N unchanged doc(s)` where N = your file count
- Chunk count after = chunk count before (no duplicates)
- `verify` smoke query still returns the same hits

**Fail signal:** chunk count doubled → `--reset` logic is broken or the file hash comparison
is not working.

---

---

### TC-4 — Taxonomy tagging (classification)

**What it tests:** chunks get category labels from the taxonomy so that eval questions routed
by category (ActionItem, QualityRisk, etc.) can find relevant content. This is what fills the
`taxonomy graph ⚠ EMPTY` warning from TC-1.

**Precondition:** TC-1 passed (parse + index done). You need a taxonomy JSON and AWS credentials.

Set `TAXO` to the existing taxonomy for this corpus — no rebuild needed:
```bash
TAXO=/Users/Karen_Florykian/projects/kt-docs/brain/taxonomy_v0.json
```

Load AWS credentials:
```bash
set -a && source ~/projects/kt-docs/brain/.env && set +a
unset AWS_SESSION_TOKEN
```

**Steps:**

1. Prepare classification batches:
   ```bash
   $VENV $SKILLS/corpus-taxonomy-extraction/classify_prep.py \
     --db       "$PROJECT/schema/knowledge.sqlite" \
     --taxonomy "$TAXO" \
     --out      "$PROJECT/classify" \
     --batches  25
   ```
   Check: `ls $PROJECT/classify/batch_*.json | wc -l` shows the expected batch count.

   **Choosing `--batches N`:** `N` controls how many agents you dispatch and how many
   chunks each one gets (`chunks_per_batch = ceil(total / N)`). The math matters — too
   few batches means each agent receives thousands of chunks and hits its context limit
   before writing output.

   | `--batches` | Chunks per agent (25k corpus) | Approx. input tokens | Risk |
   |---|---|---|---|
   | 5 | ~5,100 | ~160k | Hits context limit; agent stalls or writes nothing |
   | 25 | ~1,020 | ~32k | Comfortable; reliable output ✓ recommended |
   | 50 | ~510 | ~16k | Fast, very safe; more sessions to open |

   More batches = more agents to dispatch, but each agent finishes reliably. Quality and
   output format are unchanged — `classify_write.py` reads all `result_*.json` files
   regardless of count.

   > **Output directory note:** agents write `result_*.json` into the same directory as
   > the batch files — `$PROJECT/classify/`, **not** a separate `results/` subdirectory.
   > Verify the path before dispatching:
   > ```bash
   > ls $PROJECT/classify/   # should show batch_*.json + instructions.md + vocab.md
   > ```

2. Dispatch one agent per batch file — all in parallel. For each batch N (0–24),
   open a new Claude Code session and paste this prompt, substituting the batch number:

   ```
   Read /tmp/vtt-validation-brain/classify/instructions.md and
   /tmp/vtt-validation-brain/classify/vocab.md.

   Then read /tmp/vtt-validation-brain/classify/batch_0.json.

   For each chunk in the batch: look at its title and preview, pick 0–3
   category names from vocab.md using your judgment, following the rules
   in instructions.md.

   Write the result to /tmp/vtt-validation-brain/classify/result_0.json
   in the exact schema from instructions.md.

   Do not write any Python or code. Do not ask questions.
   Just read, classify, and write the file.
   ```

   Change `batch_0` → `batch_1` and `result_0` → `result_1` for each subsequent agent.

   **If the agent asks "should I write a classifier?" or starts writing Python — stop it.**
   Reply: "No code. Read each chunk's title and preview, assign labels from vocab.md,
   write result_N.json. Start now."

   **Monitoring while agents run:** the 25 background agents show in the Claude Code agent
   panel (`↓` to open). Each takes ~10–15 minutes. To check progress:
   ```bash
   ls $PROJECT/classify/result_*.json 2>/dev/null | wc -l   # increments 0→25 as each finishes
   ```
   Do not proceed to step 3 until this count equals your batch count (25).

3. Build the taxonomy graph **before** writing classification results:
   ```bash
   $VENV $SKILLS/corpus-taxonomy-extraction/build_graph.py \
     --taxonomy "$TAXO" \
     --db       "$PROJECT/schema/knowledge.sqlite"
   ```

   **What this does:** reads the L1/L2 category hierarchy from `taxonomy_v0.json` and
   writes two tables into SQLite:

   - **`graph_nodes`** — one row per category: `id`, `label`, `kind` (intent_l1 or
     intent_l2), `parent`
   - **`graph_edges`** — one row per parent→child link: `source`, `target`,
     `rel='subclass_of'`

   This is the city map. The next step (`classify_write`) tags each chunk with its most
   specific category — e.g. "Load Testing" (L2). The graph records that "Load Testing"
   is a `subclass_of` "Performance Testing Objectives" (L1). So a retrieval query for
   the parent automatically finds all chunks tagged with any child, via a SQL JOIN —
   without re-reading the text.

   **Must run before `classify_write`:** `classify_write.py` inserts `about` edges into
   `graph_edges`. That table is created by `build_graph.py` — if you reverse the order,
   `classify_write` aborts with `ERROR: graph_edges table not found`.

   **Safe to re-run:** `build_graph.py` only deletes `subclass_of` edges (taxonomy
   structure). It never touches `about` edges written by `classify_write`. You can
   rebuild the graph after updating the taxonomy without losing any classification work.

4. Write classification results back to SQLite:
   ```bash
   $VENV $SKILLS/corpus-taxonomy-extraction/classify_write.py \
     --db      "$PROJECT/schema/knowledge.sqlite" \
     --results "$PROJECT/classify"
   ```

**Pass criteria** — re-run verify:
```bash
$VENV $SKILLS/knowledge-pipeline/onboard.py verify \
  --db "$PROJECT/schema/knowledge.sqlite" \
  --query "Gatling"
```
- `taxonomy graph` row shows `graph_nodes=N  graph_edges=N  chunk_topics=N` — all non-zero
- `unclassified` percentage drops below 10%
- No more `⚠ EMPTY` on the taxonomy graph lane

**Triage table — what the verify output tells you:**

| Signal | What it means | Fix |
|--------|--------------|-----|
| `chunk_topics=0` | `classify_write` ran before any result files were written, or all result files are malformed | Check `ls $PROJECT/classify/result_*.json \| wc -l`; re-run `classify_write` once files exist |
| `chunk_topics=N` but unclassified >90% | Only 1–2 result files were present when `classify_write` ran — most batches hadn't finished | Check how many result files exist vs your batch count; dispatch missing agents; re-run `classify_write` (incremental — safe to re-run, adds missing chunks without duplicating) |
| `graph_nodes=0` | `build_graph` didn't run, or ran against an empty/wrong taxonomy file | Re-run `build_graph.py --taxonomy "$TAXO" --db ...`; verify `$TAXO` is non-empty |
| `fts=0 vec>0` on smoke query | The query word doesn't appear verbatim in any chunk text (e.g. spoken name transcribed differently) | Not a pipeline bug — try a common phrase you know is in the transcripts (e.g. "action item"); if that returns `fts>0` then FTS is healthy |
| `fts>0 vec=0` | sqlite-vec extension not loaded | Check `pip show sqlite-vec`; re-run `knowledge_index.py index --reset` |
| `fts=0 vec=0` | Index wasn't built or `--reset` wasn't passed on first run | Re-run `knowledge_index.py index --db ... --corpus ... --reset` |

**Most common issue — high unclassified% with non-zero `chunk_topics`:**

This means `classify_write` ran while only a few result files existed. The fix is to
wait for all batch agents to finish writing their `result_*.json`, then re-run
`classify_write` — it will add labels for the previously unlabelled chunks without
touching the ones already classified:

```bash
# How many result files do you have vs how many batches?
ls $PROJECT/classify/result_*.json 2>/dev/null | wc -l   # must equal batch count before continuing
ls $PROJECT/classify/batch_*.json  2>/dev/null | wc -l   # this is your target

# Once all result files are present, re-run classify_write
$VENV $SKILLS/corpus-taxonomy-extraction/classify_write.py \
  --db      "$PROJECT/schema/knowledge.sqlite" \
  --results "$PROJECT/classify"

# Re-verify
$VENV $SKILLS/knowledge-pipeline/onboard.py verify \
  --db "$PROJECT/schema/knowledge.sqlite" \
  --query "action item"
```

---

### TC-5 — Evals against the brain

**What it tests:** the full eval pipeline (extract verbatim facts → generate eval CSV →
generate promptfoo YAML → run against brain) produces a pass rate ≥ 90% on your corpus.
This is the end-to-end regression test for VTT retrieval quality.

**Precondition:** TC-1 passed (TC-4 optional but improves quality). AWS credentials loaded.

**Steps:**

1. Extract verbatim facts from parsed Markdown:
   ```bash
   $VENV $SKILLS/evals/extract_facts.py \
     --parsed   "$PROJECT/parsed" \
     --taxonomy "$TAXO" \
     --out      "$PROJECT/extractions"
   ```

2. Generate eval CSV:
   ```bash
   $VENV $SKILLS/evals/generate_evals.py \
     --extractions "$PROJECT/extractions" \
     --taxonomy    "$TAXO" \
     --out         "$PROJECT/evals.csv"
   ```

3. Generate promptfoo YAML with persona injected:
   ```bash
   $VENV $SKILLS/evals/generate_promptfoo.py \
     --csv        "$PROJECT/evals.csv" \
     --out        "$PROJECT/promptfooconfig.yaml" \
     --brain-url  "http://localhost:9100" \
     --context-js "$SKILLS/evals/load_brain_context.js" \
     --taxonomy   "$TAXO"
   ```

4. Run evals against the brain:
   ```bash
   bash $SKILLS/evals/run_brain_eval.sh \
     --db     "$PROJECT/schema/knowledge.sqlite" \
     --config "$PROJECT/promptfooconfig.yaml" \
     --port   9100
   ```

5. Review results:
   ```bash
   npx promptfoo@0.123.0 view
   ```

**Pass criteria:** ≥ 90% pass rate across all three models (Haiku, Sonnet, Opus).

**Fail signals and where to look:**

| Signal | Where to look |
|--------|--------------|
| `fetch failed` errors | Brain server didn't start — check port 9100 is free |
| 0% pass rate | Smoke query in TC-1 — is retrieval returning real content? |
| <50% pass rate | Open a failing eval — is the retrieved context relevant? |
| <90% pass rate | Check rubric `must_contain` — may be too strict for paraphrased answers |

---

### TC-6 — Tabular mart lane (numbers)

**What it tests:** Excel workbooks are loaded into the `facts` table so that numeric
questions (counts, percentages, KPIs by month/entity) get exact SQL answers instead of
approximate RAG answers. This fills the `numbers (marts) ⚠ EMPTY` warning from TC-1.

**Precondition:** TC-1 passed. You need at least one Excel file (`.xlsx`/`.xlsm`) in your
reporting folder, and a `families.json` config that describes its layout.

If you don't have a families config yet, the scaffold created a template at
`$PROJECT/schema/families.<corpus>.json` — copy and edit it to match your workbook's
sheet names and metric column headers.

```bash
FAMILIES="$PROJECT/schema/families.$(basename $SOURCES).json"
```

**Steps:**

1. Build the marts:
   ```bash
   $VENV $SKILLS/tabular-semantic-layer/build_marts.py \
     --root    "$SOURCES" \
     --config  "$FAMILIES" \
     --out-dir "$PROJECT/marts" \
     --db      "$PROJECT/schema/knowledge.sqlite"
   ```
   The output shows `TOTAL N facts -> SQLite` on stderr. Below it: a coverage table
   (family, grain, fact count, month count, entity count) and any `⚠ PARTIAL` or
   `❌ ZERO` warnings.

2. Verify the `facts` table is populated:
   ```bash
   $VENV $SKILLS/knowledge-pipeline/onboard.py verify \
     --db "$PROJECT/schema/knowledge.sqlite" \
     --query "placeholder"
   ```
   The `numbers (marts)` line should now show `facts=N` (non-zero) with no `⚠ EMPTY`.

3. List available metrics:
   ```bash
   $VENV $SKILLS/hybrid-retrieval/query.py \
     --db      "$PROJECT/schema/knowledge.sqlite" \
     --catalog "$FAMILIES" \
     --list
   ```
   Each line: `metric_name  family.measure  unit  description`.

4. Query a metric:
   ```bash
   $VENV $SKILLS/hybrid-retrieval/query.py \
     --db      "$PROJECT/schema/knowledge.sqlite" \
     --catalog "$FAMILIES" \
     --metric  <metric_name> \
     --month   2026-06
   ```
   Every row includes `source_file` — the exact workbook the value came from.

**Pass criteria:**
- `build_marts.py` output shows `TOTAL N facts` with N > 0
- `verify` shows `facts=N` (no `⚠ EMPTY` on the numbers lane)
- `--list` shows your configured metrics
- A `--metric` query returns rows with real values and `source_file` citations

**Fail signals:**

| Signal | What it means |
|--------|--------------|
| `TOTAL 0 facts` | Glob matched no files, or header row detection failed — check `build_audit.json` in `$PROJECT/marts/` |
| `⚠ PARTIAL` warnings | Some measure column headers didn't match — edit `families.json` column names |
| `❌ ZERO-FACT` warnings | A file was globbed but yielded nothing — open the workbook and compare sheet names to config |
| `(no rows)` on query | Metric exists but month/entity filter returns nothing — try without `--month` |

**Escape hatch — raw SQL:**
```bash
$VENV $SKILLS/hybrid-retrieval/query.py \
  --db  "$PROJECT/schema/knowledge.sqlite" \
  --sql "SELECT family, grain, COUNT(*) FROM facts GROUP BY family, grain"
```

---

### TC-7 — Obsidian vault (human-readable view)

**What it tests:** the brain can be exported as a navigable Obsidian vault — one note per
retrieval chunk, with YAML frontmatter tags and inter-note links. This is how you browse
the brain as a human: see exactly what chunks exist, how they're categorized, and which
sections are "related" via semantic similarity.

**Precondition:** TC-1 passed (narrative lane). TC-4 (taxonomy tagging) is optional — the
vault is useful without tags, but tags add `intent/` topic links to every note.

**Steps:**

1. Generate the vault:
   ```bash
   $VENV $SKILLS/corpus-taxonomy-extraction/to_obsidian.py \
     --db    "$PROJECT/schema/knowledge.sqlite" \
     --out   "$PROJECT/vault" \
     --clean
   ```
   Output: `wrote N notes -> $PROJECT/vault (M docs in a folder tree; ...)`

2. Open in Obsidian:
   - Open Obsidian → "Open folder as vault" → select `$PROJECT/vault`
   - Or browse files directly: `ls "$PROJECT/vault"`

3. Spot-check the structure:
   ```bash
   find "$PROJECT/vault" -name "*.md" | head -20
   ls "$PROJECT/vault/_topics/" 2>/dev/null | head -10
   ```

**What the vault looks like:**

```
$PROJECT/vault/
  transcripts/
    2026-01-15-kickoff/
      2026-01-15-kickoff.md     ← MOC (map of content): topics + section list
      01 Speaker Introduction.md
      02 Agenda Review.md
      ...
  _topics/
    ActionItem.md               ← taxonomy L1 vertex with L2 children listed
    QualityRisk.md
    ...
```

Each section note has:
- YAML frontmatter with `tags: [source/transcripts, intent/action-item]`
- The full chunk text (what the brain returns for that retrieval hit)
- `## Related sections` links to semantically similar chunks (if computed)
- A back-link to its document MOC

**Pass criteria:**
- `wrote N notes` where N ≈ (chunk count + source file count + taxonomy nodes)
- `find "$PROJECT/vault" -name "*.md" | wc -l` equals N
- Opening a section note shows real content from your transcripts (not empty)
- If TC-4 ran: `_topics/` folder exists with one `.md` per taxonomy L1/L2 node

**Fail signal:** `wrote 0 notes` → the `chunks` table is empty. TC-1 must pass first.

---

### TC summary

| Test case | Needs AWS? | Fills missing lane | Key pass signal |
|-----------|-----------|-------------------|----------------|
| TC-1 Fresh build | No | narrative (RAG) | `chunks=N`, `fts>0 vec>0` |
| TC-2 Add new file | No | — | chunk count increased |
| TC-3 Re-run unchanged | No | — | `skipped N unchanged doc(s)` |
| TC-4 Taxonomy tagging | Yes (Haiku agents) | taxonomy graph | `chunk_topics=N`, unclassified <10% |
| TC-5 Evals | Yes (Bedrock judges) | — | ≥ 90% pass rate |
| TC-6 Tabular marts | No | numbers (SQL) | `facts=N`, metric query returns rows with source_file |
| TC-7 Obsidian vault | No | — (view only) | `wrote N notes`, section notes show real content |

---

## The three answering paths

The brain has three lanes and each one answers a different kind of question. Understanding
this is important when you see retrieval fail — the failure usually means you sent the
question to the wrong lane.

| Lane | What it answers | Where the data lives | How it works |
|------|----------------|---------------------|-------------|
| **Narrative (RAG)** | "What was decided about X?" "Who said Y?" | `chunks` table — parsed, chunked transcript text | RRF hybrid: BM25 (FTS5) + vector similarity, scores fused by Reciprocal Rank Fusion |
| **Numbers (SQL)** | "How many X in June?" "What was the P95 for team Y?" | `facts` table — normalized rows from Excel workbooks | Governed SQL via `query.py`: model selects a metric + filters, SQLite computes the exact value |
| **Relations (graph)** | "Which topics appear in both X and Y?" "What decisions are linked to this risk?" | `graph_nodes` / `graph_edges` / `chunk_topics` tables | JOIN queries across taxonomy graph built by `build_graph.py` |

**Why not use RAG for everything?**

RAG cannot reliably return a number. When you ask "how many incidents in Q2?" a vector
search returns *chunks about incidents* — the answer is still in natural language and
requires the LLM to parse it. The LLM may hallucinate or average imprecisely. The SQL
lane returns the exact float from the source workbook, cited to the specific file.
This is why the `facts` table and `query.py` exist as a separate lane.

**What hybrid-retrieval does**

The `hybrid-retrieval` skill wraps all three lanes. When a question arrives, it:
1. Classifies the question type (measurement → SQL; factual/narrative → RRF; relational → graph)
2. Routes to the appropriate lane
3. Reconciles answers if multiple lanes fire
4. Composes a single cited response

The evals in TC-5 test the narrative lane specifically. The `query.py` CLI (TC-6) tests
the numbers lane directly. Both must work before hybrid-retrieval can deliver accurate
combined answers.

---

## Brain-maintenance: updating an existing brain

After the initial build (TC-1 through TC-6), the brain needs periodic updates as new
source files arrive. This is the job of the `brain-maintenance` skill.

The workflow has four phases, each gated — you do not proceed to the next unless the
current phase passes:

| Phase | What it does | Tool |
|-------|-------------|------|
| **Plan** | `maintenance.py` reads `brain-maintenance.toml`, scans sources for new/changed/missing files, writes a diff plan | `onboard.py` → `maintenance.py --plan` |
| **Apply** | Parse new files, re-index without `--reset`, write new chunks | `parse_corpus.py` + `knowledge_index.py index` (no --reset) |
| **Classify delta** | Run classification only on *new* chunks (not the whole corpus) | `classify_prep.py --unclassified-only` + agents + `classify_write.py` |
| **Verify** | Confirm chunk counts increased, taxonomy graph still healthy, smoke queries still return | `onboard.py verify` |

**When to set up brain-maintenance:**

You need `brain-maintenance.toml` when you move from "build once and validate" (this guide)
to "refresh the brain every sprint." Create it from the template:

```bash
cp $SKILLS/brain-maintenance/profile.example.toml $PROJECT/brain-maintenance.toml
```

Edit the three required fields:
```toml
[runtime]
python = "$VENV"
skills = "$SKILLS"

[project]
root = "$PROJECT"
```

Then run `maintenance.py`:
```bash
$VENV $SKILLS/brain-maintenance/maintenance.py --profile $PROJECT/brain-maintenance.toml --plan
```

It prints a read-only plan showing which files will be added, updated, or flagged for
removal. Review it, then execute each phase.

**Safety rule:** `maintenance.py` never mutates sources, parsed artifacts, or SQLite.
It only outputs a plan. The coding agent (or you, manually) executes the plan using the
component skills. This means a bad plan cannot corrupt your brain.

---

## Visual-parse: slides and diagrams (optional lane)

Some documents carry their meaning in visual layout — a process flow diagram, a milestone
timeline, a circle chart. Text extraction from these pages returns disconnected fragments:
the layout is lost. The `visual-parse` skill adds a VLM transcription step for these pages.

**When you need it:** only if your corpus contains presentation slides (PPTX/PDF) where
the key content is in shapes, arrows, or diagrams rather than bullet text. VTT/SRT files
do not need this — they are pure text.

**How it works (three steps):**

1. **Render pages to images** — `render_pages.py` converts each PPTX/PDF page to a PNG and
   flags which pages are "visual" (thin text + many vector drawings = likely a diagram).

   ```bash
   $VENV $SKILLS/visual-parse/render_pages.py \
     --doc "$SOURCES/deck.pptx" \
     --out "$PROJECT/vision/assets"
   ```
   Output: one PNG per page + `pages.json` with `flagged: true/false` per page.

2. **Prepare VLM dispatch batches** — `vision_prep.py` reads `pages.json` and creates
   structured prompt batches for the vision model (Claude Sonnet with image content).

3. **Assemble transcriptions** — `vision_assemble.py` takes the VLM responses and
   writes `.md` files into `$PROJECT/parsed/`, replacing the text-layer `.md` for flagged
   pages. These feed into `knowledge_index.py` as normal from that point.

**Pass criteria:** after running all three steps, the `.md` for a visual page contains
descriptive text ("A process flow diagram showing four stages: ...") rather than
disconnected fragments.
