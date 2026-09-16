# Temporal Fact Intake — Design

- **Date:** 2026-09-16
- **Status:** Draft for review
- **Skill affected:** `bundles/brain/skills/knowledge-index` (colocated with `temporal_memory.py`)
- **Approach:** A — agentic extract → deterministic-normalize + embedding-suggested merge → agentic link-adjudication → human-gated dry-run → load (chosen)

## 1. Goal

Turn transcripts and documents in the corpus into evidence-backed, time-ordered
**assertions** in `temporal_memory`, so that later statements (typically from
calls) **refine or correct** earlier understanding (typically from docs) instead
of silently coexisting. Capture **facts + verbatim evidence**, plus **sentiment**
on each assertion. Do this without over-tagging the intent taxonomy — facts live
in the temporal ledger, not in `chunk_topics`.

Today `temporal_memory.py` can **store and resolve** a ledger
(`memory_assertions` + `memory_assertion_links`, `current_fact(as_of)`,
`question_status`, `conflicted` state) but **nothing produces that ledger**. This
design adds the producer.

## 2. Context and key decisions (agreed)

- **Joined intake.** One corpus; updates add docs *and* VTTs. Fact-intake is a
  **stage of the same unified intake** as retrieval indexing (it reads the same
  `chunks` table), not a VTT-only side pipeline. Doc facts seed the ledger; later
  call facts supersede them by `event_time`.
- **Resolution: equal authority, recency wins.** All assertions carry
  `authority = 0`. `current_fact()` already resolves by recency + `supersedes`.
  Accepted tradeoff: a stray later mention can override a deliberate doc fact —
  the **human gate on links** is the guard.
- **Evidence stored inline** (verbatim quote), not by reference only.
- **Human-gated dry-run** is the safeguard on the one risky operation (overriding
  a prior fact), mirroring `taxonomy_merge`'s default dry-run diff.
- **Full subsystem:** fuzzy entity/predicate resolution + LLM-proposed links.

## 3. Non-goals (YAGNI / deferred)

- **Questions/answers extraction.** `temporal_memory` supports a question ledger
  (`memory_questions`/`memory_answers`, open→resolved across sessions), but this
  build extracts **assertions only**. Question capture is a later pass.
- **Per-call dominant intent rollup.** Out of scope; assertions are per-turn.
- **New MCP tools.** Resolution is already exposed via existing
  `get_current_fact`/`get_question_status`; no new server surface here. Exposing
  evidence/sentiment through MCP is a follow-up.
- **Cross-entity/predicate reasoning.** Links only connect the same
  `(entity, predicate)`, as `temporal_memory` requires.

## 4. Architecture — pipeline

Mirrors the repo's `classify_prep → agents → classify_write` idiom.

```
chunks (docs + vtts, from knowledge.sqlite)
   │  fact_prep.py            (deterministic: batch + instructions)
   ▼
batch_k.json  ──►  low-tier agents (Haiku)  ──►  result_k.json   (assertions)
                                                     │
                                                     ▼
                              fact_write.py   (the deterministic core + judge-on-conflict)
                                 ├─ normalize (entity, predicate): alias/exact → auto; cosine tiers (auto/review/reject)
                                 ├─ candidate priors: same canonical (entity, predicate) in memory_assertions
                                 ├─ link: deterministic recency → auto-supersedes; disagreement → LLM judge
                                 ├─ audit: fact_intake_report.json (every auto merge + supersede, reviewable)
                                 └─ --apply: emit ledger v1.0 → temporal_memory.load_ledger   (--dry-run shows, writes nothing)
```

Three new files in `knowledge-index/`: `fact_prep.py`, `fact_write.py`, and a
small shared `fact_schema.py` (assertion dataclass + id derivation + ledger
builder, so prep/write/tests agree on one shape). No move of `temporal_memory.py`
(`fastmcp_server` imports it).

## 5. Schema changes (additive, backward-compatible)

In `temporal_memory.ensure_schema`, add columns to `memory_assertions` via
`ALTER TABLE ADD COLUMN` guarded by a `PRAGMA table_info` check (same migration
idiom already used in `knowledge_index._ensure_schema`):

- `evidence TEXT` — the verbatim quote supporting the assertion.
- `sentiment TEXT` — one of `positive | neutral | negative | mixed` (default
  `neutral`).
