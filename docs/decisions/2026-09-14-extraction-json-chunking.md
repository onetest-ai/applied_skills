# ADR: Extraction JSON to Headed Markdown Serialization Step

**Date:** 2026-09-14
**Status:** ACCEPTED — bug fixed; `extraction_to_md.py` patch pending upstream commit
**Author:** Karen Florykian

---

## 1. Context

The **Philips Brain** pipeline uses the `knowledge-index` skill
(`applied_skills/skills/knowledge-index/`) to build a hybrid retrieval
index — BM25 (FTS5) + sqlite-vec — stored in a single portable
`knowledge.sqlite` file. The FastMCP server (`applied_skills/skills/knowledge-index/knowledge_index.py`)
exposes this store over MCP as three tools: `search_knowledge`,
`get_evidence`, and `health`.

The `knowledge-index` skill is fed by `vtt-transcript-etl`, which
produces per-session extraction artifacts named `{sha8}_extraction.json`
following schema_version 1.0. These JSONs contain structured records
(verbatim quotes, categories, products, SDLC phase metadata).

The Cowork `philips-brain-cowork` plugin calls `search_knowledge(query,
limit=5..15)` over MCP and renders cited results directly in the Cowork
UI.

---

## 2. Problem Statement

### Symptom observed

Every call to `search_knowledge` in Cowork returned:

```
Error: result (309,237 characters) exceeds maximum allowed tokens
```

Confirmed at `limit=15`: response was 309,237 chars. At `limit=5` the
response was ~100K chars — still hitting the MCP output cap on every
call.

### Root cause: contract mismatch between two skills

**`knowledge-index/SKILL.md` line 22** states explicitly:

> "Input = Markdown. Point it at parser output… No code→text layer needed — the input is already text."

The skill's `corpus_docs()` function in `knowledge_index.py` line 181–183
enforces this contract at the filesystem level by globbing only `**/*.md`:

```python
def corpus_docs(corpus):
    return sorted(f for f in glob.glob(
        os.path.join(corpus, "**", "*.md"), recursive=True))
```

The pipeline step that was missing: **serialize extraction JSONs to
headed Markdown before indexing**.

Instead, extraction JSONs were renamed from `.json` to `.md` and fed
directly to `knowledge_index.py`. The corpus_docs glob accepted the
renamed files (they had the right extension) but the content was raw
JSON — a single object with no blank lines between fields.

### Why this produces one chunk per file

**File:** `applied_skills/skills/knowledge-index/chunking.py` — `sections()` function, line 27.

When input Markdown has no `##` headings, `sections()` falls back to
paragraph-merge. A raw JSON object has no blank lines separating
"paragraphs", so the entire file is treated as one paragraph —
producing **one chunk per file**.

### Evidence

| Metric | Before fix | After fix |
|--------|-----------|-----------|
| Source files | 26 | 26 |
| Chunks indexed | 26 | 947 |
| Avg chunk size | 20,394 chars | ~300 chars |
| Semantic kNN edges | 111 | 4,298 |
| `search_knowledge(limit=5)` response | ~100K chars | ~2–5K chars |
| `search_knowledge(limit=15)` response | 309,237 chars | <10K chars |
| MCP cap hit | Every call | Never |

---

## 3. Decision

Add `extraction_to_md.py` to `applied_skills/skills/knowledge-index/`
as a **documented, required pipeline step** between `vtt-transcript-etl`
output and `knowledge_index.py index`.

This step serializes each `{sha8}_extraction.json` file into headed
Markdown using one `##` heading per extraction record. This gives the
`sections()` chunker (line 27 of `chunking.py`) the structural anchors
it needs to produce one chunk per record rather than one chunk per file.

**Rationale for this approach over alternatives:**

- Modifying `chunking.py` to handle JSON input would violate the
  `knowledge-index` skill's stated contract and would couple it to the
  `vtt-transcript-etl` schema. The skill is designed to be
  corpus-agnostic.
- Modifying `corpus_docs()` to also accept `.json` would silently allow
  other malformed inputs and would still leave the chunker unable to
  split within a flat JSON object.
- The serialization step is the correct architectural boundary: it is
  the responsibility of the pipeline assembler to present the
  skill-required input format, not the skill's responsibility to handle
  every upstream format.

---

## 4. Data Flow

### Before fix

```
vtt-transcript-etl → {sha8}_extraction.json
                           |
                           v  (renamed to .md — WRONG)
knowledge_index.py index   →  1 chunk per file (~20K chars)
                           |
                           v
FastMCP search_knowledge   →  MCP cap hit every query
```

### After fix

```
vtt-transcript-etl → {sha8}_extraction.json
                           |
                           v
extraction_to_md.py        →  headed Markdown (# Session / ## Category — Product per record)
                           |
                           v
knowledge_index.py index   →  ~36 chunks per file (~300 chars avg)
                           |
                           v
FastMCP search_knowledge   →  normal payload, < 5K per hit
```

---

## 5. Markdown Format Produced

`extraction_to_md.py` produces one Markdown file per extraction JSON,
with this structure:

```markdown
# {title}

**Session:** {session_date}  **Slug:** {file_slug}
**Products:** {products}  **Phases:** {sdlc_phases}

## {category} — {product} (record N)

> {verbatim_quote}

{context}

sdlc_phase: {sdlc_phase}  priority: {priority}
```

This gives the `sections()` chunker one `##` heading per extraction
record, yielding approximately 1 chunk per record. With ~36 records per
session transcript, the result is ~36 chunks per source file (vs 1 before).

---

## 6. Consequences

