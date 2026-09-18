---
name: evals
description: Use when the brain is built (knowledge.sqlite exists with chunk_topics populated) and you need to generate adversarial eval suites from a VTT/SRT corpus and run them against a FastMCP brain. Two modes — extraction (verbatim quotes via Bedrock Haiku, recommended) and db-fallback (chunk_topics, smoke-test only). Produces a promptfoo pass rate across Haiku/Sonnet/Opus tiers.
---

# Evals Skill

Turns a VTT/SRT corpus + a running brain into a scored adversarial eval suite. Every eval row is grounded in real content — either a verbatim quote extracted by Haiku (extraction mode) or a source slug from `chunk_topics` (db-fallback mode). The final output is a promptfoo pass rate that tells you whether the brain retrieves and surfaces the right facts.

## When to use

Use this skill after the brain is built:
- `knowledge.sqlite` exists with `chunks`, `chunk_topics`, and `graph_nodes` populated
- The FastMCP brain server can start (or `run_e2e.sh` will start it)

To build the brain first, use the `knowledge-pipeline` skill.

---

## Prerequisite check

Before running evals, verify the brain is ready:

```bash
$VENV $SKILLS/knowledge-pipeline/onboard.py verify \
  --db "$PROJECT/schema/knowledge.sqlite" \
  --query "performance testing"
```

All lanes must be non-zero. If `chunk_topics=0`, classification has not run — go back to the brain build.

---

## Two modes — choose before you start

| Mode | When | `must_contain` ground truth | Pass rate meaning |
|---|---|---|---|
| **Extraction** (recommended) | AWS creds set, no `AWS_SESSION_TOKEN` | Verbatim quote from transcript | Brain retrieves and surfaces a specific fact |
| **DB fallback** | No AWS creds | Source filename slug (e.g. `"aug31"`) | Brain retrieves from the right source |

**Pass rates are not comparable across modes.** Extraction mode is harder (verbatim fact vs. source slug) and is the only mode that gives meaningful quality signal. Always use extraction mode for tracking. DB mode is a smoke test when AWS is unavailable.

Check your credentials before starting:

```bash
echo "Key: ${AWS_ACCESS_KEY_ID:0:8}..."          # must NOT be empty
echo "Session token: '${AWS_SESSION_TOKEN:-UNSET}'"  # must be UNSET
unset AWS_SESSION_TOKEN                           # unset if present
```

---

## Full e2e pipeline (VTT corpus → pass rate)

`run_e2e.sh` runs all 6 stages in order and pauses at Stage 3b for the manual classification agent step.

```bash
bash $SKILLS/evals/run_e2e.sh \
  --corpus   /path/to/vtt/dir \
  --work     /tmp/my_project_e2e \
  --brain-port 8003 \
  --taxonomy /path/to/taxonomy.json
```

`$SKILLS` is the installed skills directory (e.g. `.claude/skills` in the repo, or `~/.claude/skills` with `--user`).

**Flags:**

| Flag | Required | Default |
|---|---|---|
| `--corpus` | yes | — |
| `--work` | yes | — |
| `--brain-port` | no | 8003 |
| `--taxonomy` | no | `$SKILLS/evals/taxonomy.default.json` |
| `--extractions` | no | auto-generated at Stage 3.5 |

---

## Stage-by-stage reference

### Stage 1 — Parse VTT corpus

```bash
$VENV $SKILL_TAXO/parse_corpus.py \
  --corpus     "$CORPUS" \
  --out        "$PARSED" \
  --formats    vtt,srt \
  --merge-cues 10
```

> **`--merge-cues 10` is mandatory.** Without it every cue becomes its own chunk (~79 chars). Classification agents return `[]` for nearly all of them and retrieval collapses. The eval pipeline will produce near-0% pass rate on a corpus parsed without merging.

**Check:** `ls $WORK/parsed/*.md | wc -l` equals your source file count.

---

### Stage 2 — Index into knowledge.sqlite

```bash
$VENV $SKILL_KI/knowledge_index.py index \
  --db     "$DB" \
  --corpus "$PARSED" \
  --reset
```

With `--merge-cues 10`, expect ~2,500–4,000 chunks for a typical VTT corpus (not 25k).

---

### Stage 3 — Classify chunks (three sub-steps)

**3a — Prep batches:**

```bash
$VENV $SKILL_TAXO/classify_prep.py \
  --db       "$DB" \
  --taxonomy "$TAXONOMY" \
  --out      "$CLASSIFY_DIR" \
  --batches  25
```

`--batches 25` keeps each agent under ~1,000 chunks (~32k input tokens). Too few batches → agent hits context limit and writes nothing.

**Output directory:** `result_*.json` files land in the same `$CLASSIFY_DIR` as `batch_*.json` — not a `results/` subdirectory.

**3b — Dispatch agents (manual step):**

For each batch N (0–24), open a Claude Code session and paste:

```
Read $WORK/classify/instructions.md and $WORK/classify/vocab.md.
Then read $WORK/classify/batch_N.json.
For each chunk: pick 0–3 categories from vocab.md following instructions.md.
Write result_N.json to $WORK/classify/.
No code. No questions. Just classify and write the file.
```

Monitor progress: `ls $WORK/classify/result_*.json | wc -l` — wait until it equals batch count.

**3c — Write results + build graph:**

```bash
$VENV $SKILL_TAXO/classify_write.py --db "$DB" --results "$CLASSIFY_DIR"
$VENV $SKILL_TAXO/build_graph.py --taxonomy "$TAXONOMY" --db "$DB"
```

