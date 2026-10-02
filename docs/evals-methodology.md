# Evals Methodology (living document template)

**Owner:** `<your team / engagement>` · **Brain:** `<brain name>` @ `<brain MCP endpoint>`
**Last updated:** `<date>` · **Status:** active · **Skill:** `bundles/brain/skills/evals-remote/`

> This is a **living document template**. Copy it per project, fill in the owner/brain fields, and
> keep your own copy current — every eval run, finding, and methodology change gets appended to its
> Changelog (§11) and folded into the relevant section. **Keep project findings and client data in
> your own copy, outside this repo** — only the generic methodology (§12–§17 especially) belongs here.

> **Methodology v2, §12–§17: read these before running anything.** Generic lessons from an
> evidence-grade A/B eval run; no project-specific data is in this template.
>
> - **§12:** test the evals themselves before using them.
> - **§13:** ≤ 10 feature-targeted evals instead of 200+ generated rows.
> - **§14:** a clean comparison, 2 runs before and 2 after.
> - **§15:** reuse the existing brain incrementally; never rebuild.
> - **§16:** validate every finding before recording it.
> - **§17:** preflight, ground truth, cost and data hygiene.

---

## 0. What to run (operator quick start)

The skill is **brain-agnostic and config-driven** — all project specifics live in a config JSON you
supply and keep out of this repo (e.g. `evals.config.<yourbrain>.json`: brain url, corpus inventory,
scope policy).

Run in **your own shell** — the answer/judge models need valid AWS Bedrock credentials. Confirm first:
`aws sts get-caller-identity` must return an account (not `InvalidClientTokenId`).

**Prerequisites (once):** `./install.sh --bundle brain --deps` (creates `.claude/venv` with fastmcp); node/npx.

**Set env (every session):**
```bash
export EVALS_CONFIG='path/to/your/evals.config.json'      # brain-specific config, kept out of the repo
export BRAIN_API_KEY='<your brain API key>'                 # do NOT commit; omit for a local keyless brain
export EVALS_PY="$(pwd)/.claude/venv/bin/python"          # venv python with fastmcp
export AWS_REGION=us-east-1                               # + valid AWS_ACCESS_KEY_ID / SECRET (/ SESSION_TOKEN)
```

**Baseline + coverage only (NO Bedrock — this is "gain a baseline"):**
```bash
bash bundles/brain/skills/evals-remote/baseline.sh   # 0 PASS · 1 FAIL · 2 refused/usage · 3 stage failed
```

**Full loop (adds the scored eval — needs Bedrock):**
```bash
EVALS_CSV=path/to/your-gold-set.csv bash bundles/brain/skills/evals-remote/run_eval.sh "$EVALS_CONFIG"
```
Outputs land in `$EVALS_OUT` (default `/tmp/brain_eval-<config-stem>/`, refused inside the repo):
`baseline_report.md`, `coverage_report.md`, `promptfooconfig.yaml`, `results.json`, `eval_report.md`, and one
snapshot per run under `snapshots/`.

> After a run, record the scorecard + coverage % in your own copy's §9 (Findings) and §11 (Changelog).

---

## 1. Purpose

Measure the deployed brain on three axes — **accuracy, completeness, consistency** — against a
curated, human-reviewable gold set, and re-measure on every brain change so you can prove whether
newly-added data **improved** coverage or **regressed** an answer. The overriding
aim is *evals that find real gaps in corpus logic*, not a passing score.

Two guarantees the methodology enforces:
- **Numbers are computed, meaning is agentic.** Numeric claims must trace to `get_metric` (the marts),
  never to retrieved prose. An eval that grades a number off narrative retrieval is testing the wrong lane.
- **Out-of-scope data is a defect.** Any source from an engagement or period the project has explicitly
  scoped out (an older/unrelated engagement, a prior client relationship, superseded drafts) present in
  the brain **fails the baseline gate** — it is not a judgment call.

---

## 2. How it connects (auth + transport)

