---
name: tabular-semantic-layer
description: Use when you need TRUTHFUL numeric answers from large/heterogeneous Excel reporting workbooks (calls, KPIs, financials by branch/region/month) — via a config-driven ETL into a columnar store (Parquet/SQLite) and a governed metric layer, instead of RAG/LLM reading cells. Pairs with corpus-taxonomy-extraction (which supplies the dimensions & computable-metric inventory).
---

# Tabular Semantic Layer

## Overview

Answer numeric questions over big spreadsheet reporting **deterministically**. The model selects a governed metric + filters; **SQLite computes the value from the real cells** and it cites its source file. No number is ever asserted by an LLM — that is the whole point (vector RAG and "LLM reads the sheet" both hallucinate numbers; benchmarks put governed semantic layers at ~98–100% with honest-refusal failures, vs ~90% silent-wrong for raw text-to-SQL).

**Core split:** *meaning* is agentic; *numbers* are computed. This skill owns the numbers. Narrative/`stated` figures stay in the RAG/graph lane and are used only to cross-check (`both`).

## When to use

- You have structured reporting workbooks (often 10s–100s of MB, many sheets) and need exact figures, drill-down, or aggregates.
- You already have (or will derive) conformed dimensions + a computable-metric inventory — typically from `corpus-taxonomy-extraction`.
- To decide which metrics to govern next, have the user open the taxonomy review app's **Metrics** tab — you run `taxonomy_review.py plan --mode browse` and then `serve` in the background, and the user reviews in the browser — or run `taxonomy_review.py gap --taxonomy taxonomy/current.json` yourself (add `--metrics schema/metrics.<corpus>.json` when the project has more than one governed metrics file) and read the `taxonomy/work/metrics_gap.md` it writes: computable metrics induced from the corpus with no entry in `metrics.<corpus>.json`.

