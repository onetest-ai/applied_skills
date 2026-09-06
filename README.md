# Applied AI Skills

A distributable collection of **generic, corpus-agnostic** agent skills for building truthful data pipelines over documents, knowledge graphs, and tabular reporting. Each skill is self-contained code + docs; **no project-specific data, paths, or credentials live here** — those stay in the consuming project.

## The pipeline

These skills compose into a two-lane pattern: **meaning is agentic (RAG/graph); numbers are computed (deterministic SQL).** RAG never produces figures; the mart lane never guesses.

```
documents ─▶ corpus-taxonomy-extraction ─▶ taxonomy (intent classes, entities, metric inventory)
                                              │
   narrative ──▶ cognee (knowledge graph / RAG) ─────────────┐
                                              │               ├─▶ hybrid-retrieval ─▶ cited answer
   reporting xlsx ─▶ tabular-semantic-layer (Parquet/DuckDB) ─┘
```

## Skills

| Skill | Role | Key idea |
|---|---|---|
| **corpus-taxonomy-extraction** | build | Goal-directed, low-tier-model induction of a starting taxonomy (intent classes + entities + metric inventory) from a heterogeneous corpus (PDF/PPTX/XLSX via Docling/pypdf). Map → reduce → judge → emit, with provenance. |
| **cognee** | access | Connection-agnostic REST access to a Cognee knowledge-graph server — auth, search/recall (RAG), inspect brains, add/cognify/forget. Full API reference included. |
| **tabular-semantic-layer** | build | Config-driven ETL of large/heterogeneous Excel reporting → normalized Parquet + DuckDB + a governed metric catalog. Four layouts (long / wide-month / matrix / tolerant), weighted rollups, and a build audit that makes silent gaps loud (`--strict` for CI). |
| **hybrid-retrieval** | answer | Routes each sub-question to the truthful lane (numbers → DuckDB, narrative → Cognee), reconciles `both`-class figures, composes one cited answer. |

## Why this exists

Vector RAG cannot return correct numbers; raw text-to-SQL returns *confident wrong* numbers. A governed semantic layer over deterministic SQL benchmarks far higher and fails by honest refusal. These skills implement that split, plus the operational guardrails (provenance, entity conformance, silent-gap auditing) that make it hold up in production.

## Install

Each project points its agent runtime at these skills. For local use, symlink the ones you want into your skills directory, e.g.:

```bash
ln -s "$PWD/skills/tabular-semantic-layer" ~/.claude/skills/tabular-semantic-layer
```

Corpus-specific configuration (family definitions, metric catalogs, Cognee connection + dataset ids) belongs in the **consuming project's** repo, not here. Each skill's `*.example.*` templates show the shape to copy.

## Dependencies

Python 3.9+. Per-skill: `docling`, `pypdf` (extraction); `openpyxl`, `pandas`, `pyarrow`, `duckdb` (tabular); a running Cognee server (cognee). All pip-installable and torch-free.