| Concern | Decision | Evidence |
|---|---|---|
| Endpoint | Your brain's MCP endpoint (Streamable-HTTP MCP, JSON-RPC) | `initialize` → `Semantic Knowledge Brain — <brain name> vX.Y.Z` |
| Auth | **Static API-key header** via your gateway (KISS). OAuth may exist but a static key is simpler when available. | gateway advertises OAuth; key still returns HTTP 200 |
| Client | `brain_mcp_client.py` wraps `fastmcp.Client` + `StreamableHttpTransport(headers={<your key header>: …})` | live `health` → your brain's `knowledge_version` |
| Reuse | The **existing** `bundles/brain/skills/evals/generate_promptfoo.py` + `generate_evals.py` pipeline is used **unchanged**; the only new seam is `load_brain_context.js` (MCP context provider) reading the `BRAIN_URL` var the generator already emits. The key stays in the environment: `run_eval.sh` withholds it from the generator, which would otherwise copy it into every test's vars. | confirm with one eval run through it |

Env contract: `EVALS_CONFIG` (brain config), `BRAIN_API_KEY` (secret, never written to disk; header from `config.brain.key_header`; omit for a keyless brain), `BRAIN_MCP_URL` (or `config.brain.url`), `EVALS_PY` (venv python with `fastmcp`), plus AWS Bedrock creds for the answer/judge models.

### Same-brain guarantee
Every artifact is stamped with the brain's self-reported `knowledge_version`. `consistency_diff.py` **warns
if two snapshots share a version** (not a real drift measurement) and labels a diff across two versions.
The version stamp *is* the automated "same brain?" check — confirm it by matching `knowledge_version`,
lane counts, and retrieved `chunk_id`s (64-bit content hashes) across any two access paths you use.

---

## 3. Metrics — definitions

| Metric | Definition | How measured |
|---|---|---|
| **Accuracy** | Did the answer state the correct fact? | `llm-rubric` (Sonnet judge) vs `expected_answer_must_contain`; numbers via `get_metric`. |
| **Completeness** | How many of the expected facts did the answer cover? | Rubric N-1-of-N threshold over the `\|`-separated fact list (paraphrase allowed, generic FAILs). |
| **Consistency — run-to-run** | Same question, same brain → stable answer across repeats. | promptfoo `repeat:N` → equal-outcome rate. |
| **Consistency — cross-model** | Haiku / Sonnet / Opus agree on the fact. | 3-tier provider matrix; divergence flags tier sensitivity. |
| **Consistency — temporal** | Answer stable before vs after new data. | `consistency_diff.py` diffs two version-stamped snapshots → `stable/improved/regressed/flipped`, plus `missing` for a question the newer run no longer asks. |

Not measured: **ambiguity** (the brain holding conflicting answers). An earlier probe called
`get_current_fact` / `get_question_status` with a free-text question, but those tools take an
entity + predicate / a question id, so it always reported "not ambiguous"; it was removed rather than
shipped. Measure it as answer disagreement (run-to-run, cross-model) until a real probe exists.

`consistency_diff.py` reads each row's id from `testCase.metadata.eval_id` (where the generator puts it) and
refuses a results file in which no row carries one, instead of reporting 0% accuracy.

---

## 4. Baseline & scope gate

Reconciles your corpus inventory export against the brain's actual sources. **Runnable before any
gold set exists.**

- **Provenance vs reporting period.** The exclusion cutoff applies to *when an artifact was produced*, not
  *what period its data describes*. A current-state document is KEEP even if it's recent; an old,
  superseded artifact is FAIL regardless of any recent dates inside it.
- **Buckets:** `keep` (engagement artifact / current-state data), `review` (ambiguous, human decides),
  `fail` (prior/older engagement, junk `*tobedeleted*`-style names, superseded dupes).
- **Gate:** any `fail` source in the brain → baseline **FAILs** (exit 1), lists offenders with provenance.
  The gate never mutates the brain; removal is a human action in the build project.

---

## 5. Coverage methodology

`coverage_report.py` maps each eval's `ground_truth_source` onto three lenses (all config-driven):

| Lens | Denominator | Use |
|---|---|---|
| **Narrative-corpus (authoritative)** | in-scope narrative files in the inventory (`config.corpus.narrative_exts`, minus scope-`fail`) — does **not** depend on retrieval | the honest coverage %; splits into tested / present-but-untested / **not-surfaced-by-retrieval** |
| **Brain sources** | the brain's in-scope sources: the `list_sources` catalog plus superseded sources a `latest_only=false` sweep surfaces (sweep alone, a floor, on a server without `list_sources`) | which sources an eval names: a file exactly, or a family by stem |
| **Governed metrics** | `list_metrics` | which numbers an eval pins; **tabular files are covered here**, not as narrative sources |