**Check:** `chunk_topics` and `graph_nodes` non-zero in verify output. `unclassified` < 10%.

---

### Stage 3.5 — Extract verbatim facts (extraction mode only)

```bash
$VENV $SKILL_EVALS/extract_facts.py \
  --parsed   "$PARSED" \
  --taxonomy "$TAXONOMY" \
  --out      "$WORK/extractions"
```

Sends the first 8,000 chars of each `.md` to Bedrock Haiku. Writes one `*_extraction.json` per file.

**Check:** `ls $WORK/extractions/*_extraction.json | wc -l` > 0.

**Failure signals:**
- `AccessDeniedException` → key lacks Bedrock access or wrong region. Try `AWS_REGION=us-west-2`.
- `no facts extracted — skipping` → the `.md` is mostly boilerplate. Open the file and check.
- Script fails entirely → falls back to DB mode automatically in `run_e2e.sh`.

---

### Stage 4 — Start brain server

`run_e2e.sh` starts the FastMCP shim at `http://localhost:$PORT`. The health check accepts HTTP 200 or 503 (degraded-but-running). If health never returns, the brain process died — check `$WORK/knowledge.sqlite` is readable and `sqlite-vec` is installed in the venv.

---

### Stage 5 — Generate evals

**Extraction mode** (when `$WORK/extractions/` has files):

```bash
$VENV $SKILL_EVALS/generate_evals.py \
  --extractions "$WORK/extractions" \
  --taxonomy    "$TAXONOMY" \
  --out         "$WORK/evals.csv"
```

**DB fallback** (when no extractions):

```bash
$VENV $SKILL_EVALS/generate_evals.py \
  --db       "$DB" \
  --taxonomy "$TAXONOMY" \
  --out      "$WORK/evals.csv"
```

Then generate the promptfoo YAML:

```bash
$VENV $SKILL_EVALS/generate_promptfoo.py \
  --csv        "$WORK/evals.csv" \
  --out        "$WORK/promptfooconfig.yaml" \
  --brain-url  "http://localhost:$PORT" \
  --context-js "$SKILL_EVALS/load_brain_context.js" \
  --taxonomy   "$TAXONOMY"
```

---

### Stage 6 — Run evals

```bash
cd "$WORK" && AWS_REGION=us-east-1 \
  npx promptfoo@0.123.0 eval \
    --config promptfooconfig.yaml \
    --no-cache \
    --max-concurrency 3 \
    --output results.json
```

View results: `npx promptfoo@0.123.0 view`

---

## Pass rate interpretation

| Score | Meaning |
|---|---|
| < 50% | Retrieval or chunking is broken — check `--merge-cues` was used |
| 50–75% | Retrieval works but rubrics may be too strict, or brain has genuine gaps |
| 75–90% | Good signal; investigate individual failures before claiming done |
| ≥ 90% | Target for production readiness (extraction mode only) |

For each failure, check three things:
1. **Retrieved context** — was the relevant chunk actually in the top-k?
2. **Brain answer** — did it have the right content but phrased differently?
3. **Rubric** — is `must_contain` too strict (exact quote where paraphrase is fine)?

---

## Incremental use (brain already built)

If `knowledge.sqlite` already has `chunk_topics` populated, skip Stages 1–3 and run from Stage 3.5:

```bash
# Extract facts from existing parsed dir
$VENV $SKILL_EVALS/extract_facts.py \
  --parsed   "$PROJECT/parsed" \
  --taxonomy "$TAXO" \
  --out      "$PROJECT/extractions"

# Generate evals
$VENV $SKILL_EVALS/generate_evals.py \
  --extractions "$PROJECT/extractions" \
  --taxonomy    "$TAXO" \
  --out         "$PROJECT/evals.csv"

# Generate YAML + run (start brain server first)
$VENV $SKILL_EVALS/generate_promptfoo.py \
  --csv "$PROJECT/evals.csv" --out "$PROJECT/promptfooconfig.yaml" \
  --brain-url http://localhost:9100 \
  --context-js $SKILL_EVALS/load_brain_context.js \
  --taxonomy "$TAXO"

bash $SKILL_EVALS/run_brain_eval.sh \
  --db "$PROJECT/schema/knowledge.sqlite" \
  --config "$PROJECT/promptfooconfig.yaml" \
  --port 9100
```

---

## Files

| File | Purpose |
|---|---|
| `run_e2e.sh` | Full pipeline: parse → index → classify → extract → eval |
| `extract_facts.py` | Bedrock Haiku → verbatim `*_extraction.json` per parsed `.md` |
| `generate_evals.py` | `*_extraction.json` or `chunk_topics` → adversarial `evals.csv` |
| `generate_promptfoo.py` | `evals.csv` + taxonomy persona → `promptfooconfig.yaml` |
| `run_brain_eval.sh` | Start FastMCP shim + run promptfoo (incremental use) |
| `load_brain_context.js` | Dynamic context fetcher for promptfoo (dual-query, dedup) |
| `taxonomy.default.json` | Default taxonomy when no project taxonomy is provided |

## Deps

`fastembed`, `sqlite-vec` (brain shim); AWS Bedrock access for extraction mode (`boto3`, `AWS_ACCESS_KEY_ID`, no `AWS_SESSION_TOKEN`); `node` + `npx` for promptfoo. Install into the skills venv (`install.sh --deps`).
