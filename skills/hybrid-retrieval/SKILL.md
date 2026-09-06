---
name: hybrid-retrieval
description: Use at ANSWER time to answer a question over a Cognee knowledge brain + a DuckDB metric mart — routing numeric/aggregation claims to deterministic SQL and narrative/qualitative claims to RAG, reconciling where both exist, and composing one cited answer. Use whenever a question mixes "what happened / why" with exact figures, drill-downs, or trends. Pairs with the build skills corpus-taxonomy-extraction and tabular-semantic-layer.
---

# Hybrid Retrieval

## Overview

Answer a question by **routing each part to the lane that can answer it truthfully**, then composing one cited answer:

- **Numbers / aggregates / drill-downs / trends → deterministic SQL** over the marts (`query.py` on DuckDB). The value is computed from real cells and cites its `source_file`. A model never asserts a figure.
- **What happened / why / definitions / narrative → RAG** over the Cognee brain. **Prefer the MCP `recall` tool** (`mcp__cognee__recall`) when the runtime has the Cognee MCP server wired; fall back to REST `/api/v1/search` otherwise (see the `cognee` skill).
- **Both (a figure that is quoted *and* table-backed) → compute the authoritative value, then reconcile** against the stated one and flag any discrepancy.

This is the *retrieval* half. The marts, metric catalog, and taxonomy are produced by the **build** skills (`tabular-semantic-layer`, `corpus-taxonomy-extraction`); this skill only consumes them.

## When to use

- A question mixes narrative and exact numbers ("how did X change, and why").
- Any request for a specific figure, ranking, month-over-month trend, or branch/division drill-down over the reporting data.
- You want an answer where every number is verifiable and unmodeled gaps are stated, not guessed.

## Routing procedure

1. **Decompose** the question into atomic claims/sub-questions.
2. **Classify each** by the metric's `source_type` (from the metric catalog / taxonomy):
   - `computable` → marts. `stated` → Cognee. `both` → marts + reconcile. Pure narrative → Cognee.
3. **Retrieve**:
   - Marts: `python query.py --db <project>/marts/marts.duckdb --catalog <project>/schema/metrics.<corpus>.json --metric <m> [--grain --entity|--entity-like --month|--months]`. The db + catalog are project artifacts from the build skill, not shipped here. Use `--list` to see governed metrics; `--sql` for aggregates/joins the catalog doesn't cover.
   - Cognee: **first choice — `mcp__cognee__recall`** (auto-routing, session-aware) if the MCP server is wired. **Fallback — REST `/api/v1/search`** with an explicit `searchType` (`GRAPH_COMPLETION`/`HYBRID_COMPLETION`); use REST specifically when you need to pin the search type, page results, or the MCP server isn't available. See the `cognee` skill for both.
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
DB=<project>/marts/marts.duckdb; CAT=<project>/schema/metrics.<corpus>.json
# list governed metrics
python query.py --db "$DB" --catalog "$CAT" --list
# a trend at a grain
python query.py --db "$DB" --catalog "$CAT" --metric <metric> --grain <grain> --entity <NAME> --months 2026-06,2026-07
# a named entity, case-insensitive (handles casing variants)
python query.py --db "$DB" --catalog "$CAT" --metric <metric> --grain <grain> --entity-like <name>
# aggregate the catalog doesn't name
python query.py --db "$DB" --sql "SELECT sum(value) FROM facts WHERE family='<family>' AND metric='<metric>' AND grain='<grain>' AND month='2026-06'"
```

## Output shape

For a mixed question, structure the answer as: the computed figures (each cited), the narrative context, then the synthesized conclusion — and an explicit line for any sub-part that isn't modeled. A grading rubric of ✅ full / 🟡 stated-gap / ❌ unanswered works well; keep worked examples in the project repo, not in this skill.

## Dependencies

`duckdb` (query.py). The Cognee lane needs a running Cognee server reached via its MCP tools (`mcp__cognee__*`) or REST — see the `cognee` skill. Reads artifacts produced by the build skills; produces nothing persistent itself.
