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
  --brain-url http://localhost:8002 \
  --context-js skills/evals/load_brain_context.js
```

## Files

| File | Purpose |
|------|---------|
| `generate_evals.py` | Read `*_extraction.json` → adversarial eval CSV |
| `generate_promptfoo.py` | Read CSV → promptfoo YAML with 3 Bedrock providers |
| `load_brain_context.js` | Dynamic context fetcher (dual-query, dedup) |
| `run_e2e.sh` | Full pipeline orchestrator |
