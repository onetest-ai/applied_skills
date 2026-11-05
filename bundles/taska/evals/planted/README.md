# Planted suite (tier 1)

Invented meetings with known work items, used to test taska's meeting extractor end-to-end.

## What is here

| File | Purpose |
|---|---|
| `generate.py` | Deterministic VTT + gold-annotation generator for both meetings; `per_form` scorer; `run.sh` entry point |
| `templates.json` | 14 daily-sync item templates (`slots`), 10 planning-review templates (`decision_slots`), 11 workshop-prep templates (`workshop_slots`) and a filler utterance pool |
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
- The same day's second meeting, from `generate_planning(seed)`: `YYYY-MM-DD Harbor planning review.vtt`, 10 items
  spread 3/3/4 by timestamp. It is short (under `coverage.MIN_CHARS`), so the decision gate reads it as one part:
  the planted suite measures reading each item, and the per-third gate is exercised on real three-part meetings:

| form | n | expected |
|---|---|---|
| `implicit_decision` | 3 | a `decision` node |
| `client_decision` | 1 | a `decision` node (the client decides) |
| `cadence_decision` | 1 | a `decision` node (a standing routine) |
| `implicit_risk` | 1 | a `risk` node |
| `open_question` | 1 | **no** decision node created from it |
| `hypothetical` | 1 | **no** decision node created from it |
| `people_matter` | 2 | **no node of any kind** and no card |

  No decision or risk utterance carries a word from `leftovers.DECISION_CUES` or `MARKERS`, so the suite measures
  reading, not keywords; each people matter is caught by `sensitive.people_matter`.
- The same day's third meeting, from `generate_workshop(seed)`: `YYYY-MM-DD Harbor workshop prep.vtt`, 3 real work
  items (forms `commitment`, `request`, `due_phrase`: client deliverables, no deck words) among 8 utterances the
  tracking rule excludes, each `"form": "not_work"` with a `"bullet"`: `deck_handling` (2), `agenda_invite`,
  `poster`, `session_order`, `how_might_we`, `client_pain` (2, the client's problems such as "our intro pricing
  invites fraud"). `per_form` scores the 3 work items and skips `not_work`; `not_work_recorded(store, gold)` returns
  `{"recorded": n, "items": [{"id","kind","title","bullet"}]}`, the agent creates and proposals quoting a
  `not_work` utterance or sharing its turn. The run result also carries the sum as `not_work_recorded`; 0 is the bar.
- Team: Yard team (members Nora Quill, Ravi Tamsin; aliases "yard guys", "the yard people").

Gold items are in Task-3 format (`{"evidence","quote","ts","speaker","title","kind","commitment",
"owner","due","status","client_owed","covered_by","must_report","source"}`) plus `"form"` and `"file"` (the VTT the
item comes from); a people matter also carries `"people_matter": true`. The planning review's negatives have
`"commitment": "not_work"` and `"kind": null`. `"evidence"` is `null` until `run.sh` patches it after `taska sync`
with the id of the meeting whose title is that file's name without `.vtt`.

## Output format

```json
{
  "seeds": {
    "1": {"incomplete": false, "forms": {"commitment": {"n": 2, "ok": 2, "rate": 1.0}, ...}}
  },
  "total": {"commitment": {"n": 2, "ok": 2, "rate": 1.0}, ...}
}
```

`{"incomplete": true}` when claude is not on PATH or either meeting was not processed.

## per_form ok rules

| commitment | ok when |
|---|---|
| `committed` | matched to a node in the ledger |
| `proposed`  | matched to a node **or** a pending propose card |
| `declined`  | **not** matched to a task or story (a decision recording the "no" is fine) |

The planning review's forms count every node whose quote holds the item (5-word phrase containment ≥ 0.5), not the
nearest one: its items sit a few hundred characters apart.

| form | ok when |
|---|---|
| `implicit_decision`, `client_decision`, `cadence_decision` | a `decision` node quotes it |
| `implicit_risk` | a `risk` node quotes it |
| `open_question`, `hypothetical` | no `decision` node is created from it (a note quoting it on another decision is fine) |
| `people_matter` | no node of any kind and no pending card quotes it |