Why two source lenses: the brain's catalog lists what was **ingested**; your inventory export lists what
**exists**. Narrative coverage is denominated on the inventory, and the `not_retrievable` bucket (in the
corpus, not among the brain's sources) flags genuine ingestion gaps. Superseded documents count as brain
sources: the scope gate must see an out-of-scope document even when a date cutoff hides it from search.

**Standing coverage targets** (checked each build): every in-scope narrative corpus file ≥1 recall eval;
every governed metric ≥1 `get_metric`-grounded eval; ≥20% adversarial no-hallucination probes.

---

## 6. Delta / re-evaluation — when to add new evals

The `coverage_report.py` `untested_sources` list is the backlog; a data update is "done" only when every
new in-scope source has ≥1 eval.

| Trigger event | Detect with | Action | Gate before promoting new version |
|---|---|---|---|
| New file(s) onboarded | `brain_inventory` diff | ≥1 grounded eval per new **in-scope** source | new-source coverage = 100% |
| Brain rebuild (version change) | `health.knowledge_version` | re-run frozen set + add delta | `consistency_diff` shows no unexplained regression |
| New governed metric | `list_metrics` diff | ≥1 `get_metric`-grounded eval | metric coverage ≥ target |
| Source removed | inventory diff / `orphan_eval_sources` | retire or re-target eval | zero orphan evals |
| Scope-FAIL source appears | baseline gate | **removal ticket — NOT an eval** | baseline gate PASS |
| Regression | `consistency_diff` | add a **pinning eval** locking the correct answer | flagged & reviewed |

**Freeze discipline:** `eval_id` + `expected_answer_must_contain` are immutable. Changing a fact = a *new*
`eval_id`, never an edit — that is what makes "new data broke an old answer" a real signal.

---

## 7. The gold set (human-reviewable)

`bundles/brain/skills/evals-remote/evals.example.csv` is a schema example only — **keep your real gold
set out of this repo**, same schema as the existing pipeline
(`eval_id, category, scope, question, query_suffix, expected_answer_must_contain, expected_answer_must_not_contain, ground_truth_source, notes, min_items`).
`min_items: 0` rows are adversarial no-hallucination (correct answer = "not established"). Edit your own
copy directly to review/add/remove cases.

---

## 8. Runbook

```bash
export EVALS_CONFIG=path/to/config.json  BRAIN_API_KEY=…
export EVALS_PY=$(pwd)/.claude/venv/bin/python
PY="$EVALS_PY"   # shorthand for the manual steps below
# valid AWS Bedrock creds required for the answer/judge models
S=bundles/brain/skills/evals-remote

# The two scripts below run exactly these steps; prefer them. Manual steps, with O outside the repo:
O=/tmp/brain_eval-manual; mkdir -p $O

# 1. brain sources + scope gate (exit 1 = out-of-scope leakage; anything else = could not run)
$PY $S/brain_inventory.py --config "$EVALS_CONFIG" --out $O/brain.json
$PY $S/baseline_reconcile.py --config "$EVALS_CONFIG" --brain-json $O/brain.json --brain-version <ver> --out $O/baseline.md

# 2. coverage report (the work queue); skip the metric lens on a brain without marts
$PY $S/brain_mcp_client.py call list_metrics --json '{}' --config "$EVALS_CONFIG" > $O/metrics.json || rm -f $O/metrics.json
METRICS_ARG=""; [ -s $O/metrics.json ] && METRICS_ARG="--metrics-json $O/metrics.json"
$PY $S/coverage_report.py --config "$EVALS_CONFIG" --evals <your-gold-set.csv> \
   --brain-json $O/brain.json $METRICS_ARG --brain-version <ver> --out $O/coverage.md

# 3. eval (reuses the existing generator, WITHOUT the key in its environment) → promptfoo
env -u BRAIN_API_KEY $PY bundles/brain/skills/evals/generate_promptfoo.py \
   --csv <your-gold-set.csv> --out $O/promptfooconfig.yaml \
   --brain-url <brain mcp url> --context-js $(pwd)/$S/load_brain_context.js
( cd $O && npx promptfoo@0.123.0 eval --config promptfooconfig.yaml --no-cache --output results.json )  # exit 100 = some tests failed

# 4. consistency diff vs the previous snapshot
$PY $S/consistency_diff.py --current $O/results.json --baseline <prev>.json --out $O/eval_report.md
```

---

## 9. Findings log (living — project-specific, keep in your own copy)

Findings about any one brain's actual data (what it does or doesn't know, real coverage numbers, real
source names) are project-specific and belong in **your own copy** of this doc, not this template —
per §17.4, keep evidence and client strings outside this repo.

