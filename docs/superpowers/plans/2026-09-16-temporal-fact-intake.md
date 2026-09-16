# Temporal Fact Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn corpus chunks (docs + VTT/SRT) into evidence-backed, time-ordered assertions in `temporal_memory`, so later call facts refine/correct earlier doc facts.

**Architecture:** A joined-intake stage in the `knowledge-index` skill, mirroring the repo's `classify_prep → agents → classify_write` idiom. `fact_prep.py` batches chunks for low-tier extraction agents; agents emit assertions; `fact_write.py` deterministically canonicalizes entities (confidence-tiered merge), links by recency (auto-supersedes), invokes an injected LLM judge only on genuine disagreements, writes an audit report, and loads a ledger into `temporal_memory` (which stores + resolves). Pure/deterministic helpers live in `fact_schema.py`.

**Tech Stack:** Python 3.10+, stdlib `sqlite3`, `fastembed` (via `knowledge_index.embed`), pytest. No new heavy deps.

**Spec:** `docs/superpowers/specs/2026-09-16-temporal-fact-intake-design.md`

## Global Constraints

- Python 3.10+ (no `from __future__` needed; `tuple[...]` annotations fine).
- Torch-free: embeddings only via `knowledge_index.embed(model, texts)` (`BAAI/bge-small-en-v1.5`, dim 384).
- All new tests live in `bundles/brain/tests/` (bundle-level layout); import skill modules via the shared `conftest.py` which puts every skill dir on `sys.path`. Do NOT add per-skill test dirs.
- Resolution policy: **equal authority (`authority=0`), recency wins**; `contradicts` surfaces as `conflicted`, never auto-picked.
- Merge thresholds default `HIGH=0.90`, `LOW=0.75` (flags `--merge-high`/`--merge-low`).
- `temporal_memory` is immutable + additive; never mutate/delete assertions — corrections are new assertions + links.
- Ledger `schema_version` is exactly `"1.0"`.
- Commit after each task. Run the venv python at `<VENV>` (the one with pytest/sqlite-vec/fastembed) for tests; in CI the conftest sqlite_vec stub covers no-extension environments.

---

### Task 1: Extend `temporal_memory` schema + `load_ledger` with evidence/sentiment/stance

**Files:**
- Modify: `bundles/brain/skills/knowledge-index/temporal_memory.py`
- Test: `bundles/brain/tests/test_temporal_memory_facts.py` (create)

**Interfaces:**
- Consumes: existing `ensure_schema(con)`, `load_ledger(con, ledger)`, `current_fact(con, entity, predicate, as_of=None)`.
- Produces: `memory_assertions` gains columns `evidence TEXT`, `sentiment TEXT DEFAULT 'neutral'`, `stance TEXT DEFAULT ''`; `load_ledger` reads `item["evidence"]` (default `""`), `item.get("sentiment","neutral")`, `item.get("stance","")` and stores them. `current_fact`/`question_status` return shapes unchanged.

- [ ] **Step 1: Write the failing test**

```python
# bundles/brain/tests/test_temporal_memory_facts.py
import sqlite3
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "skills" / "knowledge-index"))
import temporal_memory as T  # noqa: E402


def _ledger(**over):
    a = {"assertion_id": "a1", "entity": "go-live", "predicate": "date",
         "value": "Q2", "asserted_at": "2026-01-01T00:00:00Z",
         "ingested_at": "2026-01-02T00:00:00Z", "source": "docs/a.pdf",
         "segment_id": "chunk:1", "evidence": "go-live is Q2",
         "sentiment": "neutral", "stance": ""}
    a.update(over)
    return {"schema_version": "1.0", "assertions": [a]}


def test_load_ledger_persists_evidence_sentiment_stance():
    con = sqlite3.connect(":memory:")
    T.load_ledger(con, _ledger(sentiment="negative", stance="skeptical of Q2"))
    row = con.execute(
        "SELECT evidence, sentiment, stance FROM memory_assertions WHERE assertion_id='a1'"
    ).fetchone()
    assert row == ("go-live is Q2", "negative", "skeptical of Q2")


def test_load_ledger_defaults_when_fields_absent():
    con = sqlite3.connect(":memory:")
    a = {"assertion_id": "a2", "entity": "e", "predicate": "p", "value": "v",
         "asserted_at": "2026-01-01T00:00:00Z", "ingested_at": "2026-01-02T00:00:00Z",
         "source": "s", "segment_id": "chunk:9"}  # no evidence/sentiment/stance
    T.load_ledger(con, {"schema_version": "1.0", "assertions": [a]})
    row = con.execute("SELECT evidence, sentiment, stance FROM memory_assertions WHERE assertion_id='a2'").fetchone()
    assert row == ("", "neutral", "")


def test_ensure_schema_migrates_existing_db():
    con = sqlite3.connect(":memory:")
    # simulate an OLD memory_assertions table without the new columns
    con.execute("""CREATE TABLE memory_assertions(
      assertion_id TEXT PRIMARY KEY, entity TEXT NOT NULL, predicate TEXT NOT NULL,
      value_json TEXT NOT NULL, asserted_at TEXT NOT NULL, ingested_at TEXT NOT NULL,
      source TEXT NOT NULL, segment_id TEXT NOT NULL, authority INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'asserted')""")
    T.ensure_schema(con)
    cols = {r[1] for r in con.execute("PRAGMA table_info(memory_assertions)")}
    assert {"evidence", "sentiment", "stance"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_temporal_memory_facts.py -v`
