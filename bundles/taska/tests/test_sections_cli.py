import json
import textwrap

from conftest import run_cli
from taska_lib import config as C, coverage as CV, store as S

EV = "2026-03-02_meetings_sc000001"
FILLER = "the dock schedule and the yard plan were discussed again in some detail by everyone present today "
W1 = "Nora will send the forklift certification list to the client by Thursday morning"
W2 = "Ravi needs to book the warehouse site visit and confirm the date with Opal"


def _project(tmp_path, n_filler=180, turn_pairs=2):
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
        [[people]]
        name = "Nora Quill"
    """))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    turns = []
    for i in range(turn_pairs):
        turns.append({"speaker": "Nora Quill", "ts": f"00:{i * 10:02d}:00", "text": [W1, W2][i % 2]})
        turns.append({"speaker": "Ravi Tamsin", "ts": f"00:{i * 10 + 1:02d}:00", "text": FILLER * n_filler})
    S.write_evidence(cfg.store, {"id": EV, "kind": "meeting", "at": "2026-03-02T10:00:00", "title": "Daily sync",
                                 "source": "meetings", "turns": turns})
    return cfg


def _props(tmp_path, items):
    f = tmp_path / "props.json"
    f.write_text(json.dumps(items))
    return str(f)


def _create(quote):
    return {"op": "create", "kind": "epic", "plan": "Off-plan", "title": "Send list", "quote": quote}


def test_sections_of_a_long_meeting(tmp_path):
    cfg = _project(tmp_path)
    secs = CV.sections(S.evidence(cfg.store)[EV])
    assert len(secs) >= 3
    r = run_cli(tmp_path, "sections", "--evidence", EV)
    assert r["status"] == "ok" and r["evidence"] == EV and r["md"].endswith(f"{EV}.md")
    assert [s["n"] for s in r["sections"]] == [s["n"] for s in secs]
    assert all(set(s) == {"n", "first_line", "last_line", "start_ts", "words"} for s in r["sections"])
    assert r["sections"][0]["first_line"] <= r["sections"][0]["last_line"] < r["sections"][1]["first_line"]


def test_a_short_meeting_is_one_section(tmp_path):
    _project(tmp_path, n_filler=1, turn_pairs=1)
    r = run_cli(tmp_path, "sections", "--evidence", EV)
    assert [s["n"] for s in r["sections"]] == [1]


def test_sections_unknown_evidence_is_the_usual_error(tmp_path):
    _project(tmp_path)
    r = run_cli(tmp_path, "sections", "--evidence", "nope", check=False)
    assert r["status"] == "error" and "unknown evidence" in r["error"]


def test_apply_section_out_of_range_is_an_error_and_applies_nothing(tmp_path):
    cfg = _project(tmp_path)
    f = _props(tmp_path, [_create(W1)])
    r = run_cli(tmp_path, "apply", "--evidence", EV, "--proposals", f, "--section", "9", check=False)
    assert r["status"] == "error" and "section must be 1.." in r["error"]
    assert not [e for e in S.events(cfg.store) if e.get("type") == "create"]


def test_apply_section_refuses_a_quote_from_another_section(tmp_path):
    cfg = _project(tmp_path)
    item = S.evidence(cfg.store)[EV]
    assert CV.section_of(item, W1) == 1 and CV.section_of(item, W2) == 3
    f = _props(tmp_path, [_create(W1), _create(W2)])
    r = run_cli(tmp_path, "apply", "--evidence", EV, "--proposals", f, "--section", "3")
    assert r["status"] == "ok" and r["events"] == 1
    assert r["rejected"] == [{"i": 0, "why": "outside_section", "section": 1}]


def test_apply_section_keeps_file_indices_of_apply_rejections(tmp_path):
    _project(tmp_path)
    bad = {"op": "create", "kind": "task", "title": "x", "quote": "never said in this meeting at all today"}
    f = _props(tmp_path, [_create(W1), _create(W2), dict(_create(W2), op="bogus")])
    r = run_cli(tmp_path, "apply", "--evidence", EV, "--proposals", f, "--section", "3")
    assert [(x["i"], x["why"]) for x in r["rejected"]][0] == (0, "outside_section")
    assert any(x["i"] == 2 for x in r["rejected"])
    g = _props(tmp_path, [bad])
    r = run_cli(tmp_path, "apply", "--evidence", EV, "--proposals", g, "--section", "3")
    assert [(x["i"], x["why"]) for x in r["rejected"]] == [(0, "quote not verbatim in this evidence item (one contiguous span)")]
    assert "outside_section" not in json.dumps(r)


def test_apply_without_section_is_unchanged(tmp_path):
    cfg = _project(tmp_path)
    f = _props(tmp_path, [_create(W1), _create(W2)])
    r = run_cli(tmp_path, "apply", "--evidence", EV, "--proposals", f)
    assert r["status"] == "ok" and r["events"] == 2 and r["rejected"] == []
    assert S.events(cfg.store)


def _kid(quote, parent, **kw):
    return {"op": "create", "kind": "story", "title": "Child", "ac": ["done"], "parent": parent, "quote": quote} | kw


def test_default_handles_keep_their_file_index(tmp_path):
    cfg = _project(tmp_path)
    f = _props(tmp_path, [_create(W1), dict(_create(W2), title="Second"), _kid(W2, "#0")])
    r = run_cli(tmp_path, "apply", "--evidence", EV, "--proposals", f, "--section", "3")
    assert [x["why"] for x in r["rejected"]][:1] == ["outside_section"] and r["rejected"][0]["i"] == 0
    assert r["rejected"][1]["refers_to"] == "#0" and r["rejected"][1]["i"] == 2
    made = [e for e in S.events(cfg.store) if e["type"] == "create"]
    assert [e["fields"]["title"] for e in made] == ["Second"]


def test_a_child_of_an_excluded_explicit_ref_is_refused(tmp_path):
    cfg = _project(tmp_path)
    f = _props(tmp_path, [dict(_create(W1), ref="top"), _kid(W2, "top")])
    r = run_cli(tmp_path, "apply", "--evidence", EV, "--proposals", f, "--section", "3")
    assert r["events"] == 0
    assert r["rejected"][1] == {"i": 1, "why": "outside_section", "section": 3, "refers_to": "top"}
    assert not [e for e in S.events(cfg.store) if e["type"] == "create"]


def _refused(tmp_path, entry):
    f = _props(tmp_path, [_create(W1), entry])
    r = run_cli(tmp_path, "apply", "--evidence", EV, "--proposals", f, "--section", "3")
    assert r["events"] == 0
    return r["rejected"][1]


def test_a_link_to_an_excluded_entry_is_refused_as_refers_to(tmp_path):
    _project(tmp_path)
    link = {"op": "link", "node": "T001", "depends_on": "#0", "quote": W2}
    assert _refused(tmp_path, link) == {"i": 1, "why": "outside_section", "section": 3, "refers_to": "#0"}


def test_a_create_depending_on_an_excluded_entry_is_refused_as_refers_to(tmp_path):
    _project(tmp_path)
    kid = _kid(W2, "E01", depends_on=["#0"])
    assert _refused(tmp_path, kid)["refers_to"] == "#0"


def test_a_status_on_an_excluded_node_is_refused_as_refers_to(tmp_path):
    _project(tmp_path)
    status = {"op": "status", "node": "#0", "effect": "mention", "quote": W2}
    assert _refused(tmp_path, status) == {"i": 1, "why": "outside_section", "section": 3, "refers_to": "#0"}


def test_a_merge_into_an_excluded_node_is_refused_as_refers_to(tmp_path):
    _project(tmp_path)
    merge = {"op": "suggest_merge", "node": "E01", "into": "#0", "quote": W2, "note": "same work"}
    assert _refused(tmp_path, merge) == {"i": 1, "why": "outside_section", "section": 3, "refers_to": "#0"}
