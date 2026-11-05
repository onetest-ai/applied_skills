"""Coverage gate (r2 plan, R8): a long meeting whose last third (or any third) produced nothing is re-read before it is
marked done, or the extractor says why nothing was there. Thirds are by text offset, never by timestamp: one long
turn can hold a third of a meeting."""
import json
import textwrap

from conftest import run_cli
from taska_lib import config as C, coverage as CV, store as S

EV = "2026-03-02_meetings_cv000001"
FILLER = "the dock schedule and the yard plan were discussed again in some detail by everyone present today "
LINES = ["Nora will send the forklift certification list to the client by Thursday morning",
         "Ravi needs to book the warehouse site visit and confirm the date with Opal",
         "Opal will share the crane inspection report with the team next week"]


def _project(tmp_path, n_filler=40):
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
        [[people]]
        name = "Nora Quill"
    """))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    turns = []
    for i, line in enumerate(LINES):   # one work line at the start of each third
        turns.append({"speaker": "Nora Quill", "ts": f"00:{i * 10:02d}:00", "text": line})
        turns.append({"speaker": "Ravi Tamsin", "ts": f"00:{i * 10 + 1:02d}:00", "text": FILLER * n_filler})
    S.write_evidence(cfg.store, {"id": EV, "kind": "meeting", "at": "2026-03-02T10:00:00", "title": "Daily sync",
                                 "source": "meetings", "turns": turns})
    return cfg


def _note(cfg, quote, mid):
    S.append(cfg.store, [{"type": "note", "mid": mid, "by": "agent", "evidence": EV, "date": "2026-03-02", "node": "E01",
                          "quote": quote, "text": "x"}])


def test_coverage_counts_events_per_third_by_text_offset(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    c = CV.coverage(S.evidence(cfg.store)[EV], S.events(cfg.store))
    assert [p["events"] for p in c["parts"]] == [1, 0, 0] and c["empty_parts"] == [2, 3]


def test_done_refuses_until_acknowledged(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    r = run_cli(tmp_path, "done", "--evidence", EV, check=False)
    assert r["status"] == "coverage" and r["empty_parts"] == [2, 3]
    assert EV not in S.processed(S.events(cfg.store))
    r = run_cli(tmp_path, "done", "--evidence", EV, "--ack", "parts 2 and 3 are status talk, no new work",
                "--quote", LINES[1], "--quote", LINES[2])
    assert r["status"] == "ok"
    p = [e for e in S.events(cfg.store) if e["type"] == "processed"][-1]
    assert "coverage acknowledged: parts 2 and 3 are status talk" in p["note"]


def test_ack_without_a_quote_from_each_gap_part_is_refused(tmp_path):
    # Oct 31: five coverage refusals, five --ack "Middle part…" with no re-read in between (trajectory.py)
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    r = run_cli(tmp_path, "done", "--evidence", EV, "--ack", "status talk", check=False)
    assert r["status"] == "ack_unquoted" and r["parts_without_quote"] == [2, 3] and "--quote" in r["hint"]
    assert EV not in S.processed(S.events(cfg.store))


def test_a_quote_from_the_wrong_part_names_the_missing_part(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    r = run_cli(tmp_path, "done", "--evidence", EV, "--ack", "status talk", "--quote", LINES[1], "--quote", LINES[0],
                check=False)
    assert r["status"] == "ack_unquoted" and r["parts_without_quote"] == [3]
    unsaid = "these eight words were never said in this meeting"
    r = run_cli(tmp_path, "done", "--evidence", EV, "--ack", "status talk", "--quote", LINES[1], "--quote", unsaid, check=False)
    assert r["status"] == "ack_unquoted" and r["parts_without_quote"] == [3] and r["quotes_not_found"] == [unsaid]
    assert r["quotes_found"] == {LINES[1]: 2}


def test_part_of_locates_a_quote_by_text_offset(tmp_path):
    cfg = _project(tmp_path)
    item = S.evidence(cfg.store)[EV]
    assert [CV.part_of(item, q) for q in LINES] == [1, 2, 3] and CV.part_of(item, "never said here") == 0


def test_full_coverage_passes(tmp_path):
    cfg = _project(tmp_path)
    for i, line in enumerate(LINES):
        _note(cfg, line, f"n{i}")
    assert run_cli(tmp_path, "done", "--evidence", EV)["status"] == "ok"


def test_short_meeting_is_not_gated(tmp_path):
    cfg = _project(tmp_path, n_filler=1)
    assert run_cli(tmp_path, "done", "--evidence", EV)["status"] == "ok"


def test_extract_says_what_to_do_when_done_answers_coverage():
    from pathlib import Path
    ext = " ".join((Path(__file__).resolve().parent.parent / "skills" / "run" / "EXTRACT.md").read_text().split())
    assert '"status": "coverage"' in ext and "--ack" in ext and "re-read" in ext and "--quote" in ext


def test_a_long_meeting_with_a_thin_third_is_gated(tmp_path):
    # Oct 31: a 20,000-word readout produced 13, 1 and 3 events per third; "no empty third" let it through
    cfg = _project(tmp_path, n_filler=260)                       # ~80,000 characters: a long meeting
    for i, line in enumerate(LINES):
        _note(cfg, line, f"n{i}")
    _note(cfg, LINES[0], "n4")
    c = CV.coverage(S.evidence(cfg.store)[EV], S.events(cfg.store))
    assert [p["events"] for p in c["parts"]] == [2, 1, 1] and c["empty_parts"] == [] and c["thin_parts"] == [2, 3]
    r = run_cli(tmp_path, "done", "--evidence", EV, check=False)
    assert r["status"] == "coverage" and r["thin_parts"] == [2, 3]


def test_a_long_meeting_with_two_events_per_third_passes(tmp_path):
    cfg = _project(tmp_path, n_filler=260)
    for i, line in enumerate(LINES):
        _note(cfg, line, f"n{i}")
        _note(cfg, line, f"m{i}")
    assert CV.coverage(S.evidence(cfg.store)[EV], S.events(cfg.store))["thin_parts"] == []
    assert run_cli(tmp_path, "done", "--evidence", EV)["status"] == "ok"


def test_a_short_meeting_is_never_thin(tmp_path):
    cfg = _project(tmp_path, n_filler=40)
    for i, line in enumerate(LINES):
        _note(cfg, line, f"n{i}")
    assert CV.coverage(S.evidence(cfg.store)[EV], S.events(cfg.store))["thin_parts"] == []


def test_a_short_quote_or_the_title_does_not_acknowledge_a_part(tmp_path):
    # review: one word per part, or the title (full_text ends with it, so it always lands in the last part), passed
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    r = run_cli(tmp_path, "done", "--evidence", EV, "--ack", "x", "--quote", "forklift", "--quote", "Daily sync", check=False)
    assert r["status"] == "ack_unquoted" and r["parts_without_quote"] == [2, 3]
    assert r["quotes_too_short"] == ["forklift", "Daily sync"]
    assert CV.part_of(S.evidence(cfg.store)[EV], "Daily sync " * 4) == 0


def test_a_quote_present_in_two_parts_counts_for_the_first(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    everywhere = "the dock schedule and the yard plan were discussed again in some detail"
    r = run_cli(tmp_path, "done", "--evidence", EV, "--ack", "x", "--quote", everywhere, check=False)
    assert r["status"] == "ack_unquoted" and r["parts_without_quote"] == [2, 3] and r["quotes_found"] == {everywhere: 1}


def test_the_refusal_says_where_each_part_starts(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, LINES[0], "n1")
    r = run_cli(tmp_path, "done", "--evidence", EV, check=False)
    assert [s["part"] for s in r["starts"]] == [1, 2, 3] and r["starts"][0] == {"part": 1, "ts": "00:00:00", "speaker": "Nora Quill"}
    assert [s["ts"] for s in r["starts"]] == ["00:00:00", "00:01:00", "00:20:00"]   # the long filler turns hold the cuts
    r = run_cli(tmp_path, "done", "--evidence", EV, "--ack", "x", "--quote", LINES[1], check=False)
    assert r["status"] == "ack_unquoted" and [s["part"] for s in r["starts"]] == [1, 2, 3]