Expected: FAIL (no such column: evidence).

- [ ] **Step 3: Implement the migration + load_ledger fields**

In `temporal_memory.py`: add the three columns to the `memory_assertions` `CREATE TABLE` in `SCHEMA`. Then, in `ensure_schema`, after running `SCHEMA`, add a guarded migration:

```python
def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA foreign_keys=ON")
    for stmt in (s.strip() for s in SCHEMA.split(";") if s.strip()):
        con.execute(stmt)
    cols = {r[1] for r in con.execute("PRAGMA table_info(memory_assertions)")}
    for col, decl in (("evidence", "TEXT"), ("sentiment", "TEXT"), ("stance", "TEXT")):
        if col not in cols:
            con.execute(f"ALTER TABLE memory_assertions ADD COLUMN {col} {decl}")
```

In `load_ledger`, extend the `memory_assertions` column list and values with `evidence`, `sentiment`, `stance`:

```python
["assertion_id","entity","predicate","value_json","asserted_at","ingested_at",
 "source","segment_id","authority","status","evidence","sentiment","stance"],
[ item["assertion_id"], item["entity"], item["predicate"],
  json.dumps(item["value"], sort_keys=True, separators=(",", ":")),
  _timestamp(item["asserted_at"]), _timestamp(item["ingested_at"]),
  item["source"], item["segment_id"], int(item.get("authority", 0)),
  item.get("status", "asserted"),
  item.get("evidence", ""), item.get("sentiment", "neutral"), item.get("stance", "") ],
```

(The new columns are included in `_insert_immutable`'s content-equality check automatically since they are in the column list.)

- [ ] **Step 4: Run test to verify it passes**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_temporal_memory_facts.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the existing temporal test to confirm no regression**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_temporal_memory.py -v`
Expected: PASS (unchanged).

- [ ] **Step 6: Commit**

```bash
git add bundles/brain/skills/knowledge-index/temporal_memory.py bundles/brain/tests/test_temporal_memory_facts.py
git commit -m "feat(temporal): add evidence/sentiment/stance columns + guarded migration"
```

---

### Task 2: `fact_schema.py` — id derivation, canonicalization, ledger builder

**Files:**
- Create: `bundles/brain/skills/knowledge-index/fact_schema.py`
- Create: `bundles/brain/skills/knowledge-index/fact_aliases.json` (seed, `{}`)
- Test: `bundles/brain/tests/test_fact_schema.py`

**Interfaces:**
- Produces:
  - `SENTIMENTS: frozenset[str]` = `{"positive","neutral","negative","mixed"}`
  - `normalize(text: str) -> str` — lowercase, collapse whitespace, strip a small stopword set (`{"the","a","an","of","for","to","is","are"}`) and trailing punctuation.
  - `canon_key(entity: str, predicate: str, aliases: dict[str,str]) -> tuple[str,str]` — apply `normalize` to each, then map through `aliases` (key = `"<norm_entity>|<norm_predicate>"`, value = `"<canon_entity>|<canon_predicate>"`).
  - `assertion_id(entity: str, predicate: str, value, source: str, segment_id: str) -> str` — `hashlib.sha1` hex, first 16 chars, over `"|".join([entity,predicate,json.dumps(value,sort_keys=True),source,segment_id])`.
  - `build_ledger(assertions: list[dict], links: list[tuple[str,str,str]]) -> dict` — returns `{"schema_version":"1.0","assertions":[...]}`; each link `(src_id, relation, tgt_id)` appends `tgt_id` to `assertions[src].setdefault(relation, [])`.

- [ ] **Step 1: Write the failing test**

```python
# bundles/brain/tests/test_fact_schema.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "skills" / "knowledge-index"))
import fact_schema as F  # noqa: E402


def test_normalize_folds_case_ws_stopwords():
    assert F.normalize("  The  Go-Live   DATE ") == "go-live date"


def test_canon_key_applies_alias():
    aliases = {"deployment|date": "go-live|date"}
    assert F.canon_key("Deployment", "date", aliases) == ("go-live", "date")


def test_canon_key_without_alias_is_normalized():
    assert F.canon_key("Go-Live", "Date", {}) == ("go-live", "date")


def test_assertion_id_is_stable_and_value_sensitive():
    a = F.assertion_id("go-live", "date", "Q2", "docs/a.pdf", "chunk:1")
    b = F.assertion_id("go-live", "date", "Q2", "docs/a.pdf", "chunk:1")
    c = F.assertion_id("go-live", "date", "Q3", "docs/a.pdf", "chunk:1")
    assert a == b and a != c and len(a) == 16


def test_build_ledger_attaches_links():
    assertions = [{"assertion_id": "n1"}, {"assertion_id": "o1"}]
    led = F.build_ledger(assertions, [("n1", "supersedes", "o1")])
    assert led["schema_version"] == "1.0"
    n1 = [a for a in led["assertions"] if a["assertion_id"] == "n1"][0]
    assert n1["supersedes"] == ["o1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_fact_schema.py -v`
Expected: FAIL (no module named fact_schema).

- [ ] **Step 3: Implement `fact_schema.py`**

```python
#!/usr/bin/env python3
"""Pure, deterministic helpers for temporal fact intake (no I/O, no LLM)."""
import hashlib, json, re

SENTIMENTS = frozenset({"positive", "neutral", "negative", "mixed"})
_STOP = {"the", "a", "an", "of", "for", "to", "is", "are"}


def normalize(text: str) -> str:
    toks = re.findall(r"[a-z0-9][a-z0-9'-]*", (text or "").lower())
    return " ".join(t for t in toks if t not in _STOP)


def canon_key(entity: str, predicate: str, aliases: dict) -> tuple[str, str]:
    e, p = normalize(entity), normalize(predicate)
    mapped = aliases.get(f"{e}|{p}")
    if mapped and "|" in mapped:
        e, p = mapped.split("|", 1)
    return e, p


def assertion_id(entity: str, predicate: str, value, source: str, segment_id: str) -> str:
    raw = "|".join([entity, predicate, json.dumps(value, sort_keys=True), source, segment_id])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def build_ledger(assertions: list, links: list) -> dict:
    by_id = {a["assertion_id"]: a for a in assertions}
    for src_id, relation, tgt_id in links:
        by_id[src_id].setdefault(relation, []).append(tgt_id)
    return {"schema_version": "1.0", "assertions": assertions}
```

Also create `fact_aliases.json` containing `{}`.

- [ ] **Step 4: Run test to verify it passes**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_fact_schema.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add bundles/brain/skills/knowledge-index/fact_schema.py bundles/brain/skills/knowledge-index/fact_aliases.json bundles/brain/tests/test_fact_schema.py
git commit -m "feat(facts): fact_schema helpers (normalize, canon_key, assertion_id, build_ledger)"
```

---

### Task 3: `fact_prep.py` — batch chunks for extraction agents

**Files:**
- Create: `bundles/brain/skills/knowledge-index/fact_prep.py`
- Test: `bundles/brain/tests/test_fact_prep.py`

**Interfaces:**
- Consumes: `chunks(id, source, title, text, speaker, event_date)` in the knowledge SQLite.
- Produces: CLI `fact_prep.py --db <db> --out <dir> [--batches 5] [--preview 400] [--docs csv] [--chunks csv] [--sources vtt,srt]`. Writes `<out>/instructions.md` and `<out>/batch_<k>.json` where each item is `{"id":int,"source":str,"title":str,"event_date":str|None,"speaker":str|None,"preview":str}`. `--sources` filters by file extension of `source`. Reuses the SQL filter idiom from `classify_prep.py`.

- [ ] **Step 1: Write the failing test**

```python
# bundles/brain/tests/test_fact_prep.py
import json, sqlite3, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "skills" / "knowledge-index"))
import fact_prep as P  # noqa: E402


