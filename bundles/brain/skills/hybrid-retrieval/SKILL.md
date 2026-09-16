---
name: hybrid-retrieval
description: Use at ANSWER time to answer a question over one local SQLite knowledge store — routing numeric/aggregation claims to deterministic SQL (marts), narrative/qualitative claims to hybrid RAG (FTS5+vector RRF), and taxonomy/relations to graph JOINs, then composing one cited answer. Use whenever a question mixes "what happened / why" with exact figures, drill-downs, or trends. Pairs with the build skills knowledge-index, tabular-semantic-layer, corpus-taxonomy-extraction.
---

# Hybrid Retrieval

## Overview

Everything lives in **one portable `knowledge.sqlite`** (no server): `chunks/chunks_fts/chunks_vec` (RAG), `facts` (marts), `graph_nodes/graph_edges` (taxonomy). Answer by **routing each part to the lane that can answer it truthfully**, then compose one cited answer:

- **Numbers / aggregates / drill-downs / trends → deterministic SQL** over the `facts` table (`query.py`, sqlite3). Computed from real cells, cites `source_file`. A model never asserts a figure.
- **What happened / why / definitions / narrative → hybrid RAG** over `chunks` — BM25 (FTS5) + vector (sqlite-vec) fused by **RRF** (`knowledge_index.py search`, from the `knowledge-index` skill).
- **Taxonomy / categories / relations → graph JOINs** on `graph_nodes`/`graph_edges` (L1↔L2, entity kinds) via SQL / recursive CTE.
- **Both (a figure quoted *and* table-backed) → compute the authoritative value, then reconcile** against the stated one and flag any discrepancy.

This is the *retrieval* half; the store is produced by the **build** skills (`knowledge-index`, `tabular-semantic-layer`, `corpus-taxonomy-extraction`) — all writing into the same local `.sqlite`.

## When to use

- A question mixes narrative and exact numbers ("how did X change, and why").
- Any request for a specific figure, ranking, month-over-month trend, or branch/division drill-down over the reporting data.
- You want an answer where every number is verifiable and unmodeled gaps are stated, not guessed.

## Routing procedure

1. **Decompose** the question into atomic claims/sub-questions.
2. **Classify each** by the metric's `source_type` (from the metric catalog / taxonomy):
   - `computable` → marts. `stated` → RAG. `both` → marts + reconcile. Pure narrative → RAG. Category/relation → graph.
3. **Retrieve** (all against the one `<project>/schema/knowledge.sqlite`):
   - Marts: `python query.py --db knowledge.sqlite --catalog <project>/schema/metrics.<corpus>.json --metric <m> [--grain --entity|--entity-like --month|--months]`. `--list` for governed metrics; `--describe <m>` for a metric's spec + `definition`/provenance; `--sql` for aggregates/joins. A REPORTED composite metric (occupancy, AHT, service level) carries a `definition` — cite it and never reconstruct the value from primitives.
   - RAG: `python knowledge_index.py search --db knowledge.sqlite --query "..." [--k 8] [--json]` (BM25+vector RRF; returns cited chunks).
   - Graph: `--sql "SELECT … FROM graph_nodes JOIN graph_edges …"` (taxonomy L1↔L2, entity kinds; recursive CTE for multi-hop).
4. **Reconcile** `both`-class: report the computed value as authoritative; note the stated value and any gap.
5. **Compose** one answer: tag each fact `[MART: file]`, `[NARRATIVE]`, or `[STATED: doc]`.

## Truthfulness rules (non-negotiable)

- **Never state a number the marts didn't compute.** If a metric/grain/month isn't modeled, say so plainly — an honest "not modeled yet" beats a fabricated figure. (This is the whole reason the numeric lane exists.)
- **Every computed number cites its `source_file`** (query.py returns it).
- **Watch the grain.** Don't silently compare a division-grain figure with a CEC-overall one; state the grain when it matters (e.g. a division calls/customer vs. an overall abandonment rate).
- **Entity conformance:** the same entity appears in variants (`Chicago`/`CHICAGO`); use `--entity-like` for cross-family lookups until a dimension alias table exists.
- **Prefer the authoritative source for `both`:** compute; use the stated figure only to cross-check and to surface revisions/discrepancies.

## Quick reference

```bash
DB=<project>/schema/knowledge.sqlite; CAT=<project>/schema/metrics.<corpus>.json
# numbers — governed metric + filters (marts)
python query.py --db "$DB" --catalog "$CAT" --list
python query.py --db "$DB" --catalog "$CAT" --metric <metric> --grain <grain> --entity-like <name> --months 2026-06,2026-07
python query.py --db "$DB" --sql "SELECT sum(value) FROM facts WHERE family='<f>' AND metric='<m>' AND grain='<g>' AND month='2026-06'"
# narrative — hybrid RRF (BM25+vector)
python knowledge_index.py search --db "$DB" --query "why did X change" --k 8
# taxonomy graph — L2 children of an L1
python query.py --db "$DB" --sql "SELECT n.label FROM graph_nodes n JOIN graph_edges e ON e.source=n.id WHERE e.rel='subclass_of' AND e.target='billing_disputes'"
```

## Output shape

For a mixed question, structure the answer as: the computed figures (each cited), the narrative context, then the synthesized conclusion — and an explicit line for any sub-part that isn't modeled. A grading rubric of ✅ full / 🟡 stated-gap / ❌ unanswered works well; keep worked examples in the project repo, not in this skill.

## Dependencies

Stdlib `sqlite3` (query.py) + `sqlite-vec`, `fastembed` for the RAG lane (via `knowledge-index`). No server. Reads the one `knowledge.sqlite` produced by the build skills; produces nothing persistent itself.