Findings about the **eval harness itself** (bugs in the generic tooling, independent of any brain's
content) are generic and worth keeping here as a shared record:

### F-010 — The retrieval grader passes on the recall probe, not on the question  *(validated; fix in a pending PR)*
`evals/run_evals.py` (default mode) also searches each expected snippet **directly**. A row therefore passes
whenever the string is anywhere in the index, even if the question never retrieves it.

Its `expected_answer_must_not_contain` was ignored on regular rows. The docstring said "ALL snippets must
appear" while the code accepted ANY.

Evidence on a 303-row set: default 94% vs question-only top-5 60%. A known misread ID stated as fact could not
fail any row.

**Fix:** `--strict` (question only, top 5) plus forbidden strings enforced on every row, in a separate
`run_evals` PR. **Until it merges, report default and strict side by side, and never default alone.**

### F-011 — Generated gold rows cannot fail  *(validated)*
`generate_evals.py --db` emits one template: "What <category> content was discussed? (source: <file>)".

- `expected_answer_must_contain` is the source name plus the **first alphabetic word of the chunk**, often
  "Yeah" or "Okay".
- With OR-grading and the recall probe, such rows measure "the source name is in the index".
- Naming the source in the question also biases retrieval.

Regeneration is deterministic (identical output for the same store), which makes these rows a usable
**regression smoke**. They are **not** evidence of answer quality.

### F-012 — The promptfoo rubric misgrades leak/no-hallucination rows  *(validated)*
`generate_promptfoo.build_rubric` turns every `min_items: 0` row into "the answer must acknowledge absence of
data". A row meant to check that *forbidden text is absent* (a meeting-UI or caption leak) is then graded on
the wrong question: it fails even when nothing leaked.

**Rule:** express leak checks as regular rows (one true fact in `must_contain`, the leak token in
`must_not_contain`), or grade them deterministically. Use `min_items: 0` only for genuine "not in the
corpus" probes.

### F-013 — The LLM judge is lenient on exact identifiers  *(validated)*
The judge accepted an answer without the exact file name that a string check marked missing. It also accepts a
*spoken* paraphrase of a user-profile name (e.g. "agent one") for its exact screen string (e.g. "Agent 1").

**Rule:** for IDs, codes and file names, pair the LLM judge with an exact string check (`run_evals --strict`
or a doc metric). The judge alone overstates ID accuracy.

### F-014 — Harness bugs found while running the orchestrator  *(validated; fixed — see F-015)*
1. `run_eval.sh` aborts before promptfoo with `BRAIN_API_KEY=""`: `${BRAIN_API_KEY:?}` rejects empty values,
   although the Python/JS clients support a keyless brain.
2. Its coverage step aborts the whole run (under `set -e`) when `list_metrics` fails on a brain without marts.

**Fixed:** the key is optional in both scripts, and a failing `list_metrics` skips the metric lens instead of
aborting. Both are covered by end-to-end tests against a local fake brain.

### F-015 — The harness's own outputs were wrong or leaked  *(validated; fixed, each with a failing test first)*
Found by running both scripts end to end against a fake brain over real MCP/HTTP, and by replaying real
promptfoo exports through `consistency_diff.py`:
1. `consistency_diff.py` looked for `vars.gold_id`; the generator writes `testCase.metadata.eval_id`. Every
   real run reported **0% accuracy and no regressions**. Replayed on a real export it now reports the same
   accuracy as the promptfoo viewer.
2. The scope gate saw only a source's **first** folder, so a prior-engagement folder nested deeper passed,
   and it never saw **superseded** documents (default search hides them).
3. With a key set, the generator copied it into every test's vars, so it reached `promptfooconfig.yaml`,
   `results.json`, promptfoo's database and a `snapshots/` folder **inside the repo**.
4. A crashed reconcile exited 1, the same code as gate FAIL, and the summary then read a stale report from an
   earlier run. Stage errors now exit 3 and stale reports are deleted first.
5. Coverage merged same-named files from different folders and counted `plan.pdf` as covering
   `Business plan.pdf`. Metric names matched inside words (`sla` in `translation`).
6. The context provider attached the **oldest** metric rows (rows come oldest first, then `slice(-8)`), not the
   newest; it now anchors on the catalog's `last_month` and marks restated/conflicting values.