def _db(tmp_path):
    db = tmp_path / "k.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY, source TEXT, title TEXT, text TEXT, speaker TEXT, event_date TEXT)")
    con.executemany("INSERT INTO chunks VALUES(?,?,?,?,?,?)", [
        (1, "calls/a.vtt", "Intro", "We shipped v2 on Friday.", "Alex", "2026-09-14"),
        (2, "docs/a.pdf", "Plan", "Go-live targeted for Q2.", None, "2026-01-01"),
    ])
    con.commit(); con.close()
    return db


def test_prep_writes_batches_and_instructions(tmp_path):
    db = _db(tmp_path); out = tmp_path / "out"
    P.main(["--db", str(db), "--out", str(out), "--batches", "1"])
    assert (out / "instructions.md").exists()
    items = json.loads((out / "batch_0.json").read_text())
    ids = {i["id"] for i in items}
    assert ids == {1, 2}
    a = [i for i in items if i["id"] == 1][0]
    assert a["speaker"] == "Alex" and a["event_date"] == "2026-09-14"


def test_prep_sources_filter_vtt_only(tmp_path):
    db = _db(tmp_path); out = tmp_path / "out"
    P.main(["--db", str(db), "--out", str(out), "--batches", "1", "--sources", "vtt,srt"])
    items = json.loads((out / "batch_0.json").read_text())
    assert {i["id"] for i in items} == {1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_fact_prep.py -v`
Expected: FAIL (no module named fact_prep).

- [ ] **Step 3: Implement `fact_prep.py`**

```python
#!/usr/bin/env python3
"""Batch corpus chunks + instructions for LOW-TIER fact-extraction agents.

Open-category extraction: pull any salient fact (WHO/WHAT + concrete action,
decision, risk, finding, date, number, status). Skip questions and chit-chat.
Agents write result_<k>.json: [{chunk_id, entity, predicate, value, evidence,
sentiment, stance}]. fact_write.py consumes those.
"""
import argparse, json, os, sqlite3

INSTRUCTIONS = """# Extract facts from each chunk (open-category)

For every chunk, extract 0-N assertions it genuinely states. Each assertion:
- entity: the thing the fact is about (short noun phrase)
- predicate: the attribute/relation (short)
- value: the asserted value (string)
- evidence: a verbatim quote (<=200 chars) from the chunk supporting it
- sentiment: one of positive|neutral|negative|mixed
- stance: optional short stance toward the entity (may be "")

QUALITY BAR: every assertion must state WHO/WHAT + a concrete action, decision,
risk, finding, date, number, or status. Skip questions, hedges, greetings,
scheduling and chit-chat. If a chunk states nothing concrete, emit nothing.

Output ONE JSON file result_<k>.json: a list of objects, each including the
integer "chunk_id" it came from. Example:
[{"chunk_id": 1, "entity": "v2", "predicate": "ship date", "value": "Friday",
  "evidence": "We shipped v2 on Friday.", "sentiment": "positive", "stance": ""}]
"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--batches", type=int, default=5); ap.add_argument("--preview", type=int, default=400)
    ap.add_argument("--docs"); ap.add_argument("--chunks"); ap.add_argument("--sources")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "instructions.md"), "w") as f:
        f.write(INSTRUCTIONS)
    con = sqlite3.connect(a.db)
    where, params = [], [a.preview]
    if a.docs:
        d = [s.strip() for s in a.docs.split(",") if s.strip()]
        where.append("source IN (" + ",".join("?" * len(d)) + ")"); params += d
    elif a.chunks:
        ids = [int(x) for x in a.chunks.split(",") if x.strip()]
        where.append("id IN (" + ",".join("?" * len(ids)) + ")"); params += ids
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = con.execute(
        f"SELECT id, source, title, substr(text,1,?), speaker, event_date FROM chunks{clause} ORDER BY id",
        params).fetchall()
    if a.sources:
        exts = tuple("." + s.strip().lower().lstrip(".") for s in a.sources.split(",") if s.strip())
        rows = [r for r in rows if str(r[1]).lower().endswith(exts)]
    if not rows:
        print(f"no chunks match -> {a.out}"); return
    n = max(1, a.batches); size = (len(rows) + n - 1) // n
    for k in range(n):
        batch = rows[k*size:(k+1)*size]
        if not batch: break
        items = [{"id": r[0], "source": r[1], "title": r[2],
                  "event_date": r[5], "speaker": r[4],
                  "preview": " ".join((r[3] or "").split())} for r in batch]
        json.dump(items, open(os.path.join(a.out, f"batch_{k}.json"), "w"), indent=1)
    print(f"prepared {len(rows)} chunks -> {a.out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_fact_prep.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add bundles/brain/skills/knowledge-index/fact_prep.py bundles/brain/tests/test_fact_prep.py
git commit -m "feat(facts): fact_prep batches corpus chunks for extraction agents"
```

---

### Task 4: `fact_write.py` — entity/predicate resolution (tiered merge)

**Files:**
- Create: `bundles/brain/skills/knowledge-index/fact_write.py`
- Test: `bundles/brain/tests/test_fact_write_resolve.py`

**Interfaces:**
- Consumes: `fact_schema.canon_key`, `fact_schema.normalize`, `knowledge_index.embed` (injected as `embed_fn` for testability).
- Produces: `resolve_key(entity, predicate, existing_keys, aliases, embed_fn, high, low) -> tuple[tuple[str,str], str]` returning `((canon_entity, canon_predicate), band)` where `band ∈ {"exact","auto","review","distinct"}`. `existing_keys` is a list of `(canon_entity, canon_predicate)` tuples already in the store. Cosine is computed on the `"<e> <p>"` string vs each existing key's `"<e> <p>"`. `exact` when alias/normalized-equal to an existing key; `auto` when max cosine ≥ high; `review` when low ≤ max cosine < high; else `distinct`. When `auto`/`review`/`exact` matches an existing key, the returned canon pair is that **existing** key (merge target); `distinct` returns the chunk's own normalized key.

- [ ] **Step 1: Write the failing test**

```python
# bundles/brain/tests/test_fact_write_resolve.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "skills" / "knowledge-index"))
import fact_write as W  # noqa: E402


def _fake_embed(pairs):
    # deterministic stub: identical strings -> vector [1,0]; contains "deploy"/"go-live" -> near
    def emb(model, texts):
        out = []
        for t in texts:
            if "go-live" in t or "deploy" in t:
                out.append([1.0, 0.05])
            elif "budget" in t:
                out.append([0.0, 1.0])
            else:
                out.append([0.3, 0.3])
        return out
    return emb


def test_exact_normalized_match_is_exact_band():
    key, band = W.resolve_key("Go-Live", "date", [("go-live", "date")], {}, _fake_embed(None), 0.90, 0.75)
    assert band == "exact" and key == ("go-live", "date")


def test_alias_maps_to_existing_key():
    key, band = W.resolve_key("Deployment", "date", [("go-live", "date")],
                              {"deployment|date": "go-live|date"}, _fake_embed(None), 0.90, 0.75)
    assert band == "exact" and key == ("go-live", "date")


def test_high_cosine_auto_merges_to_existing():
    key, band = W.resolve_key("deploy window", "date", [("go-live", "date")], {}, _fake_embed(None), 0.90, 0.75)
    assert band == "auto" and key == ("go-live", "date")


def test_low_cosine_is_distinct():
    key, band = W.resolve_key("budget", "amount", [("go-live", "date")], {}, _fake_embed(None), 0.90, 0.75)
    assert band == "distinct" and key == ("budget", "amount")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_fact_write_resolve.py -v`
Expected: FAIL (no module named fact_write / no resolve_key).

- [ ] **Step 3: Implement `resolve_key` (and module skeleton) in `fact_write.py`**

```python
#!/usr/bin/env python3
"""Resolve entities, link by recency, judge disagreements, emit + load a ledger."""
import argparse, json, math, os, sqlite3, sys
import fact_schema as FS


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def resolve_key(entity, predicate, existing_keys, aliases, embed_fn,
                high, low, model="BAAI/bge-small-en-v1.5"):
    cand = FS.canon_key(entity, predicate, aliases)
    if cand in existing_keys:
        return cand, "exact"
    if not existing_keys:
        return cand, "distinct"
    texts = [f"{cand[0]} {cand[1]}"] + [f"{e} {p}" for (e, p) in existing_keys]
    vecs = embed_fn(model, texts)
    q, rest = vecs[0], vecs[1:]
    sims = [(_cos(q, v), existing_keys[i]) for i, v in enumerate(rest)]
    best_sim, best_key = max(sims, key=lambda t: t[0])
    if best_sim >= high:
        return best_key, "auto"
    if best_sim >= low:
        return best_key, "review"
    return cand, "distinct"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_fact_write_resolve.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add bundles/brain/skills/knowledge-index/fact_write.py bundles/brain/tests/test_fact_write_resolve.py
git commit -m "feat(facts): fact_write entity/predicate tiered merge (resolve_key)"
```

---

### Task 5: `fact_write.py` — candidate priors + deterministic recency linking

**Files:**
- Modify: `bundles/brain/skills/knowledge-index/fact_write.py`
- Test: `bundles/brain/tests/test_fact_write_link.py`

**Interfaces:**
- Consumes: `resolve_key` (Task 4).
- Produces:
  - `find_priors(con, entity, predicate) -> list[dict]` — rows from `memory_assertions` with matching entity+predicate, each `{"assertion_id","value","asserted_at"}` (value decoded from `value_json`).
  - `plan_links(new_id, new_value, new_at, priors) -> tuple[list[tuple], list[dict]]` — returns `(auto_links, disagreements)`. For each prior: same value → skip; `new_at > prior_at` and different value → `(new_id,"supersedes",prior_id)` auto-link; `new_at == prior_at` (or unorderable) and different value → a disagreement dict `{"new_id","prior_id","new_value","prior_value"}`; `new_at < prior_at` → skip (no link; recency resolution handles it).

- [ ] **Step 1: Write the failing test**

```python
# bundles/brain/tests/test_fact_write_link.py
import sqlite3, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "skills" / "knowledge-index"))
import fact_write as W  # noqa: E402
import temporal_memory as T  # noqa: E402


def _con_with_prior(value="Q2", at="2026-01-01T00:00:00Z"):
    con = sqlite3.connect(":memory:")
    T.load_ledger(con, {"schema_version": "1.0", "assertions": [{
        "assertion_id": "old", "entity": "go-live", "predicate": "date", "value": value,
        "asserted_at": at, "ingested_at": at, "source": "docs/a.pdf", "segment_id": "chunk:2"}]})
    return con


def test_find_priors_returns_matching():
    con = _con_with_prior()
    priors = W.find_priors(con, "go-live", "date")
    assert priors[0]["assertion_id"] == "old" and priors[0]["value"] == "Q2"


def test_later_different_value_auto_supersedes():
    con = _con_with_prior()
    priors = W.find_priors(con, "go-live", "date")
    auto, dis = W.plan_links("new", "Q3", "2026-09-14T00:00:00Z", priors)
    assert auto == [("new", "supersedes", "old")] and dis == []


def test_same_value_no_link():
    con = _con_with_prior(value="Q2")
    priors = W.find_priors(con, "go-live", "date")
    auto, dis = W.plan_links("new", "Q2", "2026-09-14T00:00:00Z", priors)
    assert auto == [] and dis == []


def test_same_time_conflict_is_disagreement():
    con = _con_with_prior(at="2026-09-14T00:00:00Z")
    priors = W.find_priors(con, "go-live", "date")
    auto, dis = W.plan_links("new", "Q3", "2026-09-14T00:00:00Z", priors)
    assert auto == [] and dis and dis[0]["prior_id"] == "old"


def test_older_than_prior_no_link():
    con = _con_with_prior(at="2026-09-14T00:00:00Z")
    priors = W.find_priors(con, "go-live", "date")
    auto, dis = W.plan_links("new", "Q1", "2026-01-01T00:00:00Z", priors)
    assert auto == [] and dis == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_fact_write_link.py -v`
Expected: FAIL (no find_priors/plan_links).

- [ ] **Step 3: Implement `find_priors` + `plan_links`**

```python
def find_priors(con, entity, predicate):
    rows = con.execute(
        "SELECT assertion_id, value_json, asserted_at FROM memory_assertions "
        "WHERE entity=? AND predicate=? ORDER BY asserted_at", (entity, predicate)).fetchall()
    return [{"assertion_id": r[0], "value": json.loads(r[1]), "asserted_at": r[2]} for r in rows]


def plan_links(new_id, new_value, new_at, priors):
    auto, disagreements = [], []
    for p in priors:
        if p["value"] == new_value:
            continue
        if new_at > p["asserted_at"]:
            auto.append((new_id, "supersedes", p["assertion_id"]))
        elif new_at == p["asserted_at"]:
            disagreements.append({"new_id": new_id, "prior_id": p["assertion_id"],
                                  "new_value": new_value, "prior_value": p["value"]})
        # new_at < prior: no link; recency resolution in current_fact handles it
    return auto, disagreements
```

(Timestamps are ISO-8601 `...Z` strings normalized by `temporal_memory._timestamp`; lexical `>`/`==` on that canonical form is correct chronological order. `fact_write` will normalize `new_at` via `temporal_memory._timestamp` before calling `plan_links` — see Task 7.)

- [ ] **Step 4: Run test to verify it passes**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_fact_write_link.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add bundles/brain/skills/knowledge-index/fact_write.py bundles/brain/tests/test_fact_write_link.py
git commit -m "feat(facts): candidate priors + deterministic recency linking"
```

---

### Task 6: `fact_write.py` — LLM judge on disagreements (injected callable)

**Files:**
- Modify: `bundles/brain/skills/knowledge-index/fact_write.py`
- Test: `bundles/brain/tests/test_fact_write_judge.py`

**Interfaces:**
- Consumes: `plan_links` disagreement dicts (Task 5).
- Produces: `judge_disagreements(disagreements, judge_fn) -> tuple[list[tuple], list[dict]]` returning `(links, unresolved)`. `judge_fn(dis: dict) -> dict` returns `{"relation": "supersedes"|"contradicts"|"keep_both"}`. `supersedes`/`contradicts` → append `(new_id, relation, prior_id)` to links; `keep_both` → no link. Malformed/raised `judge_fn` → leave in `unresolved` (surfaced; becomes a natural `conflicted` state since both assertions stay active). A default `bedrock_judge` factory mirrors `extract_facts._make_bedrock_llm`; tests inject a stub.

- [ ] **Step 1: Write the failing test**

```python
# bundles/brain/tests/test_fact_write_judge.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "skills" / "knowledge-index"))
import fact_write as W  # noqa: E402


def test_judge_supersedes_becomes_link():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    links, unresolved = W.judge_disagreements(dis, lambda d: {"relation": "supersedes"})
    assert links == [("n", "supersedes", "o")] and unresolved == []


def test_judge_contradicts_becomes_link():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    links, _ = W.judge_disagreements(dis, lambda d: {"relation": "contradicts"})
    assert links == [("n", "contradicts", "o")]


def test_judge_keep_both_no_link():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    links, unresolved = W.judge_disagreements(dis, lambda d: {"relation": "keep_both"})
    assert links == [] and unresolved == []


def test_judge_error_is_unresolved():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    def boom(d): raise RuntimeError("llm down")
    links, unresolved = W.judge_disagreements(dis, boom)
    assert links == [] and unresolved == dis
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_fact_write_judge.py -v`
Expected: FAIL (no judge_disagreements).

- [ ] **Step 3: Implement `judge_disagreements`**

```python
def judge_disagreements(disagreements, judge_fn):
    links, unresolved = [], []
    for d in disagreements:
        try:
            verdict = judge_fn(d)
            rel = verdict.get("relation")
        except Exception as exc:  # noqa: BLE001 — judge failures must not abort intake
            print(f"[warn] judge failed for {d['new_id']}->{d['prior_id']}: {exc}", file=sys.stderr)
            unresolved.append(d); continue
        if rel in ("supersedes", "contradicts"):
            links.append((d["new_id"], rel, d["prior_id"]))
        elif rel == "keep_both":
            pass
        else:
            unresolved.append(d)
    return links, unresolved
```

- [ ] **Step 4: Run test to verify it passes**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_fact_write_judge.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add bundles/brain/skills/knowledge-index/fact_write.py bundles/brain/tests/test_fact_write_judge.py
git commit -m "feat(facts): LLM judge on genuine disagreements (injected callable)"
```

---

### Task 7: `fact_write.py` — assemble assertions, audit report, gate, load (CLI)

**Files:**
- Modify: `bundles/brain/skills/knowledge-index/fact_write.py`
- Test: `bundles/brain/tests/test_fact_write_apply.py`

**Interfaces:**
- Consumes: `resolve_key`, `find_priors`, `plan_links`, `judge_disagreements`, `fact_schema.assertion_id`, `fact_schema.build_ledger`, `temporal_memory.{ensure_schema,load_ledger,current_fact,_timestamp}`, `knowledge_index.embed`.
- Produces: `run(con, results, aliases, embed_fn, judge_fn, high, low, now_iso) -> dict` building the ledger and audit report; and CLI `fact_write.py --db --results <dir> [--aliases f] [--report f] [--apply] [--strict-merges] [--merge-high 0.90] [--merge-low 0.75] [--model ...]`. For each result item: read chunk row (source, event_date, speaker) via `--db`; `asserted_at = _timestamp(event_date or now)`, `ingested_at = now`; canonicalize via `resolve_key` against existing keys pulled once from `memory_assertions`; derive `assertion_id`; compute links (recency + judge). Build ledger; write `report` JSON `{assertions:n, merges:{auto,review,exact,distinct}, auto_supersedes:n, judge:{supersedes,contradicts,keep_both,unresolved}}`. When `--apply`: `temporal_memory.load_ledger`. `sentiment` validated against `fact_schema.SENTIMENTS` (invalid → `neutral`). Malformed result JSON items skipped with a warning.

- [ ] **Step 1: Write the failing test**

```python
# bundles/brain/tests/test_fact_write_apply.py
import json, sqlite3, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "skills" / "knowledge-index"))
import fact_write as W  # noqa: E402
import temporal_memory as T  # noqa: E402


def _emb(model, texts):  # everything distinct
    return [[float(i + 1), 0.0] for i, _ in enumerate(texts)]


def _db(tmp_path):
    db = tmp_path / "k.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY, source TEXT, title TEXT, text TEXT, speaker TEXT, event_date TEXT)")
    con.executemany("INSERT INTO chunks VALUES(?,?,?,?,?,?)", [
        (1, "docs/a.pdf", "Plan", "Go-live Q2.", None, "2026-01-01"),
        (2, "calls/a.vtt", "Call", "Go-live now Q3.", "Alex", "2026-09-14"),
    ])
    con.commit(); con.close()
    return db


def test_apply_supersedes_and_current_fact(tmp_path):
    db = _db(tmp_path)
    results = tmp_path / "res"; results.mkdir()
    (results / "result_0.json").write_text(json.dumps([
        {"chunk_id": 1, "entity": "go-live", "predicate": "date", "value": "Q2",
         "evidence": "Go-live Q2.", "sentiment": "neutral", "stance": ""},
        {"chunk_id": 2, "entity": "go-live", "predicate": "date", "value": "Q3",
         "evidence": "Go-live now Q3.", "sentiment": "neutral", "stance": ""},
    ]))
    con = sqlite3.connect(db)
    report = W.run(con, str(results), {}, _emb, lambda d: {"relation": "supersedes"},
                   0.90, 0.75, now_iso="2026-09-16T00:00:00Z", apply=True)
    assert report["auto_supersedes"] >= 1
    cur = T.current_fact(con, "go-live", "date")
    assert cur["value"] == "Q3"


def test_dry_run_writes_nothing(tmp_path):
    db = _db(tmp_path)
    results = tmp_path / "res"; results.mkdir()
    (results / "result_0.json").write_text(json.dumps([
        {"chunk_id": 1, "entity": "go-live", "predicate": "date", "value": "Q2",
         "evidence": "Go-live Q2.", "sentiment": "neutral", "stance": ""}]))
    con = sqlite3.connect(db)
    W.run(con, str(results), {}, _emb, lambda d: {"relation": "supersedes"},
          0.90, 0.75, now_iso="2026-09-16T00:00:00Z", apply=False)
    T.ensure_schema(con)
    assert con.execute("SELECT COUNT(*) FROM memory_assertions").fetchone()[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_fact_write_apply.py -v`
Expected: FAIL (no run()).

- [ ] **Step 3: Implement `run()` + CLI `main()`**

```python
def _load_results(results_dir):
    items = []
    for fn in sorted(os.listdir(results_dir)):
        if fn.startswith("result_") and fn.endswith(".json"):
            try:
                items += json.load(open(os.path.join(results_dir, fn)))
            except (ValueError, OSError) as exc:
                print(f"[warn] skip {fn}: {exc}", file=sys.stderr)
    return items


def run(con, results, aliases, embed_fn, judge_fn, high, low, now_iso, apply=False,
        strict_merges=False, model="BAAI/bge-small-en-v1.5"):
    import temporal_memory as T
    T.ensure_schema(con)
    existing = list({(r[0], r[1]) for r in con.execute(
        "SELECT DISTINCT entity, predicate FROM memory_assertions")})
    bands = {"exact": 0, "auto": 0, "review": 0, "distinct": 0}

    # Pass 1: parse + canonicalize every item into a candidate assertion.
    assertions = []
    for it in _load_results(results):
        try:
            cid = int(it["chunk_id"]); value = it["value"]
            ent_in, pred_in = it["entity"], it["predicate"]
        except (KeyError, TypeError, ValueError) as exc:
            print(f"[warn] skip malformed item: {exc}", file=sys.stderr); continue
        row = con.execute("SELECT source, event_date, speaker FROM chunks WHERE id=?", (cid,)).fetchone()
        if not row:
            print(f"[warn] chunk {cid} not found", file=sys.stderr); continue
        source, event_date, speaker = row
        (ce, cp), band = resolve_key(ent_in, pred_in, existing, aliases, embed_fn, high, low, model)
        if band == "review" and strict_merges:
            ce, cp = FS.canon_key(ent_in, pred_in, aliases)  # hold: treat as distinct pending review
        bands[band] += 1
        if (ce, cp) not in existing:
            existing.append((ce, cp))
        asserted_at = T._timestamp(event_date) if event_date else now_iso
        aid = FS.assertion_id(ce, cp, value, source, f"chunk:{cid}")
        sentiment = it.get("sentiment", "neutral")
        if sentiment not in FS.SENTIMENTS:
            sentiment = "neutral"
        assertions.append({
            "assertion_id": aid, "entity": ce, "predicate": cp, "value": value,
            "asserted_at": asserted_at, "ingested_at": now_iso, "source": source,
            "segment_id": f"chunk:{cid}", "evidence": it.get("evidence", ""),
            "sentiment": sentiment, "stance": it.get("stance", "")})

    # Pass 2: link in chronological order so a later fact supersedes an earlier one
    # even within the same batch. Priors = DB rows + in-batch assertions seen so far.
    assertions.sort(key=lambda a: a["asserted_at"])
    priors_cache = {}
    all_auto, disagreements = [], []
    for a in assertions:
        key = (a["entity"], a["predicate"])
        if key not in priors_cache:
            priors_cache[key] = find_priors(con, *key)  # DB priors, once per key
        auto, dis = plan_links(a["assertion_id"], a["value"], a["asserted_at"], priors_cache[key])
        all_auto += auto; disagreements += dis
        priors_cache[key].append({"assertion_id": a["assertion_id"],
                                  "value": a["value"], "asserted_at": a["asserted_at"]})
    judged, unresolved = judge_disagreements(disagreements, judge_fn)
    links = all_auto + judged
    report = {"assertions": len(assertions), "merges": bands,
              "auto_supersedes": len(all_auto),
              "judge": {"resolved": len(judged), "unresolved": len(unresolved)}}
    if apply:
        ledger = FS.build_ledger(assertions, links)
        T.load_ledger(con, ledger); con.commit()
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--results", required=True)
    ap.add_argument("--aliases"); ap.add_argument("--report")
    ap.add_argument("--apply", action="store_true"); ap.add_argument("--strict-merges", action="store_true")
    ap.add_argument("--merge-high", type=float, default=0.90); ap.add_argument("--merge-low", type=float, default=0.75)
    ap.add_argument("--model", default="BAAI/bge-small-en-v1.5")
    a = ap.parse_args(argv)
    from datetime import datetime, timezone
    import knowledge_index as KI
    aliases = json.load(open(a.aliases)) if a.aliases else {}
    judge = _bedrock_judge(a.model)
    con = sqlite3.connect(a.db)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    report = run(con, a.results, aliases, KI.embed, judge, a.merge_high, a.merge_low,
                 now_iso, apply=a.apply, strict_merges=a.strict_merges, model=a.model)
    if a.report:
        json.dump(report, open(a.report, "w"), indent=1)
    print(json.dumps(report), file=sys.stderr)


if __name__ == "__main__":
    main()
```

Add a minimal `_bedrock_judge(model)` factory that mirrors `extract_facts._make_bedrock_llm` and returns a callable `judge_fn(dis)->{"relation":...}` (prompt: given entity/predicate and two dated values + evidence, answer supersedes|contradicts|keep_both as JSON). It is only invoked in `--apply` runs with disagreements; unit tests always inject a stub, so no Bedrock in tests.

- [ ] **Step 4: Run test to verify it passes**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_fact_write_apply.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run all fact + temporal tests**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_fact_*.py bundles/brain/tests/test_temporal_memory*.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bundles/brain/skills/knowledge-index/fact_write.py bundles/brain/tests/test_fact_write_apply.py
git commit -m "feat(facts): assemble assertions, audit report, dry-run gate, ledger load"
```

---

### Task 8: Wire the fact-intake stage into `onboard.py` (joined intake)

**Files:**
- Modify: `bundles/brain/skills/knowledge-pipeline/onboard.py`
- Test: `bundles/brain/tests/test_onboard.py` (extend — assert the emitted script text includes the fact-intake stage)

**Interfaces:**
- Consumes: `fact_prep.py`, `fact_write.py` in `knowledge-index`.
- Produces: the onboard-emitted run script gains a stage after indexing/classify that runs `fact_prep → (agents) → fact_write --apply` over the indexed corpus, skipped when there are no chunks. Because agent dispatch is harness-specific (like the taxonomy map step), the emitted script includes the `fact_prep` call + a clearly-marked `🤖 agents` placeholder comment + the `fact_write --apply` call, mirroring how the existing taxonomy/classify stages are emitted.

- [ ] **Step 1: Write the failing test**

```python
# add to bundles/brain/tests/test_onboard.py
def test_run_script_includes_fact_intake_stage():
    # onboard emits a shell script; assert the fact-intake stage is present.
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        # call the same entry the other onboard tests use to render the run script;
        # match the existing test's invocation pattern in this file.
        _render_run_script_for_test()  # see existing helpers in this test module
    out = buf.getvalue()
    assert "fact_prep.py" in out and "fact_write.py" in out
```

(Adapt to the existing `test_onboard.py` invocation style — the current tests already exercise the script emission; add the assertion alongside them rather than inventing a new harness.)

- [ ] **Step 2: Run test to verify it fails**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_onboard.py -k fact_intake -v`
Expected: FAIL (stage absent).

- [ ] **Step 3: Add the stage to the emitted script in `onboard.py`**

Locate the emitted-run-script block that contains the taxonomy/classify stages (search for the `classify_prep`/`knowledge_index.py index` emission around the stage comments). After the classify stage, emit:

```
# 6 · temporal fact intake (docs + transcripts → evidence-backed assertions)
"$PY" "{KI/'fact_prep.py'}" --db "$DB" --out "{proj/'facts'}"
# 🤖 dispatch low-tier agents: read facts/instructions.md + facts/batch_*.json → facts/result_*.json
"$PY" "{KI/'fact_write.py'}" --db "$DB" --results "{proj/'facts'}" --report "{proj/'facts'/'fact_intake_report.json'}" --apply
```

Match the exact f-string/emission idiom already used for the neighbouring stages in this file (paths, quoting, `$PY`/`$DB` variables).

- [ ] **Step 4: Run test to verify it passes**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/test_onboard.py -v`
Expected: PASS.

- [ ] **Step 5: Full bundle regression**

Run: `<VENV>/bin/python -m pytest bundles/brain/tests/ -q`
Expected: PASS (all prior + new fact tests green).

- [ ] **Step 6: Commit**

```bash
git add bundles/brain/skills/knowledge-pipeline/onboard.py bundles/brain/tests/test_onboard.py
git commit -m "feat(facts): wire temporal fact intake into the joined onboard pipeline"
```

---

## Notes for the executor

- `<VENV>` = the project's test interpreter with `pytest`, `sqlite-vec`, `fastembed` (and `pandas` for `test_coverage`). The bundle-level `conftest.py` puts every skill dir on `sys.path`, so `import fact_write` / `import temporal_memory` work from `bundles/brain/tests/`.
- Keep `fact_schema.py` free of I/O and LLM calls (pure, fully unit-tested).
- Never call Bedrock in tests — always inject `embed_fn`/`judge_fn` stubs.
- SKILL.md doc update for `knowledge-index` (mention the fact-intake scripts) can ride with Task 7 or be a follow-up; it is not covered by a test and is optional for a green build.
