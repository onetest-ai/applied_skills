---
name: evals-remote
description: Use when you need to evaluate a REMOTE, key-authenticated Brain (MCP over HTTP) — reconcile its sources against a corpus inventory export to gate out out-of-scope data (including superseded documents), then score answers for accuracy, completeness and consistency, diffed run to run. Config-driven; works for any Brain built on this codebase.
---

# Evals-Remote Skill

Evaluates a **deployed** Brain at a remote MCP endpoint authenticated by a static key header (or a
local keyless one). Reuses the existing `evals/generate_promptfoo.py` pipeline unchanged; adds a
corpus↔brain baseline scope gate, a coverage report, and a run-to-run consistency diff. **Nothing about
any one brain is hardcoded** — every project specific (endpoint, key, corpus inventory, scope policy,
domain vocabulary) lives in a config JSON supplied by the consuming project.

Full methodology + trigger rules: **`docs/evals-methodology.md`** (living doc template; copy one per brain).

## Config

Copy `evals.config.example.json`, fill it in, keep it **outside the repo**, and point `EVALS_CONFIG`
(or `--config`) at it:

```json
{
  "brain":   { "url": "https://host/brain/mcp", "key_env": "BRAIN_API_KEY", "key_header": "X-API-Key" },
  "corpus":  { "inventory_xlsx": "inventory.xlsx", "inventory_sheet": "Query",
               "narrative_exts": [".pdf",".pptx",".docx",".vtt"], "tabular_exts": [".xlsx",".xlsm"],
               "seed_terms": [] },
  "scope":   { "provenance_cutoff": "2026-06",
               "exclude_patterns": ["prior .*engagement"], "keep_patterns": ["monthly", "survey"],
               "junk_patterns": ["tobedeleted","~\\$","\\bcopy\\b"] },
  "context": { "metric_synonyms": { "<acronym>": "<word in the metric's name or description>" } }
}
```

The scope policy is *data*, not code: `exclude_patterns` fail the baseline (out-of-scope / prior
engagement), `keep_patterns` mark current-state data, `provenance_cutoff` fails pre-cutoff project
artifacts. Patterns match the source's **whole folder path** plus its file name.
`corpus.seed_terms` adds sweep queries (the brain's own taxonomy and metric names are always used);
`context.metric_synonyms` lets a question's acronym pull in the matching governed metric.

## Prerequisites

- `export BRAIN_API_KEY=…` for a remote brain (env name per `config.brain.key_env`; header per
  `config.brain.key_header`, default `X-API-Key`). Omit it for a local keyless brain.
- `export EVALS_CONFIG=path/to/config.json` · `export EVALS_PY=$(pwd)/.claude/venv/bin/python` (venv with fastmcp).
- `node`+`npx` for promptfoo; valid AWS Bedrock creds for the answer/judge models.

## Baseline only (no Bedrock) — one command

```bash
export EVALS_CONFIG=path/to/config.json BRAIN_API_KEY=…
bash bundles/brain/skills/evals-remote/baseline.sh
```

Runs health → brain sources → **scope gate** → **coverage**, prints a summary.
Exit codes: **0** gate PASS · **1** gate FAIL · **2** refused / usage error · **3** a stage failed (no verdict —
never read as PASS or FAIL). A brain without marts gets a baseline without the metric lens.

## Run everything

```bash
export EVALS_CONFIG=path/to/config.json EVALS_CSV=path/to/your-gold-set.csv BRAIN_API_KEY=…
bash bundles/brain/skills/evals-remote/run_eval.sh
```

Stages: health → brain sources → baseline gate (reported, not fatal) → coverage report → **existing**
`evals/generate_promptfoo.py` → promptfoo eval (Haiku/Sonnet/Opus × `REPEAT`, default 3) → snapshot →
consistency diff vs the previous snapshot. Failing tests are a result (promptfoo's exit 100), not a crash.

## Sensitive data

Every output — source lists, coverage, the generated config, answers, retrieved context, snapshots — is
client data. Both scripts write to `$EVALS_OUT` (default `/tmp/brain_eval-<config-stem>`), **refuse** a path
that resolves inside the git tree (before creating anything), and keep one snapshot per run under
`$EVALS_OUT/snapshots/`. The API key stays in the environment: it is withheld from the shared generator
(which would copy it into every test's vars, and so into the config, results and promptfoo's database),
passed to the Python client through its environment rather than its command line, and `run_eval.sh` refuses
to run if it ever finds the key in the generated config. Never commit eval outputs, the key, the per-brain
config, or a real gold set — only the generic scripts here are committable.

## How the brain's sources are found

`list_sources` (MCP ≥ 1.3.0) gives the exact catalog of visible documents. It hides superseded ones, but an
out-of-scope document is a defect even when a date cutoff hides it, so a sweep with `latest_only=false` adds
superseded sources (that part is a floor: retrieval surfaces a query-dependent subset). On a server without
`list_sources` the sweep alone is used and the report says it is a floor. Eval context searches, by contrast,
state `latest_only=true` — what an agent sees by default — so results don't move when a server default changes.

## Coverage lenses

- **Narrative-corpus (authoritative):** evals vs in-scope narrative files in the inventory — the honest denominator.
- **Brain sources:** which of the brain's in-scope sources an eval names (an eval covers its file exactly, or a
  file family by stem).
- **Governed-metric:** which numbers an eval pins (whole-word match); tabular files are covered here, not as
  narrative sources.

## Reuse contract

Does **not** fork the eval generator. Reuses `bundles/brain/skills/evals/generate_promptfoo.py` unchanged; the
only new seam is `load_brain_context.js` — it reads the `BRAIN_URL` var that generator already emits (the key
from the environment), speaks MCP (via `brain_mcp_client.py`), and enriches numeric questions with the most
recent `get_metric` rows, marking any value another report restated or contradicts.

## Files

| File | Purpose |
|---|---|
| `evals_config.py` | Central, brain-agnostic config (defaults + per-brain JSON override). |
| `brain_mcp_client.py` | MCP-over-HTTP client with a static key header (the one MCP seam). |
| `sharepoint_inventory.py` / `source_key.py` | Parse the corpus inventory xlsx; map brain `source` ↔ filename + full folder path. |
| `brain_inventory.py` | The brain's sources: `list_sources` catalog + superseded sources from a `latest_only=false` sweep. |
| `scope_rules.py` / `baseline_reconcile.py` | Config-driven scope classification + PASS/FAIL gate. |
| `evals.example.csv` | Fictional example gold set (existing-pipeline CSV schema); supply your own per brain. |
| `load_brain_context.js` | MCP context provider (+ `get_metric` enrichment). Drives the **existing** generator. |
| `coverage_report.py` | Maps evals → tested corpus/sources/metrics; emits the untested work queue. |
| `consistency_diff.py` | Accuracy, run-to-run and cross-model agreement; before/after diff of two snapshots. |
| `baseline.sh` | One-command **baseline** trigger (scope gate + coverage; no LLM). |
| `run_eval.sh` | Orchestrator (full loop incl. scored eval, config-driven). |
| `out_dir.sh` | Shared output-dir resolution + the outside-the-repo refusal. |
| `evals.config.example.json` | Config template to copy per brain. |