**Not for:** stated targets/one-off figures in prose/slides (that's the narrative lane), or when you don't yet know the dimensions/metrics (run the taxonomy skill first).

## Pipeline

```
profile   profile_workbooks.py  → structure map (sheets, headers, grains) — constant memory, never loads whole book
design    relation schema: conformed dimensions + fact marts + governed metrics  (human-ratified)
config    families.<corpus>.json → per-family {glob, grain→sheet, header, dim/measures, layout}
build     build_marts.py → normalized long facts.parquet + SQLite view `facts`
catalog   metrics.json → governed metric definitions (the contract consumed at answer time)
```

Answering questions from the marts is a separate concern — the **`hybrid-retrieval`** skill owns `query.py` and the routing/composition. This skill only *builds* the store + catalog.

### profile_workbooks.py
`python profile_workbooks.py --root <dir> --out profiles.json` — openpyxl read_only; per sheet: detected header, columns, row estimate, samples. Cheap even on 130 MB books. Grounds the schema design.

### families.<corpus>.json — corpus-specific config, lives in the PROJECT
This file and `metrics.<corpus>.json` are **project data, not part of the skill** — keep them under your repo (e.g. `<project>/schema/`), and pass their paths as args. The skill ships only generic code plus `families.example.json` / `metrics.example.json` templates to copy from.

One block per metric family: `glob`, `month_from` (`filename`|`wide_banner`|`matrix_header`), `layout` (`long`|`wide_month`|`matrix_month_cols`), `grains` (grain→{sheet, dim_header}), `measures` (canonical→raw column text, or a **list of alternative header substrings** when headers vary month to month, e.g. `["FCR % - 7 Days","7-Day FCR"]`), optional `header_row`, a `dimension_map` for explicit aliases, and `conform_titlecase` (dims like branch/region whose casing varies across families — canonicalized so they join). Adding a month = drop the file + re-run. A new family = one new config block (bounded by metrics, not files).

### build_marts.py (generic loader)
`python build_marts.py --root <reporting dir> --config <project>/schema/families.<corpus>.json --out-dir <project>/marts`
Emits long facts `(family, metric, grain, entity, month, value, source_file)` → `facts.parquet` + `knowledge.sqlite`, plus **`build_audit.json`**. Handles four layouts:
- `long` — grain sheet, dim rows, measures across columns.
- `wide_month` — unpivots month-banner column blocks (e.g. adjustments); dim label may sit on the banner row or the header row (both are searched).
- `matrix_month_cols` — metric rows × month columns (e.g. workforce MOM dashboards); time cells like ASA/AHT become seconds; uses the latest snapshot only.
- `tolerant_long` — for **template-less exports** (e.g. NPS): scans all sheets, finds the header by content (any `dim_candidate` + a measure), infers grain from the matched dim, maps measures by name wherever they sit, and cleans entity encodings via `entity_regex` (e.g. `SE1: Clinton Poche` → `SE1`).

Conforms dimension names, drops junk dim values (`#N/A`, `(blank)`…), stops at table boundaries, dedups (keep latest). Reports per-family coverage + the audit.

### metrics.json (the governed layer / contract)
`metrics.json` maps each friendly metric → `(family, metric, unit, grain)`. It is the semantic-layer definition this build emits; the **`hybrid-retrieval`** skill's `query.py` consumes it at answer time (`--metric/--grain/--entity[-like]/--month[s]`, plus `--describe` and a `--sql` escape hatch). The model's job is metric+filter selection; correctness is the engine's.

- **Provenance / `definition` (reported vs computed).** A metric may carry an optional `definition` (a.k.a. `provenance`) string stating what it measures, what's included/excluded, and whether it is **REPORTED** (a directly-ingested vendor cell) or **COMPUTED**. Surfaced by `query.py --list/--describe` and the MCP. **Reported composite metrics — occupancy, AHT, service level — must carry a `definition` and must NOT be silently reconstructed from primitives:** the layer ingests the authoritative reported value, and a naive recompute (e.g. occupancy ≈ answered×AHT÷staffed-hours) diverges because the vendor definition includes hold/ACW. Recording the definition is how an answerer tells "reported metric, don't rederive" from a data-quality bug.

## Truthfulness guarantees

- Numbers are **computed and cited**, never asserted. `query.py --sql` and every result carries `source_file`.
- Unmodeled question → empty/`(no rows)` (honest refusal), not a fabricated number.
- Values are re-derivable; validate a new mart by spot-checking cells vs. the source sheet before trusting it.

## Silent gaps are the #1 production risk — the build audit

A config is authored from a sample of a family's files; if other files in that family drift (renamed sheet, moved header row, different dim column), they **produce zero facts and vanish silently** — the store looks complete but a month/branch is missing. This is the failure mode to engineer against, not the parsing.

- Every globbed file × unit lands in **`build_audit.json`** with `status` ok/partial/zero/error + a reason. `zero` = a matched file yielded nothing; `partial` = some measures/labels unmatched.
- The console prints ⚠️ partials and ❌ zero-fact units loudly at the end of every build.
- **Run `--strict` in CI/prod**: it exits non-zero on any zero/partial unit, so a drifted file fails the pipeline instead of quietly shrinking the data.
- Benign vs real: for `wide_month` families each file repeats prior months, so a failed newer file may still be *redundant* — but it can also hide a genuinely missing latest month (e.g. a July file that won't parse while June only carries Jan–June). Triage the audit; don't assume zero = harmless.
- Once you've **confirmed** a zero-fact file is truly redundant/legacy, list a filename substring in the family's `allow_zero`: it's reclassified `benign` (ℹ️, not ❌) and no longer fails `--strict`. Use this only for files you've verified add nothing — it's an explicit acknowledgement, not a mute button.
- A family whose files have **no stable template** (ad-hoc exports) is the wrong fit for a rigid `long`/`wide_month` block — use the **`tolerant_long`** layout (content-based header/dim/measure detection + `entity_regex`). NPS is the worked example: one tolerant block ingested files that varied in sheet name, header row, dim column, and entity encoding.

### `coverage.json` — completeness the audit CANNOT see (missing-entirely)

`build_audit` checks per-file **parse health** — it only knows about files that were globbed. It **cannot** catch an entity or month for which **no file/row ever existed** (a division that stops appearing after January; a month whose source workbook was never produced/ingested). Every build now also emits **`coverage.json`** and prints:
- **grains per family** (grain visibility — see at a glance that e.g. `workforce_mom` is `overall` only, so a division-grain comparison isn't modeled).
- **⚠️ coverage holes** — an entity present in *some* of a family/grain's months but absent in others (the disappearing-division case). Detected with **no config**.
- **❌ expected-roster violations** — months/entities named in an optional top-level `coverage` config block that are wholly absent (the missing-month case). `coverage: {"<family>": {"month_range": ["2026-01","2026-07"], "entities": [...]}}` (or `"months": [...]`, or `"*"` for a default). Under **`--strict`** a violation fails the build alongside zero/partial units; intra-family holes always warn (a genuinely sparse entity may be legitimate — triage, then add an expected roster to enforce).

## Guardrails / where the real work is

- **Entity conformance is the bounded effort** (not parsing, not scale). Same entity appears in many forms across families — e.g. `Mid Atlantic`↔`Mid-Atlantic`, `CHICAGO`↔`Chicago`↔`Chicago Hod`, region codes (`NE3`)→division. Extend `dimension_map`; for branch/RSR add an alias table. Until conformed, use `--entity-like` for cross-family lookups.
- **Header offsets differ per family** (export metadata rows, month banners) — encode `header_row` in config; the loader also auto-detects the header row containing the dim + a measure.
- **Wide month-over-month layouts** (adjustments): a trailing non-date block (YTD/Total) must reset the forward-filled month, else it leaks — banner strings that aren't months set month→null.
- **Matrix dashboards** (workforce MOM): month headers repeat across several horizontal sections (monthly totals, daily-avg, weekly) — take only the **leftmost** column per month (monthly totals). A monthly snapshot already holds full history, so process only the **latest** file (loader auto-selects max month); values are the latest revision, which can differ from an earlier month's own snapshot (e.g. a prior arithmetic quirk fixed later).
- **Rollups (weighted aggregation to a coarser grain).** A config `rollups: [{family, from, to, weight, map_prefix?, crosswalk?}]` block aggregates a fine grain up to a coarse one, **weighting rate metrics by a count metric** (never a naive mean). Map the fine entity to its coarse parent one of two ways: **`map_prefix`** (region-code prefixes, `NW`→`Northwest`, when the parent is encoded in the code), or **`crosswalk`** — an explicit `{entity: parent}` lookup (inline dict, or a path to such a JSON, resolved relative to the config) for **arbitrary, non-prefixable hierarchies like `branch → division`**. Example: NPS `region → division` by prefix; `branch → division` by crosswalk, both weighted by `n_records`. Derived rows carry `source_file="<rollup>"` so they're never mistaken for source cells. Include the count metric (e.g. `n_records`) in `measures` so the weight is available.
- **Derived / ratio metrics (governed, not hand-precomputed).** A config `derived: [{family, metric, grain, numerator, denominator, scale?}]` block computes `metric = numerator / denominator` at a grain in the semantic layer, so a ratio like `calls_per_customer` is governed rather than left to whoever queries (or precomputed inconsistently per schema-author). Rows carry `source_file="<derived>"`. **Honest by construction:** operands are inner-joined on `(entity, month)`, so a missing operand yields *no* row and `denominator==0` rows are dropped — never a fabricated value. Register the derived metric in `metrics.json` with a `definition` naming its operands (pairs with the provenance field).
- **Always validate a fresh build** against known cells before answering from it.
- Deps: `openpyxl`, `pandas` (required); `pyarrow` optional (Parquet side-output). Writes the `facts` table into SQLite via stdlib `sqlite3` (no extra dep) — pass `--db <project>/schema/knowledge.sqlite` to unify with the RAG + graph lanes in one file. Torch-free.

## Downstream

The marts + `metrics.json` are consumed by the **`hybrid-retrieval`** skill, which runs deterministic queries for `computable` metrics and reconciles them with the local SQLite RAG lane for `stated`/`both` claims.