7. The JS tests exercised pasted copies of the provider's functions rather than the module, and two
   summary tests pasted a copy of the script's heredoc. All now run the shipped files.
8. promptfoo's exit 100 (some tests failed) aborted `run_eval.sh` before the snapshot and diff.

## 10. Backlog

- [ ] Re-run live with valid Bedrock creds → confirm your project's gold set is green; record the scorecard.
- [ ] Add remaining gold-set cases from your own coverage gaps → target ≥80% coverage.
- [ ] Ambiguity (removed, see §3): re-model it as answer disagreement (repeat/cross-model) plus curated
      entity/predicate probes against `get_current_fact`, test-first, before reintroducing it.
- [ ] Second snapshot when a new `knowledge_version` ships → demonstrate the temporal diff.
- [x] Brain source discovery: `list_sources` catalog + superseded sweep (F-015.2).
- [ ] **F-010:** merge the `run_evals --strict` / forbidden-string PR; until then, report default + strict.
- [ ] **F-011:** replace the generated rows' role as evidence with feature-targeted sets (§13). Keep ≤ 60
      generated rows as a regression smoke.
- [ ] **F-012:** fix `build_rubric` so a `min_items: 0` row with a leak token is graded as "must not contain",
      not "acknowledge absence" (test-first).
- [x] **F-014:** keyless runs; metric lens skipped on brains without marts.
- [ ] Apply §14 to the next brain version change: 2 runs on the current version, 2 on the new one, and a
      report per §14.4.

---

## 11. Changelog

- **(harness correctness)** — F-015: the diff reads real promptfoo rows; the gate sees nested folders and
  superseded documents via `list_sources` + a `latest_only=false` sweep; the key never reaches disk; outputs
  and snapshots stay outside the repo; explicit exit codes (0/1/2/3); newest metric rows; ambiguity probe
  removed. Domain vocabulary moved to config (`corpus.seed_terms`, `context.metric_synonyms`), and the
  example gold set is fictional. End-to-end tests run both scripts against a fake brain over real MCP/HTTP.

- **(methodology v2)** — Folded in the lessons of an evidence-grade A/B eval run, generic lessons only.
  Added §12–§17:
  - test the evals first;
  - feature-targeted ≤ 10-eval sets;
  - 2-before / 2-after clean comparison;
  - incremental reuse of the existing brain (add → measure → remove → exact S0 → add), with no rebuild;
  - finding validation;
  - preflight and hygiene.

  Findings F-010 to F-014 are about the eval harness itself, not about any one brain's data.

- **(reusable + coverage fix)** — Made the skill **brain-agnostic and config-driven**
  (`evals_config.py`): endpoint, key env, corpus inventory, and scope policy (exclude/keep/junk patterns,
  provenance cutoff) all move to a per-brain config JSON — nothing project-specific in code (a test asserts
  no hardcoded endpoint). Generic env: `EVALS_CONFIG`, `BRAIN_MCP_URL`/`BRAIN_API_KEY`, `EVALS_PY`. Runner
  renamed to a brain-agnostic `run_eval.sh`; gold CSV named generically `evals.example.csv`; any
  project-specific config stays out of the repo. **Coverage denominator fixed**: authoritative
  narrative-corpus lens replaces the optimistic sweep-floor %; the `not_retrievable` bucket added.

> Project-specific run results and findings (real coverage numbers, real source names, real timings)
> belong in your own copy's changelog, not here.

---

## 12. Test the evals before using them (methodology v2)

An eval set is code. It gets its own checks **before** any result is used as evidence. Each check below
caught a real defect (F-010 to F-013).

| # | Check | How | Fails when |
|---|---|---|---|
| Q1 | **Can each row fail?** | Rows must name a concrete fact from a primary source (image, transcript, spreadsheet). No filler words, and no answer embedded in the question. | template rows as in F-011 |
| Q2 | **Does the grader grade the question?** | Run `run_evals` default **and** `--strict`; a row that passes only in default mode passed on the recall probe | F-010 |
| Q3 | **Are forbidden strings enforced?** | Seed one row whose `must_not_contain` is known to be present in the store; it must FAIL | F-010 |
| Q4 | **Does the judge grade what the row means?** | Read the generated rubric for one row of each kind (fact, adversarial, leak) before the run | F-012 |
| Q5 | **Controls:** one known-pass row and one known-fail row, carried in every run | If either flips, the harness changed; stop | — |
| Q6 | **Noise band:** run the unchanged state twice (§14) | Count flipped cells; later differences inside the band are noise | — |
| Q7 | **Frozen truth:** expected strings and questions are written **before** any tool output exists and frozen with sha256; every run checks the hash | edited truth invalidates the comparison | — |
| Q8 | **Exact strings need a string check:** pair the LLM judge with `--strict` or a doc metric for IDs, codes, file names and numbers | F-013 | — |

