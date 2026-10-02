import json
import sqlite3
from pathlib import Path

import pytest

import classify_prep as C

VTT = "Team_SprintDemo/standup.vtt.md"
PDF = "acme/report.pdf.md"


def _vtt_text() -> str:
    return ("Speaker A: " + "ok sure. " * 200)[:1000]


def _pdf_text() -> str:
    return "\n".join(f"line{i:03d}   alpha  beta" for i in range(100))[:1000]


def _marker_text() -> str:
    lead = ("Speaker A: fine thanks.\n" * 30)[:450]
    return lead + " ZEBRA_MARKER_TERM " + "and then some more talk. " * 10


def _fixture(tmp_path: Path):
    db = tmp_path / "k.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS chunks(id INTEGER, source TEXT, title TEXT, text TEXT)")
    con.execute("DELETE FROM chunks")
    rows = [(1, VTT, "t1", _vtt_text()), (2, VTT, "t2", _marker_text()),
            (3, PDF, "t3", _pdf_text()), (4, PDF, "t4", "short doc")]
    con.executemany("INSERT INTO chunks VALUES(?,?,?,?)", rows)
    con.commit()
    con.close()
    tax = tmp_path / "t.json"
    tax.write_text(json.dumps({"intent_taxonomy": {"l1": ["A"], "tree": {"A": []}}}))
    return db, tax, rows


def _run(tmp_path: Path, *extra: str) -> dict[int, str]:
    db, tax, _ = _fixture(tmp_path)
    out = tmp_path / "o"
    C.main(["--db", str(db), "--taxonomy", str(tax), "--out", str(out), *extra])
    return {it["id"]: it["preview"] for b in sorted(out.glob("batch_*.json")) for it in json.loads(b.read_text())}


def collapse(s: str) -> str:
    return " ".join(s.split())


def test_transcript_chunk_arrives_whole(tmp_path):
    got = _run(tmp_path)
    assert len(_vtt_text()) == 1000
    assert got[1] == collapse(_vtt_text())


def test_marker_past_400_chars_is_visible_in_transcript(tmp_path):
    got = _run(tmp_path)
    assert "ZEBRA_MARKER_TERM" in got[2]


def test_document_chunk_cut_at_400_raw_chars(tmp_path):
    got = _run(tmp_path)
    assert got[3] == collapse(_pdf_text()[:400])


def test_preview_zero_is_full_text_and_negative_is_rejected(tmp_path):
    got = _run(tmp_path, "--preview", "0")
    assert got[3] == collapse(_pdf_text())
    (tmp_path / "neg").mkdir()
    with pytest.raises(SystemExit):
        _run(tmp_path / "neg", "--preview", "-1")
    with pytest.raises(SystemExit):
        _run(tmp_path / "neg", "--transcript-preview", "-5")


def test_transcript_preview_zero_is_full_text(tmp_path):
    got = _run(tmp_path, "--transcript-preview", "0")
    assert got[2] == collapse(_marker_text())


def test_coverage_json_and_line(tmp_path, capsys):
    _, _, rows = _fixture(tmp_path)
    _run(tmp_path)
    cov = json.loads((tmp_path / "o" / "coverage.json").read_text())
    v = [len(r[3]) for r in rows if r[1] == VTT]
    d = [len(r[3]) for r in rows if r[1] == PDF]
    assert cov["preview"] == 400 and cov["transcript_preview"] == 1000
    assert cov["transcript"] == {"chunks": 2, "truncated": sum(n > 1000 for n in v),
                                 "chars_total": sum(v), "chars_seen": sum(min(n, 1000) for n in v)}
    assert cov["other"] == {"chunks": 2, "truncated": sum(n > 400 for n in d),
                            "chars_total": sum(d), "chars_seen": sum(min(n, 400) for n in d)}
    t, o = cov["transcript"], cov["other"]
    line = (f"coverage: transcript {100 * t['chars_seen'] / t['chars_total']:.0f}% of chars "
            f"({t['truncated']} of {t['chunks']} chunks truncated), "
            f"other {100 * o['chars_seen'] / o['chars_total']:.0f}% of chars "
            f"({o['truncated']} of {o['chunks']} truncated)")
    assert line in capsys.readouterr().out


def test_coverage_line_omits_absent_kind(tmp_path, capsys):
    db = tmp_path / "k.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE chunks(id INTEGER, source TEXT, title TEXT, text TEXT)")
    con.execute("INSERT INTO chunks VALUES(1,'a.pdf.md','t','hello')")
    con.commit(); con.close()
    tax = tmp_path / "t.json"
    tax.write_text(json.dumps({"intent_taxonomy": {"l1": ["A"], "tree": {"A": []}}}))
    C.main(["--db", str(db), "--taxonomy", str(tax), "--out", str(tmp_path / "o")])
    line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("coverage:")][0]
    assert "transcript" not in line and "other 100%" in line
    assert "transcript" not in json.loads((tmp_path / "o" / "coverage.json").read_text())


def test_build_items_contract():
    rows = [(7, "x.srt.md", "T", "a\n b " * 300), (8, "y.docx.md", None, None)]
    items = C.build_items(rows, preview=10, transcript_preview=0)
    assert [set(i) for i in items] == [{"id", "source", "title", "preview"}] * 2
    assert items[0]["preview"] == collapse("a\n b " * 300)
    assert items[1] == {"id": 8, "source": "y.docx.md", "title": None, "preview": ""}
    assert C.build_items(rows[:1], 10, 5)[0]["preview"] == collapse(("a\n b " * 300)[:5])


def test_coverage_contract():
    rows = [(1, "a.vtt.md", "t", "x" * 1500), (2, "b.pdf.md", "t", "y" * 100), (3, "c.pdf.md", "t", "z" * 500)]
    assert C.coverage(rows) == {
        "transcript": {"chunks": 1, "truncated": 1, "chars_total": 1500, "chars_seen": 1000},
        "other": {"chunks": 2, "truncated": 1, "chars_total": 600, "chars_seen": 500},
        "preview": 400, "transcript_preview": 1000}
    full = C.coverage(rows, preview=0, transcript_preview=0)
    assert full["transcript"]["truncated"] == 0 and full["other"]["chars_seen"] == 600
    assert "transcript" not in C.coverage(rows[1:])


def test_no_chunks_match_removes_stale_coverage(tmp_path):
    db, tax, _ = _fixture(tmp_path)
    out = tmp_path / "o"
    C.main(["--db", str(db), "--taxonomy", str(tax), "--out", str(out)])
    assert (out / "coverage.json").exists()
    C.main(["--db", str(db), "--taxonomy", str(tax), "--out", str(out), "--docs", "nothing/here.pdf.md"])
    assert not (out / "coverage.json").exists()
