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