### What changes

1. `applied_skills/skills/knowledge-index/extraction_to_md.py` — new file (the serializer)
2. `applied_skills/skills/knowledge-index/SKILL.md` — add pipeline step documentation

### What stays the same

- `chunking.py` — unchanged; the fix respects its existing contract
- `knowledge_index.py` — unchanged; the fix respects its existing contract
- `vtt-transcript-etl` output format — unchanged
- FastMCP server — unchanged
- MCP tool signatures (`search_knowledge`, `get_evidence`, `health`) — unchanged
- Cowork plugin — unchanged

### Operational impact

After re-running the pipeline with the serialization step:
- `search_knowledge` results are focused, cited, and well within the MCP output cap
- kNN graph density increases ~39× (111 → 4,298 edges), improving `related` lookups
- Index rebuild time increases proportionally to chunk count (still sub-minute for 26 files)

---

## 7. Patch Plan

### Files to create

**`applied_skills/skills/knowledge-index/extraction_to_md.py`**

Serializes `{sha8}_extraction.json` files (vtt-transcript-etl schema_version 1.0)
into headed Markdown with one `##` heading per extraction record. Source
of truth for the format is the ad-hoc prototype at
`~/projects/kt-docs/brain/serialize_extractions.py` (session artifact,
not tracked; do not commit that path).

CLI:

```bash
python applied_skills/skills/knowledge-index/extraction_to_md.py \
  --input <extractions_dir> \
  --out   <parsed_dir>
```

### Files to modify

**`applied_skills/skills/knowledge-index/SKILL.md`**

Add a new section after the existing "Input = Markdown" note (line 22):

```markdown
### Pipeline step: serializing `vtt-transcript-etl` extractions

If your corpus contains `{sha8}_extraction.json` files produced by
`vtt-transcript-etl`, run `extraction_to_md.py` before indexing:

```bash
python skills/knowledge-index/extraction_to_md.py \
  --input <extractions_dir> \
  --out   <parsed_dir>
```

This converts each structured extraction JSON into headed Markdown with
one `## Category — Product` heading per record, which gives the chunker
the structural anchors it needs to produce one chunk per record (~300
chars) rather than one chunk per file (~20K chars).
```

---

## 8. Re-run Checklist

After committing the patch:

```bash
# Step 1: Serialize extraction JSONs to headed Markdown
python applied_skills/skills/knowledge-index/extraction_to_md.py \
  --input ~/projects/kt-docs/brain/parsed/orig/ \
  --out   ~/projects/kt-docs/brain/parsed/

# Step 2: Re-index (--reset drops and rebuilds all tables)
uv run --with sqlite-vec --with fastembed \
  python applied_skills/skills/knowledge-index/knowledge_index.py index \
  --db ~/projects/kt-docs/brain/knowledge.sqlite \
  --corpus ~/projects/kt-docs/brain/parsed/ --reset

# Step 3: Rebuild kNN semantic graph
uv run --with sqlite-vec --with fastembed \
  python applied_skills/skills/knowledge-index/knowledge_index.py related \
  --db ~/projects/kt-docs/brain/knowledge.sqlite
```

**Expected results after re-run:**

- Chunks: ~940+ (was 26)
- Avg chunk size: ~300 chars (was 20,394 chars)
- kNN edges: ~4,000+ (was 111)
- `search_knowledge(limit=5)` response: 2–5K chars (was ~100K chars)

**Verification:**

```bash
# Quick chunk count check
uv run --with sqlite-vec python -c "
import sqlite3
db = sqlite3.connect('/Users/Karen_Florykian/projects/kt-docs/brain/knowledge.sqlite')
print('chunks:', db.execute('SELECT count(*) FROM chunks').fetchone()[0])
print('edges:', db.execute('SELECT count(*) FROM chunks_related').fetchone()[0])
"
```

---

## 9. Related Files

| File | Role | Notes |
|------|------|-------|
| `applied_skills/skills/knowledge-index/chunking.py:27` | `sections()` function — the chunker | Falls back to paragraph-merge when no `##` headings present |
| `applied_skills/skills/knowledge-index/knowledge_index.py:181–183` | `corpus_docs()` — globs `**/*.md` | Enforces the Markdown input contract at the filesystem level |
| `applied_skills/skills/knowledge-index/SKILL.md:22` | "Input = Markdown" contract statement | Must be updated with the serialization step |
| `applied_skills/skills/knowledge-index/extraction_to_md.py` | **New file** — the serializer | Does not yet exist; must be created from the session prototype |
| `~/projects/kt-docs/brain/serialize_extractions.py` | Session prototype — ad-hoc, untracked | Source of truth for the Markdown format; do not commit this path; use `extraction_to_md.py` instead |
| `~/projects/kt-docs/brain/knowledge.sqlite` | The live index | Must be rebuilt (--reset) after serialization |
| `~/projects/kt-docs/brain/parsed/` | Markdown corpus fed to the indexer | Output directory for `extraction_to_md.py` |
| `~/projects/kt-docs/brain/parsed/orig/` | Original extraction JSONs | Input directory for `extraction_to_md.py` |
| `applied_skills/skills/philips-brain-cowork/` | Cowork plugin that calls `search_knowledge` | Unchanged; was hitting MCP cap due to oversized results |

---

## 10. Security Notes

- No credentials or tokens are involved in this fix
- `carrier-tasks` lambdas are untouched
- `knowledge.sqlite` is a local file; it is not uploaded to Carrier or any remote store
- The Cowork plugin communicates with the FastMCP server on localhost only (127.0.0.1:8002)
