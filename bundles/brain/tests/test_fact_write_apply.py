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


def _emb_review(model, texts):
    # cosine(q, existing) = 0.8 for any pairing -> lands strictly between
    # default low=0.75 and high=0.90, i.e. the review band.
    vecs = []
    for t in texts:
        if "prior-entity" in t:
            vecs.append([1.0, 0.0])
        else:
            vecs.append([0.8, 0.6])
    return vecs


def _db_review(tmp_path):
    db = tmp_path / "k2.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY, source TEXT, title TEXT, text TEXT, speaker TEXT, event_date TEXT)")
    con.executemany("INSERT INTO chunks VALUES(?,?,?,?,?,?)", [
        (1, "docs/a.pdf", "Plan", "Prior status green.", None, "2026-01-01"),
        (2, "calls/a.vtt", "Call", "New status noted.", "Alex", "2026-09-14"),
    ])
    con.commit(); con.close()
    return db


def test_review_band_does_not_auto_merge_default(tmp_path):
    db = _db_review(tmp_path)
    con = sqlite3.connect(db)
    # Seed an existing assertion for "prior-entity"/"status" directly.
    results0 = tmp_path / "res0"; results0.mkdir()
    (results0 / "result_0.json").write_text(json.dumps([
        {"chunk_id": 1, "entity": "prior-entity", "predicate": "status", "value": "green",
         "evidence": "Prior status green.", "sentiment": "neutral", "stance": ""}]))
    W.run(con, str(results0), {}, _emb_review, lambda d: {"relation": "keep_both"},
          0.90, 0.75, now_iso="2026-01-01T00:00:00Z", apply=True)

    # Now feed a near-neighbour (review-band cosine 0.8) under a DIFFERENT own key.
    results1 = tmp_path / "res1"; results1.mkdir()
    (results1 / "result_0.json").write_text(json.dumps([
        {"chunk_id": 2, "entity": "new-entity", "predicate": "status", "value": "amber",
         "evidence": "New status noted.", "sentiment": "neutral", "stance": ""}]))
    report = W.run(con, str(results1), {}, _emb_review, lambda d: {"relation": "keep_both"},
                    0.90, 0.75, now_iso="2026-09-16T00:00:00Z", apply=True)

    assert report["merges"]["review"] == 1
    assert report["auto_supersedes"] == 0  # own key -> no prior to link against
    cur_new = T.current_fact(con, "new-entity", "status")
    assert cur_new["value"] == "amber"
    cur_prior = T.current_fact(con, "prior-entity", "status")
    assert cur_prior["value"] == "green"  # untouched, no cross-merge


def test_review_band_still_distinct_under_strict_merges(tmp_path, capsys):
    db = _db_review(tmp_path)
    con = sqlite3.connect(db)
    results0 = tmp_path / "res0"; results0.mkdir()
    (results0 / "result_0.json").write_text(json.dumps([
        {"chunk_id": 1, "entity": "prior-entity", "predicate": "status", "value": "green",
         "evidence": "Prior status green.", "sentiment": "neutral", "stance": ""}]))
    W.run(con, str(results0), {}, _emb_review, lambda d: {"relation": "keep_both"},
          0.90, 0.75, now_iso="2026-01-01T00:00:00Z", apply=True)

    results1 = tmp_path / "res1"; results1.mkdir()
    (results1 / "result_0.json").write_text(json.dumps([
        {"chunk_id": 2, "entity": "new-entity", "predicate": "status", "value": "amber",
         "evidence": "New status noted.", "sentiment": "neutral", "stance": ""}]))
    report = W.run(con, str(results1), {}, _emb_review, lambda d: {"relation": "keep_both"},
                    0.90, 0.75, now_iso="2026-09-16T00:00:00Z", apply=True, strict_merges=True)

    assert report["merges"]["review"] == 1
    cur_new = T.current_fact(con, "new-entity", "status")
    assert cur_new["value"] == "amber"  # still distinct even with --strict-merges


def test_audit_report_is_itemized_for_supersede(tmp_path):
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
    assert "merge_items" in report and "supersede_items" in report and "judge_items" in report
    assert len(report["supersede_items"]) == report["auto_supersedes"] == 1
    item = report["supersede_items"][0]
    assert item["entity"] == "go-live" and item["predicate"] == "date"
    assert item["new_value"] == "Q3" and item["prior_value"] == "Q2"
    assert "new_id" in item and "prior_id" in item


def test_reonboard_is_idempotent_noop_despite_reworded_evidence(tmp_path):
    db = _db(tmp_path)
    con = sqlite3.connect(db)
    results = tmp_path / "res"; results.mkdir()
    (results / "result_0.json").write_text(json.dumps([
        {"chunk_id": 1, "entity": "go-live", "predicate": "date", "value": "Q2",
         "evidence": "Go-live Q2.", "sentiment": "neutral", "stance": ""}]))
    report1 = W.run(con, str(results), {}, _emb, lambda d: {"relation": "supersedes"},
                     0.90, 0.75, now_iso="2026-09-16T00:00:00Z", apply=True)
    assert report1["assertions"] == 1
    cur1 = T.current_fact(con, "go-live", "date")

    # Re-run over the SAME chunk/entity/predicate/value but with reworded evidence
    # (e.g. LLM non-determinism on re-extraction) — must be a true no-op.
    results2 = tmp_path / "res2"; results2.mkdir()
    (results2 / "result_0.json").write_text(json.dumps([
        {"chunk_id": 1, "entity": "go-live", "predicate": "date", "value": "Q2",
         "evidence": "The go-live date is Q2, confirmed.", "sentiment": "neutral", "stance": ""}]))
    report2 = W.run(con, str(results2), {}, _emb, lambda d: {"relation": "supersedes"},
                     0.90, 0.75, now_iso="2026-09-17T00:00:00Z", apply=True)

    assert report2["assertions"] == 0
    cur2 = T.current_fact(con, "go-live", "date")
    assert cur2 == cur1
