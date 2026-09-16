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