- `stance TEXT` — optional short stance toward the entity (free text, may be
  empty).

`load_ledger` is extended to read `evidence`, `sentiment`, `stance` from each
assertion item (defaulting when absent) and include them in the immutable insert.
Existing ledgers without these fields keep working (defaults applied). The
`_insert_immutable` content-equality check now includes the new columns.

## 6. Extraction contract (agent output)

`fact_prep.py` writes `vocab`-free instructions (open-category) plus batches of
`{id, source, title, event_date, speaker, preview}` per chunk. Agents emit
`result_k.json`: a list of assertions, each:

```json
{
  "chunk_id": 412,
  "entity": "go-live",
  "predicate": "date",
  "value": "Q3 2026",
  "evidence": "we're now targeting Q3 for go-live",
  "sentiment": "neutral",
  "stance": ""
}
```

Rules (reuse `extract_facts`'s quality bar):
- **Open-category** — extract any salient fact, not only taxonomy L1 categories,
  so corrective facts in unmodeled areas are not dropped.
- Every assertion must state WHO/WHAT + a concrete action, decision, risk,
  finding, date, number, or status. Skip questions, hedges, and chit-chat →
  emit nothing for that chunk (`[]`).
- `entity`/`predicate` are short noun-phrases the agent proposes; canonicalization
  happens deterministically in `fact_write`.
- `value` is the asserted value (string or number-as-string).
- `evidence` is a verbatim ≤200-char quote from the chunk.
- `sentiment` ∈ the four values; `stance` optional.

`event_time` and `speaker` are **not** trusted from the agent — `fact_write` reads
them from the chunk row (`event_date`, `speaker`); `asserted_at = event_date` (or
`created_at`/ingest time when absent), `ingested_at = now`, `source = chunk.source`,
`segment_id = "chunk:<id>"`.

## 7. Normalization + linking (`fact_write.py`)

Design principle: **auto by default, escalate only genuine ambiguity.** Pre-gating
every similar pair overwhelms the human and trains rubber-stamping; instead we
auto-apply the confident cases, log everything for after-the-fact review, and
spend the LLM judge only on real disagreements. Because `temporal_memory` is
immutable + additive, any wrong auto-action is correctable by a later assertion
(audit-and-correct, not pre-gate).

1. **Canonicalize `(entity, predicate)` — three bands, two thresholds:**
   - fold case/whitespace, strip a small stopword set;
   - **exact** alias-map hit (`fact_aliases.json`, additive, human-curated) or
     normalized-equal → **auto-merge**;
   - **embedding cosine** (via `knowledge_index.embed`, `BAAI/bge-small-en-v1.5`)
     against existing canonical keys:
     - `cosine ≥ HIGH` (default **0.90**) → **auto-merge**, logged to the audit report;
     - `LOW ≤ cosine < HIGH` (default **0.75**) → **review band** — the only merges a
       human sees; held only when `--strict-merges` is passed, otherwise treated as
       distinct and logged for later review;
     - `cosine < LOW` → distinct, silently.
   Thresholds tunable via `--merge-high` / `--merge-low`.
2. **Candidate priors**: select `memory_assertions` sharing the resolved
   `(entity, predicate)`.
3. **Linking — deterministic recency, LLM judge on disagreement:**
   - value-identical prior → **no link** (idempotent re-ingest);
   - later assertion (`new.asserted_at > prior.asserted_at`), different value,
     **orderable** → **auto-`supersedes`** (this *is* "recency wins"; logged, no prompt);
   - **not cleanly orderable** — same/undecidable `asserted_at` with different values,
     i.e. a genuine **disagreement** → invoke the **LLM judge** (agentic, batched over
     only the conflicting set): it decides `supersedes` (one clearly corrects the other),
     `contradicts` (genuine unresolved conflict → `conflicted`), or keep-both. The judge
     runs *only* on this small disagreement set, not per link.
4. **Constraint filter** (deterministic, enforces `temporal_memory`'s rules): a link is
   only emitted when `new.asserted_at >= prior.asserted_at` (the linking assertion cannot
   be older). A genuinely *older* late-ingested fact creates **no link** and relies on
   recency resolution in `current_fact` (matches the existing
   `late_ingestion_of_an_older_meeting` behavior). Equal authority satisfies the supersede
   authority rule.
5. **Audit + gate**: every auto merge and auto `supersedes`, plus every judge decision,
   is written to `fact_intake_report.json` (+ a stderr summary). `--dry-run` (default in
   the standalone CLI) prints the report and writes nothing; `--apply` builds the
   `schema_version: "1.0"` ledger and calls `temporal_memory.load_ledger` in one
   transaction. `--strict-merges` additionally holds the review-band merges for human
   confirmation instead of treating them as distinct.

**assertion_id** = short hash of `canonical_entity | canonical_predicate | value |
source | segment_id` — stable and idempotent (re-running the same corpus is a
no-op, guaranteed by `_insert_immutable`'s content check).

## 8. Resolution semantics (unchanged, by design)

- `current_fact(entity, predicate, as_of)` → recency + supersede resolution; two
  active values that only `contradict` → `status: "conflicted"`, surfaced not
  auto-picked.
- All `authority = 0` (equal). No doc-vs-call precedence beyond time order.

## 9. Integration with the joined intake

- **onboard**: add a stage after indexing (after stage 3/4) —
  `fact_prep → agents → fact_write --apply` over the whole indexed corpus. Skipped
  when no chunks. Ordered after taxonomy classify so both lanes see the same chunks.
- **Incremental updates** (`brain_sync`/`--corpus` re-index): `fact_prep` accepts
  `--docs`/`--chunks` (same flags as `classify_prep`) so an update re-extracts only
  changed sources; idempotent ids keep re-runs safe.
- **Corpus scope**: default = all chunks (docs + vtts). `--sources vtt,srt` optional
  filter when a run should target calls only.

## 10. Error handling

- Malformed agent JSON (extraction or adjudication): skip that item with a stderr
  warning; never abort the batch (matches `classify_write`/`extract_facts`).
- `load_ledger` already raises on unknown link targets, cross-entity links, and
  older→newer links; `fact_write`'s constraint filter prevents the last two so
  valid runs don't hit them.
- Dry-run is the default; nothing writes without `--apply`.
- Embedding model unavailable → skip the embedding-suggested-merge step (alias map
  + exact canonical still work); warn once.

## 11. Testing (all under `bundles/brain/tests/`)

- `fact_schema`: id derivation stability/idempotency; ledger builder shape.
- `fact_prep`: batch/instruction generation; `--docs`/`--chunks`/`--sources` filters;
  event_date/speaker passthrough from chunk rows.
- `fact_write` normalization: case/stopword fold, alias-map hit, embedding-threshold
  merge suggestion (embed monkeypatched), new-key path.
- `fact_write` merge tiers: exact/alias auto-merge; cosine ≥ HIGH auto (embed
  monkeypatched); LOW≤cosine<HIGH review-band (distinct unless `--strict-merges`);
  cosine < LOW distinct.
- `fact_write` linking: value-identical → no link; later+orderable → auto-supersedes;
  disagreement (same/undecidable time) → LLM judge invoked (judge monkeypatched) →
  supersedes/contradicts/keep-both; older-than-prior → no link; malformed judge JSON
  → leave conflicted, warn.
- `fact_write` audit + gate: `fact_intake_report.json` lists every auto merge/supersede
  + judge decision; `--dry-run` writes nothing; `--apply` round-trips through
  `load_ledger` then `current_fact`/`question_status` return expected current + history +
  conflicted states.
- `temporal_memory` migration: `ensure_schema` on a pre-existing DB adds
  `evidence`/`sentiment`/`stance`; `load_ledger` stores and re-reads them; old
  ledgers without the fields still load.

## 12. Decisions (resolved)

1. Colocation in `knowledge-index` — **agreed** (joined intake: docs + VTTs, same as retrieval).
2. Evidence stored inline — **agreed**.
3. Sentiment = `positive|neutral|negative|mixed` + optional **free-text** `stance` — **agreed**.
4. Entity/predicate merge is **confidence-tiered auto** (auto ≥ 0.90, review band 0.75–0.90,
   distinct < 0.75), not suggest-only — **agreed** (suggest-only overwhelms the human).
5. Linking is **deterministic recency** for clean cases (auto-supersedes) with an **LLM judge
   only on genuine disagreements** — **agreed**.
6. Thresholds default `HIGH=0.90`, `LOW=0.75`, tunable — **agreed**.
