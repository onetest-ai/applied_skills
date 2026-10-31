"""Leftovers: the sentences of a meeting that sound like work and that no applied event quotes. For a long meeting,
`done` needs the token `leftovers` issued for the current state, so the reader has looked at the list after its
last apply (Oct 31: a 20,000-word readout with 21 key items yielded 12 events and passed the coverage gate)."""
import textwrap

from conftest import run_cli
from taska_lib import config as C, leftovers as LO, store as S

EV = "2026-03-02_meetings_lo000001"
FILLER = "the dock schedule and the yard plan were discussed again in some detail by everyone present today. "
WORK = ["Nora will send the forklift certification list to the client by Thursday morning",
        "Ravi needs to book the warehouse site visit and confirm the date with Opal",
        "Opal will share the crane inspection report with the team next week"]
EXTRA = "Nora will draft the vendor shortlist for the crane tender by Friday"


def _project(tmp_path, n_filler=40, extra=True):
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
    """))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    turns = []
    for i, line in enumerate(WORK):   # each work line sits in the middle of its third
        turns.append({"speaker": "Ravi Tamsin", "ts": f"00:{i * 10:02d}:00", "text": FILLER * (n_filler // 2)})
        turns.append({"speaker": "Nora Quill", "ts": f"00:{i * 10 + 1:02d}:00", "text": line})
        if i == 1 and extra:
            turns.append({"speaker": "Nora Quill", "ts": "00:12:00", "text": EXTRA})
        turns.append({"speaker": "Ravi Tamsin", "ts": f"00:{i * 10 + 2:02d}:00", "text": FILLER * (n_filler // 2)})
    S.write_evidence(cfg.store, {"id": EV, "kind": "meeting", "at": "2026-03-02T10:00:00", "title": "Daily sync",
                                 "source": "meetings", "turns": turns})
    return cfg


def _note(cfg, quote, mid):
    S.append(cfg.store, [{"type": "note", "mid": mid, "by": "agent", "evidence": EV, "date": "2026-03-02", "node": "E01",
                          "quote": quote, "text": "x"}])


def _item(cfg):
    return S.evidence(cfg.store)[EV]


def test_marked_sentences_not_quoted_by_an_event_are_leftovers(tmp_path):
    cfg = _project(tmp_path)
    r = LO.leftovers(_item(cfg), S.events(cfg.store))
    texts = [x["text"] for p in r["parts"] for x in p["leftovers"]]
    assert texts == WORK[:2] + [EXTRA] + WORK[2:] and r["count"] == 4
    assert [p["part"] for p in r["parts"]] == [1, 2, 3] and r["parts"][1]["leftovers"][0]["speaker"] == "Nora Quill"
    assert all(FILLER.strip(". ") not in t for t in texts)


def test_a_sentence_covered_by_an_applied_quote_is_not_a_leftover(tmp_path):
    cfg = _project(tmp_path)
    _note(cfg, "send the forklift certification list to the client", "n1")
    r = LO.leftovers(_item(cfg), S.events(cfg.store))
    assert WORK[0] not in [x["text"] for p in r["parts"] for x in p["leftovers"]] and r["count"] == 3


def test_token_changes_when_an_event_is_applied(tmp_path):
    cfg = _project(tmp_path)
    before = LO.leftovers(_item(cfg), S.events(cfg.store))["token"]
    _note(cfg, WORK[1], "n1")
    after = LO.leftovers(_item(cfg), S.events(cfg.store))["token"]
    assert before != after and len(before) == 12


def test_done_on_a_long_meeting_needs_the_current_token(tmp_path):
    cfg = _project(tmp_path, n_filler=260)
    for i, line in enumerate(WORK):
        _note(cfg, line, f"n{i}")
        _note(cfg, line, f"m{i}")
    r = run_cli(tmp_path, "done", "--evidence", EV, check=False)
    assert r["status"] == "leftovers" and r["count"] == 1 and "leftovers --evidence" in r["hint"]
    assert run_cli(tmp_path, "done", "--evidence", EV, "--leftovers", "000000000000", check=False)["status"] == "leftovers"
    token = run_cli(tmp_path, "leftovers", "--evidence", EV)["token"]
    assert run_cli(tmp_path, "done", "--evidence", EV, "--leftovers", token)["status"] == "ok"
    assert EV in S.processed(S.events(cfg.store))


def test_done_on_a_long_meeting_with_no_leftovers_needs_no_token(tmp_path):
    cfg = _project(tmp_path, n_filler=260, extra=False)
    for i, line in enumerate(WORK):
        _note(cfg, line, f"n{i}")
        _note(cfg, line, f"m{i}")
    assert run_cli(tmp_path, "done", "--evidence", EV)["status"] == "ok"


def test_short_meetings_are_not_token_gated(tmp_path):
    cfg = _project(tmp_path, n_filler=40)
    for i, line in enumerate(WORK):
        _note(cfg, line, f"n{i}")
    assert run_cli(tmp_path, "leftovers", "--evidence", EV)["count"] == 1
    assert run_cli(tmp_path, "done", "--evidence", EV)["status"] == "ok"
