## Pipeline Consistency Analysis: VTT/SRT Integration ⚠️

This PR successfully adds VTT/SRT transcript support to the corpus processing pipeline. The new **Stage 3.5 extraction** (Bedrock Haiku → `*_extraction.json`) and **fact-driven evals** are well-designed. However, there are **seven consistency gaps** between this new path and the existing PDF/XLSX workflow that should be documented or addressed before production use.

### Context

The pipeline now has **two data paths** for taxonomy alignment:

```
Path A (VTT/SRT — NEW):
  Parse VTT/SRT → Markdown
              → extract_facts.py (Bedrock Haiku) → *_extraction.json
              → generate_evals.py (extraction mode) → evals.csv

Path B (PDF/XLSX — EXISTING):
  Parse PDF/XLSX → Markdown
              → knowledge_index.py + classify_prep.py (Claude agents) → chunk_topics table
              → generate_evals.py (--db mode) → evals.csv
```

Both paths feed `generate_evals.py`, but use different schemas and grading logic. Here are the gaps:

---

### 🔴 High Priority

#### **1. Extraction JSON vs. chunk_topics Schema Divergence**

**Status**: VTT/SRT create `*_extraction.json` files; PDF/XLSX pipeline unclear.

Looking at the code, `extract_facts.py` produces:
```json
{
  "file_slug": "meeting_slug",
  "extractions": [
    {"category": "ActionItem", "verbatim_quote": "..."}
  ]
}
```

But `generate_evals.py --db` reads directly from SQLite `chunk_topics` table. **Question**: Do PDF/XLSX workflows also produce `*_extraction.json` files, or do they go directly to the DB?

**Risk**: If they diverge, two teams maintaining separate paths for the same semantic problem (fact → category mapping).

**Recommendation**: 
- [ ] Document which corpus types produce extraction JSONs vs. direct DB writes
- [ ] If mixed, add a shim to convert chunk_topics rows to extraction JSON schema for consistency

---

#### **2. LLM Extraction (Haiku) vs. Taxonomy Classification (Claude Agents)**

**Status**: Two different LLMs doing similar work via different processes.

- **VTT/SRT**: Bedrock Haiku auto-extracts facts from MD (fully automated, no human review)
- **PDF/XLSX**: Claude agents classify chunks in Stage 3 (requires manual approval via `run_e2e.sh` pause at 3b)

**Risk**: 
- No validation that Haiku-extracted categories match Claude agent categories
- If Haiku performs poorly on certain categories, evals silently degrade
- No fallback if extraction fails (unlike manual review for PDF/XLSX)

**Recommendation**:
- [ ] Add optional `--review-extractions` mode to `run_e2e.sh` that surfaces Haiku results before Stage 4
- [ ] Log extraction confidence or audit trail (which facts, which categories)
- [ ] Compare category distribution: `extracted_categories.json` vs. `chunk_topics counts` — warn if diverge >20%

---

### 🟡 Medium Priority

#### **3. Query Suffix Derivation Strategy Differs**

**Status**: Two approaches produce different retrieval queries.

Extraction mode:
```python
suffix = _query_suffix_from_fact(verbatim_quote)  
# "availability calendar scheduling" from the actual fact
```

DB mode fallback:
```python
query_suffix = eval_config.get("query_suffix", DEFAULT_QUERY_SUFFIX)
# "owners decisions commitments" from taxonomy
```

**Risk**: Brain may retrieve different chunks depending on which path was used, making evals non-comparable.

**Recommendation**:
- [ ] In DB-mode `generate_evals.py`, derive query_suffix from first N chunks for the category (hybrid approach) instead of pure taxonomy defaults
- [ ] Document in `skills/evals/SKILL.md` which mode is "canonical" for your project

---

#### **4. No Extraction Result Caching or Idempotency**

**Status**: `extract_facts.py` calls Bedrock Haiku for every MD file on every `run_e2e.sh` invocation.

```python
# extract_facts.py has no cache check
facts = extract_facts_from_md(md_text, taxonomy_l1, llm_fn)
```

**Risk**:
- Re-running on same corpus can produce *different* facts (LLM non-determinism)
- Evals may diverge between runs without code changes
- High AWS cost on large corpora

**Recommendation**:
- [ ] Add content-hash caching: store SHA256(md_text) alongside each `*_extraction.json`
- [ ] Skip re-extraction if hash matches; warn if content changed (legitimate re-extraction)
- [ ] Document cost implications in onboarding docs

