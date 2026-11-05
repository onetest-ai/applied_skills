"""BM25 candidate tasks for a Teams message. Invented data only."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))

from taska_lib.candidates import candidates

NODES = [
    {"id": "S001", "kind": "story", "title": "Stakeholder contact list compiled", "ac": ["List shared with the client"],
     "parent": None, "created": "2026-09-01", "state": "open"},
    {"id": "T002", "kind": "task", "title": "Book kickoff workshop room", "ac": [], "parent": "S001",
     "created": "2026-09-01", "state": "done"},
    {"id": "T003", "kind": "task", "title": "Draft billing interview questions", "ac": [], "parent": None,
     "created": "2026-10-20", "state": "open"},
    {"id": "E01", "kind": "epic", "title": "Stakeholder discovery", "ac": [], "parent": None,
     "created": "2026-09-01", "state": "open"},
]


def _msg(text, ts="2026-10-05T10:00", files=(), ctx=""):
    return {"text": text, "ts": ts, "files": list(files), "ctx": ctx}


def test_best_lexical_match_ranks_first():
    out = candidates(_msg("Final stakeholder contact list is shared with the client"), NODES, k=3)
    assert out[0][0] == "S001"


def test_epics_are_never_candidates():
    out = candidates(_msg("stakeholder discovery update"), NODES, k=8)
    assert "E01" not in [c[0] for c in out]


def test_nodes_created_after_the_message_are_excluded():
    out = candidates(_msg("billing interview questions drafted", ts="2026-10-05T10:00"), NODES, k=8)
    assert "T003" not in [c[0] for c in out]


def test_closed_nodes_stay_candidates():
    out = candidates(_msg("kickoff workshop room booked"), NODES, k=8)
    assert "T002" in [c[0] for c in out]


def test_exact_id_mention_comes_first():
    out = candidates(_msg("T002 is sorted, see above"), NODES, k=8)
    assert out[0][0] == "T002"


def test_file_names_count_as_text():
    out = candidates(_msg("", files=["stakeholder-contact-list-final.xlsx"]), NODES, k=3)
    assert out[0][0] == "S001"


def test_short_message_uses_context():
    out = candidates(_msg("Done.", ctx="can someone share the stakeholder contact list?"), NODES, k=3)
    assert out and out[0][0] == "S001"


def test_returns_rank_and_score():
    out = candidates(_msg("stakeholder contact list"), NODES, k=2)
    assert [c[2] for c in out] == list(range(len(out)))
    assert all(isinstance(c[1], float) for c in out)
