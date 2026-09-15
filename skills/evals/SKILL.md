---
name: evals
description: Generate adversarial eval suites from extraction JSONs and run them against any brain REST shim (FastMCP). Corpus-agnostic — works for any project that has *_extraction.json files and a running brain server. Produces a promptfoo score across Haiku/Sonnet/Opus tiers.
---

# Evals Skill

Generate adversarial eval suites from `*_extraction.json` files and run them against a FastMCP brain.

## Quick start

```bash
# Full e2e from VTT corpus
bash skills/evals/run_e2e.sh \
  --corpus   /path/to/vtt/dir \
  --work     /tmp/my_project_e2e \
  --brain-port 8003 \
  --taxonomy /path/to/taxonomy.json

# Just generate evals from existing extractions
python skills/evals/generate_evals.py \
  --extractions /path/to/output/dir \
  --out /tmp/evals.csv

# Just generate promptfoo YAML
python skills/evals/generate_promptfoo.py \
  --csv /tmp/evals.csv \
  --out /tmp/promptfooconfig.yaml \
  --brain-url http://localhost:8003 \
  --context-js skills/evals/load_brain_context.js
```

## Files

| File | Purpose |
|------|---------|
| `generate_evals.py` | Read `*_extraction.json` → adversarial eval CSV |
| `generate_promptfoo.py` | Read CSV → promptfoo YAML with 3 Bedrock providers |
| `load_brain_context.js` | Dynamic context fetcher (dual-query, dedup) |
| `run_e2e.sh` | Full pipeline orchestrator |

## Eval modes and pass-rate comparability

Two modes produce `evals.csv` with different ground-truth quality:

| Mode | Trigger | `expected_answer_must_contain` | `query_suffix` |
|------|---------|-------------------------------|----------------|
| **Extraction** (recommended) | AWS creds present; `extract_facts.py` runs | Verbatim quoted fact from transcript | Keywords derived from the fact itself |
| **DB fallback** | No AWS creds; reads `chunk_topics` directly | Source slug (e.g. `"aug31"`) | Taxonomy `eval_config` default or empty |

**Pass rates from the two modes are not directly comparable.** Extraction-mode tests whether the brain retrieves and surfaces specific facts. DB-mode tests whether the brain retrieves the right source. Running without AWS creds produces a higher pass rate (source slug is easier to satisfy than a verbatim fact) but measures a weaker property.

Always use extraction mode for meaningful quality tracking. DB mode is a smoke test.