---

#### **5. Fact Metadata Completeness Varies**

**Status**: VTT/SRT captures speaker and event_date; PDF/XLSX may not.

VTT/SRT enriches chunks with:
```python
speaker = "Alice"           # extracted from HTML tags or "Name: text"
event_date = "2025-09-21"   # from filename or content regex
breadcrumb_path = "Architecture > Transport"  # heading hierarchy
```

PDF/XLSX extraction likely captures:
```python
category, context, owner(?), product(?)
# But no speaker or temporal context
```

**Risk**: Evals referencing speaker context won't work for PDF facts. Mixed metadata makes rubric grading inconsistent.

**Recommendation**:
- [ ] Add schema validation in `extract_facts_from_md()`: warn if source/category/verbatim_quote missing
- [ ] Backfill speaker/event_date from PDF/XLSX metadata (if available) during chunking
- [ ] Document which metadata fields are "required" vs. "optional" in eval rubrics

---

#### **6. Breadcrumb Hierarchy Not Applied to PDF/XLSX**

**Status**: New breadcrumb tracking is in `chunking.py`, but PDF/Office paths may not produce equivalent structure.

```python
# NEW (chunking.py):
breadcrumb = " > ".join(["Architecture", "FastMCP", "Transport"])
parent_heading = "FastMCP"

# Does parse_pdf_pymupdf() and parse_xlsx_structure() preserve this?
```

**Risk**: VTT facts have rich hierarchical context for filtering; PDF facts lose structure.

**Recommendation**:
- [ ] Verify PDF heading levels → breadcrumb conversion in `parse_pdf_pymupdf()`
- [ ] For XLSX, reconstruct breadcrumb from sheet name + row grouping (if applicable)
- [ ] Add test: `test_breadcrumb_consistency_across_formats()` confirming all corpus types populate `breadcrumb_path`

---

### 🟢 Low Priority (Documentation)

#### **7. Two-Path Pipeline Design Not Documented**

**Status**: `skills/evals/SKILL.md` and `run_e2e.sh` don't explain why VTT/SRT ≠ PDF/XLSX.

**Recommendation**:
- [ ] Add section to `SKILL.md`:
  ```markdown
  ## Data Flow by Corpus Type
  
  | Corpus Type | Parsing | Extraction | Indexing | Taxonomy | Eval Mode |
  |---|---|---|---|---|---|
  | VTT/SRT | parse_corpus.py | extract_facts.py (Haiku) | knowledge_index.py | Haiku-tagged | fact-derived |
  | PDF/PPTX/DOCX | parse_office_pymupdf() | classify_prep.py (Claude) | knowledge_index.py | Claude-tagged | hybrid (extraction + DB) |
  | XLSX | parse_xlsx_structure() | classify_prep.py (Claude) | knowledge_index.py | Claude-tagged | hybrid (extraction + DB) |
  ```
- [ ] Explain rationale: Why Haiku for VTT but Claude for PDF? (Speed vs. accuracy? Cost?)

---

### Summary Checklist

| Concern | Priority | Suggested Action |
|---------|----------|---|
| Extraction JSON vs. chunk_topics divergence | 🔴 | Document schema; add shim if needed |
| Haiku vs. Claude comparison | 🔴 | Add extraction review + category distribution check |
| Query suffix derivation differs | 🟡 | Use hybrid approach in DB mode |
| No extraction caching | 🟡 | Add content-hash caching layer |
| Metadata completeness varies | 🟡 | Add schema validation; backfill PDF/XLSX metadata |
| Breadcrumb not in PDF/XLSX | 🟡 | Add hierarchy conversion for all formats |
| Two-path design undocumented | 🟢 | Add data-flow table to SKILL.md |

---

### Positive Notes ✅

- **Fact-driven eval design** is sound: verbatim facts as ground truth, keyword derivation, N-1 rubric all follow best practices
- **46/46 tests passing** with TDD discipline — RED phase enforced
- **Graceful fallback**: `--db` mode works without AWS creds
- **Speaker/breadcrumb extraction** is a valuable addition for future filtering and context
- **Incremental indexing** (docs cached by hash) prevents wasteful re-embedding

---

**Recommendation**: Merge with these seven gaps documented as follow-up issues. The evals improvement (86% → 94.67% pass rate) is too valuable to block, and these gaps are *consistency* issues rather than *correctness* bugs.