Generation (`generate_evals.py --db`) is deterministic, so regenerating gives an identical file for an
unchanged store. Use that as a regression smoke, not as evidence (F-011).

## 13. Smart, feature-targeted evals: ≤ 10 per feature, not 200+ generic rows

**Principle:** one row = one claim of the change under test, with a **predicted outcome per state**
(before / after / control), written down before running. A set that can't tell the before-state from the
after-state proves nothing, however large it is.

| Row type | Proves | Predicted before → after |
|---|---|---|
| New screen-only fact (×2) | the feature adds answerable content | fail → pass |
| New spoken/transcript fact | the ingestion path works (e.g. ASR) | fail → pass |
| Existing fact through a changed path | nothing is lost (e.g. a transcript retired by a replacement doc) | pass → pass |
| Exact ID with its look-alike forbidden | verbatim fidelity: pass or honest `[illegible]`, never the look-alike | fail → pass / gap |
| Leak check: the true fact plus a forbidden UI/caption token (regular row, see F-012) | noise is not presented as content | depends on the change |
| Not in the corpus | no invention | pass → pass |
| 2 controls from the previous baseline (1 pass, 1 fail) | the harness is stable | unchanged |

- **Deterministic doc metrics carry the "noise" claims**, with no LLM and identical on every rerun: duplicate
  pairs, UI/caption leaks, cross-frame references, empty chunks, exact strings found or forbidden, coverage.
- **The LLM answer eval** is supporting evidence for "does a user get a better answer".
- **Large generated sets** (§7 gold set, F-011 rows) stay as a periodic regression smoke. Cap a run at
  **≤ 100 questions**.

## 14. Clean comparison protocol: 2 runs before, 2 after

**14.1 Order**

1. Freeze the eval file (sha256).
2. **Before ×2** on the unchanged state gives the noise band.
3. Apply exactly **one** change (§15).
4. **After ×2.**
5. **Control arm when the change is code:** run old code and new code on byte-identical inputs (same frames,
   same transcript, same corpus delta). The only difference between the arms is then the code under test.
   Verify the inputs are identical (hashes) and record it.

**14.2 Where the model varies**

- Vision/LLM extraction can vary between runs of the same code; a small-text title can be read correctly in
  one arm and misread in the other.
- For extraction features, the second "after" run should **re-run the extraction**, not only the eval,
  otherwise extraction variance is invisible.
- A claim counts only if it holds in both runs and exceeds the band.

**14.3 Evidence kept per run** (outside the repo): the eval file hash, the store state (document/chunk
counts), the promptfoo eval id, `run_evals` default and strict JSON, doc metrics JSON, and a copy of each
changed doc.

**14.4 Report shape**
1. Controls: the noise band, identical inputs, only-the-change delta, controls stable.
2. Per-question matrix: rows × states × tiers, plus strict and default.
3. Deterministic doc metrics: before → after.
4. What the change does **not** fix, stated as limits.
5. Findings (validated per §16).
6. Confidence per claim, and a verdict.

## 15. Reuse the existing brain: incremental A/B, never rebuild

A full rebuild re-embeds and re-classifies the whole corpus. The maintenance tools already support an exact,
incremental change, so every comparison runs on the **existing** brain.

```
S0 (today) ─ add change via maintenance ─▶ S1 (arm A) ─ remove via maintenance ─▶ S0 (verified exact)
            └─ add change again (arm B) ─▶ S2 (kept)
```

**15.1 Steps**

1. **Backup first:** `sqlite3 <store> ".backup S0.sqlite"` plus a tar of `parsed/ assets/ video/ vision/`.
2. **Add:** copy the sources into the registered root, then `./brain source plan`. **Apply only the actions of
   the change**: filter the plan. A plan can carry unrelated adds, such as duplicate-content twins or
   out-of-scope sidecars.
3. Materialise only those sources (e.g. the per-recording video lane).
4. Run `./brain plan parsed …`. The delta must be exactly the change, for example "+2 added, −1
   superseded_by_video, N unchanged".
