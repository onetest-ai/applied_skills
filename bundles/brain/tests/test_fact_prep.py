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
