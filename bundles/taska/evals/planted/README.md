# Planted suite (tier 1)

Invented meetings with known work items, used to test taska's meeting extractor end-to-end.

## What is here

| File | Purpose |
|---|---|
| `generate.py` | Deterministic VTT + gold-annotation generator; `per_form` scorer; `run.sh` entry point |
| `templates.json` | 14 item templates (one per form slot) and a filler utterance pool |
| `run.sh` | Shell wrapper: `run.sh [--out DIR] SEED...` |
| `out/` | Per-run JSON outputs (gitignored) |

## Quick start

```bash
# Unit tests only (no claude needed)
cd <repo-root>
uv run --with pytest --with pyyaml python -m pytest bundles/taska/tests/test_planted_generator.py -v

# End-to-end for seed 1 (requires claude on PATH)
bundles/taska/evals/planted/run.sh 1
```

## What `generate(seed)` produces

- A 30-minute WebVTT transcript named `YYYY-MM-DD Harbor daily sync.vtt` (date derived from the seed).
- 14 gold annotation items spread 4/5/5 across the temporal thirds, covering all forms:
  `commitment`, `request`, `due_phrase`, `client_owed`, `proposal_team`, `proposal_none`,
  `conditional_decision`, `risk`, `declined`.
- Speakers: Nora Quill, Ravi Tamsin (internal), Opal Brennick (client).
- Team: Yard team (members Nora Quill, Ravi Tamsin; aliases "yard guys", "the yard people").

Gold items are in Task-3 format (`{"evidence","quote","ts","speaker","title","kind","commitment",
"owner","due","status","client_owed","covered_by","must_report","source"}`) plus `"form"`.
`"evidence"` is `null` until `run.sh` patches it after `taska sync`.

## Output format

```json
{
  "seeds": {
    "1": {"incomplete": false, "forms": {"commitment": {"n": 2, "ok": 2, "rate": 1.0}, ...}}
  },
  "total": {"commitment": {"n": 2, "ok": 2, "rate": 1.0}, ...}
}
```

`{"incomplete": true}` when claude is not on PATH or the evidence item was not processed.

## per_form ok rules

| commitment | ok when |
|---|---|
| `committed` | matched to a node in the ledger |
| `proposed`  | matched to a node **or** a pending propose card |
| `declined`  | **not** matched to any node |