5. Run `./brain update parsed …`: a snapshot, then only the delta is embedded.
6. **Remove:** `./brain source remove <id> --yes`, `video_capture.py forget …`, and re-parse the transcripts
   (dry-run into a scratch dir first: the docs must be byte-identical), then update.
7. **Verify exact S0:** document and chunk counts **and every chunk id+source+text equal the backup**. Chunk
   ids are stable, so this is a strict check. The evals must equal S0.
8. **Re-add the other arm** with `./brain source restore <id>`, which reactivates the same source.
9. A server that opens a read-only connection per call sees each change at once, with **no restart**.

**15.2 Measured cost example** (2 × ~25-min recordings, 23 frames):
- `update`: 30 s;
- remove / back to S0: ~3 min;
- each arm: ~8–12 min, with ~7 vision agents plus ~2 readers.

A rebuild would re-embed ~10k chunks and re-classify them all.

**15.3 Pitfalls**

| Pitfall | Handling |
|---|---|
| `--strict-sources` refuses because of pre-existing unregistered docs | Run without the strict flag; note the pre-existing state; never register unrelated sources to "fix" it |
| `brain update` needs a Python whose sqlite loads extensions | Set `BRAIN_PY` to such an interpreter; the first failed attempt aborted before any write |
| Old code can't write to a cache schema upgraded by new code (downgrade) | Old-code arm runs without `--db` (the cache isn't needed when results are passed) |
| Remove + re-add drops topic tags until reclassified | Skip reclassification in both arms (retrieval doesn't use tags; the arms stay equal); reclassify once for the kept state |
| A remote/deployed brain (e.g. behind an API gateway) can't be switched | Run §15 on the build project's local store, deploy only the kept state, and repeat §14 remotely as a confirmation |

## 16. Validating and documenting findings

A finding is recorded only after it passes these checks:

1. **Reproduce** it at least once, or show its deterministic source (a file, a hash, a log line).
2. **Validate against the primary source.** Zoom the screenshot or read the transcript line. Two independent
   model runs agreeing against your own reading means re-check your reading.
3. **Rule out a measurement artefact.** Token lists can match real content: a browser tab or a chat author
   looks like a participant name. Recount with narrower tokens before reporting a leak.
4. **Root cause** before any fix; the fix gets a test that fails first. For harness findings, **replay the
   real failing input** through the fixed code.
5. **Record it:** `F-NNN — claim (status)`, evidence path, validation method, root cause, fix or owner.
   Mark superseded or retracted findings as such; never delete them.
6. **Post-fix regression check:** if the fix can't change outputs for valid input, prove it (e.g. byte-identical
   re-assembly and a 0-change plan) instead of re-running the whole comparison.

## 17. Anything else: preflight, ground truth, cost, hygiene

**17.1 Preflight** (before spending agents or Bedrock):
- `eval "$(grep '^export AWS_' ~/.zshrc)"` and `aws sts get-caller-identity`.
- `EVALS_PY` has fastmcp; `BRAIN_PY` loads sqlite extensions.
- Brain `health` OK; ports free; never touch live servers.
- Eval-file hash recorded; backup taken.
- F-014 workarounds in place.

**17.2 Ground truth:** expected answers come from the primary source (image, transcript line, cell), read
**before** any tool output exists. Include look-alikes as forbidden strings; a person spot-checks the frozen
file.

**17.3 Measured costs, for estimates:**

| Item | Measured cost |
|---|---|
| promptfoo | 3.4 s/case at concurrency 4 (300 cases in 17 min; 30 in ~1.3 min) |
| `run_evals` | < 1 s per 10 rows |
| whisper `small.en` | ~2.3 s per audio minute |
| Frame capture | ~10 s per recording |
| Vision agent | 30–150 s per batch of ≤ 7 frames |
| Blind reader | 12–240 s |

Watch for stalled calls: 30-minute hangs were seen in long runs. Monitor per case, and don't extrapolate from
the viewer's "# Tests" column.

**17.4 Run hygiene:**
- One clean run per question; stop superseded runs before starting new ones.
- Label runs (`--description`) so the viewer shows the state.
- Keep evidence and client strings outside the repo.
- Scan any code diff for client data before committing.

**17.5 Honest limits go in every report.** `[illegible]` marks model uncertainty, and `not_modeled` is an
honest gap, not a failure. State what a change does **not** fix as plainly as what it does.
