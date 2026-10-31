"""The pre-send gate (taska_lib/gate.py): each check is a pure function tested here on invented data; `run_gate` wires
them to a store and a week's draft and writes presend.json + presend.html, the shapes the dashboard and the send step
read. Moved from a project's scripts (Oct 31) with these tests."""
import json
import zipfile

from taska_lib import gate as G, ledger as L, store as S

TEXT = ("Summary. The depot workshop with the client was held on Oct 22. T101 · Book the yard visit. "
        "S010 Dock schedule shared. R003 Late forklift list. D004 Keep the old scanner. E02 Yard.")


def test_cited_ids_are_found():
    assert G.cited_ids(TEXT) == {"T101", "S010", "R003", "D004", "E02"}


def test_missing_cited_ids_fail():
    r = G.check_cited_exist({"T101", "S010"}, {"T101": {"state": "open"}})
    assert r["ok"] is False and r["detail"]["missing"] == ["S010"]


def test_baseline_ids_may_be_merged_or_cancelled_but_not_vanish():
    N = {"T101": {"state": "merged"}, "S010": {"state": "cancelled"}}
    assert G.check_cited_exist({"T101", "S010"}, N)["ok"] is True
    assert G.check_cited_exist({"T101", "S010", "R003"}, N)["detail"]["missing"] == ["R003"]


def test_date_sentence_contradicting_the_calendar_fails():
    cal = [{"subject": "Harbor: Depot Workshop for the client", "start": "2026-03-04T14:00:00Z"},
           {"subject": "Canceled: Depot Workshop placeholder", "start": "2026-03-05T14:00:00Z"}]
    r = G.check_dates("The depot workshop with the client was held on Mar 5.", cal, "America/New_York", 2026)
    assert r["ok"] is False
    assert r["detail"]["wrong"] == [{"said": "2026-03-05", "calendar": "2026-03-04",
                                     "event": "Harbor: Depot Workshop for the client",
                                     "sentence": "The depot workshop with the client was held on Mar 5."}]
    assert G.check_dates("The depot workshop with the client was held on Mar 4.", cal, "America/New_York", 2026)["ok"]


def test_a_date_with_no_calendar_match_is_not_judged():
    assert G.check_dates("The yard tour was held on Mar 5.", [], "UTC", 2026)["ok"] is True


def test_must_report_needs_a_choice_first():
    r = G.check_must_report(None, {}, set())
    assert r["ok"] is False and "not chosen" in r["detail"]["why"]


def test_must_report_item_cited_or_excluded():
    choices = {"must_report": ["c1", "c2", "c3"], "excluded": {"c3": "client asked to drop it"}}
    match = {"c1": "node:T101", "c2": "card", "c3": None}
    r = G.check_must_report(choices, match, {"T101"})
    assert r["ok"] is False
    assert r["detail"]["not_in_report"] == [{"cid": "c2", "match": "card"}]
    assert G.check_must_report({"must_report": ["c1"]}, match, {"T101"})["ok"] is True


def test_cited_duplicate_pair_fails():
    r = G.check_duplicates({"T101", "T102", "S010"}, [["T101", "T102", 0.8], ["S010", "S099", 0.6]])
    assert r["ok"] is False and r["detail"]["pairs"] == [["T101", "T102"]]


def test_owner_from_room_audio_needs_confirmation():
    N = {"T101": {"created_in": "m1", "internal_owner": "Nora Quill"}, "T102": {"created_in": "m2", "internal_owner": "Ravi"},
         "T103": {"created_in": "m1", "internal_owner": None}}
    r = G.check_room_owners({"T101", "T102", "T103"}, N, [{"id": "m1"}], {})
    assert r["ok"] is False and r["detail"]["to_confirm"] == [{"id": "T101", "owner": "Nora Quill", "meeting": "m1"}]
    assert G.check_room_owners({"T101"}, N, [{"id": "m1"}], {"owners_confirmed": ["T101"]})["ok"] is True


def test_blocking_failure_sets_the_exit_code():
    gates = [{"id": "G2", "blocking": True, "ok": True}, {"id": "G8", "blocking": False, "ok": False}]
    assert G.exit_code(gates) == 0
    assert G.exit_code(gates + [{"id": "G5", "blocking": True, "ok": False}]) == 1


def test_coverage_counts_the_states():
    items = [{"cid": "a", "match": "node:T101"}, {"cid": "b", "match": "node:T200"}, {"cid": "c", "match": "card"},
             {"cid": "d", "match": None}]
    assert G.coverage(items, {"T101"}) == {"cited": 1, "in_tree_not_cited": 1, "card": 1, "missing": 1, "total": 4}


def test_a_date_sentence_starts_after_the_header_separator():
    cal = [{"subject": "Harbor: Depot Workshop for the client", "start": "2026-03-04T14:00:00Z"}]
    r = G.check_dates("Weekly report · built Mar 6 · internal only The depot workshop with the client was held on Mar 5.",
                      cal, "America/New_York", 2026)
    assert r["detail"]["wrong"][0]["sentence"] == "internal only The depot workshop with the client was held on Mar 5."


def test_no_baseline_fails_g1_instead_of_passing():
    r = G.check_baseline(None)
    assert r["ok"] is False and "baseline" in r["detail"]["why"]


def test_a_week_the_key_does_not_cover_cannot_look_ready():
    assert G.check_key_covers([], ["m1", "m2"])["ok"] is False
    r = G.check_key_covers([{"evidence": "m1"}], ["m1", "m2"])
    assert r["ok"] is True and r["detail"]["meetings_without_key_items"] == ["m2"]


def test_pending_merge_and_merged_pairs_count_as_duplicates():
    N = {"T1": {"state": "merged", "merged_into": "T3"}, "T3": {"state": "open"},
         "T5": {"state": "open", "suggestions": [{"op": "suggest_merge", "to": "T6", "status": "pending"}]}, "T6": {"state": "open"}}
    pairs = G.known_pairs(N, [["T7", "T8", 0.6]])
    assert ["T1", "T3"] in pairs and ["T5", "T6"] in pairs and ["T7", "T8"] in pairs
    assert G.check_duplicates({"T1", "T3"}, pairs)["ok"] is False


def test_a_draft_older_than_the_tree_fails():
    assert G.check_fresh(100.0, 200.0)["ok"] is False
    assert G.check_fresh(300.0, 200.0)["ok"] is True


def test_a_look_alike_pair_you_marked_different_does_not_block():
    pairs = [["T107", "T118", 0.6]]
    assert G.check_duplicates({"T107", "T118"}, pairs)["ok"] is False
    r = G.check_duplicates({"T107", "T118"}, pairs, {"pairs_ok": [["T118", "T107"]]})
    assert r["ok"] is True and r["detail"]["marked_different"] == [["T107", "T118"]]


# ---- a normal week (no answer key, live calendar, last sent report as baseline): Oct 26 automation design ----

def test_key_checks_block_only_in_a_week_the_key_covers():
    assert G.key_blocking([{"cid": "g1", "evidence": "m1"}]) is True
    assert G.key_blocking([]) is False


def test_calendar_events_come_from_the_store_in_the_project_timezone():
    evd = {"2026-03-06_calendar_aa": {"kind": "event", "title": "Depot workshop part 2",
                                      "meta": {"what": "scheduled", "start": "2026-03-09T10:00:00", "end": "2026-03-09T11:00:00"}},
           "2026-03-06_calendar_bb": {"kind": "event", "title": "Yard review",
                                      "meta": {"what": "cancelled", "start": "2026-03-10T09:00:00", "end": "2026-03-10T10:00:00"}},
           "2026-03-06_meetings_cc": {"kind": "meeting", "title": "Daily sync"}}
    cal = G.calendar_from_store(evd, "America/New_York")
    assert {e["subject"] for e in cal} == {"Depot workshop part 2", "Canceled: Yard review"}
    e = next(e for e in cal if e["subject"] == "Depot workshop part 2")
    assert e["start"] == "2026-03-09T14:00:00+00:00"          # 10:00 New York (EDT from Mar 8), stored as UTC
    # and the date check reads it on the right local day
    r = G.check_dates("The depot workshop part 2 with the client was held on Mar 9.", cal, "America/New_York", 2026)
    assert r["ok"] is True


def test_the_baseline_is_the_last_report_the_reader_received(tmp_path):
    rep = tmp_path / "reports"
    (rep / "2026-03-02").mkdir(parents=True)
    (rep / "2026-03-09").mkdir()
    sent = rep / "2026-03-02" / "Harbor weekly report 2026-03-09.docx"
    sent.write_bytes(b"x")
    (rep / "2026-03-02" / "sent-final.json").write_text('{"docx": "Harbor weekly report 2026-03-09.docx"}')
    assert G.pick_baseline(rep, "2026-03-09", {}) == sent
    # a choice names it by hand (a week sent before sent-final.json existed)
    other = rep / "2026-03-02" / "older.docx"
    other.write_bytes(b"y")
    assert G.pick_baseline(rep, "2026-03-09", {"baseline": str(other)}) == other
    # the week's own earlier send is not its baseline when it is being re-sent: never compare a file with itself
    assert G.pick_baseline(rep, "2026-03-02", {}) is None


def test_report_as_built_accepts_what_the_report_cites():
    # the owner's choice for the week (Oct 26): the report as built is what must be reported; a rebuild that cites a
    # slightly different set must not hold the automatic send
    ch = {"must_report_policy": "report_as_built", "must_report": ["g1", "g2"]}
    r = G.check_must_report(ch, {"g1": "node:T101", "g2": "node:T999"}, {"T101"})
    assert r["ok"] is True and r["detail"]["policy"] == "report_as_built"
    assert G.check_must_report({"must_report": ["g2"]}, {"g2": "node:T999"}, {"T101"})["ok"] is False


def test_the_newest_draft_is_never_a_word_lock_or_a_half_built_file(tmp_path):
    import os, time
    good = tmp_path / "Harbor weekly report 2026-03-09.docx"; good.write_bytes(b"x")
    time.sleep(0.01)
    for n in ["~$rbor weekly report 2026-03-09.docx", ".building.docx"]:
        (tmp_path / n).write_bytes(b"y")
    assert G.latest(tmp_path, "*weekly report*.docx") == good


# ---- the wired gate on an invented project ----

def _docx(path, text):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f"<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")


def _project_with_a_draft(project, week="2026-03-02"):
    from taska_lib import config as C
    cfg = C.load(project)
    S.ensure(cfg.store)
    S.append(cfg.store, [{"type": "create", "mid": "c1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None,
                          "date": None, "fields": {"title": "Yard", "ac": [], "depends_on": []}}])
    wk = project / "reports" / week
    wk.mkdir(parents=True)
    docx = wk / "Harbor weekly report 2026-03-06.docx"
    _docx(docx, "Weekly report · built Mar 6 · internal only. E01 Yard work continues.")
    (wk / "presend-choices.json").write_text(json.dumps({"baseline": str(docx)}))
    return cfg, wk, docx


def test_gate_without_a_key_is_advisory(project):
    # every project but the one with a hand-built answer key: the key checks never block, never crash
    cfg, wk, docx = _project_with_a_draft(project)
    r = G.run_gate(cfg, "2026-03-02", docx, project / "reports", reader="Opal Brennick", port=1)
    by = {g["id"]: g for g in r["gates"]}
    assert by["G3"]["blocking"] is False and by["G9b"]["blocking"] is False and r["coverage"]["total"] == 0
    assert by["G2"]["ok"] is True
    assert (wk / f"presend-{r['stamp']}" / "presend.json").exists() and (wk / f"presend-{r['stamp']}" / "presend.html").exists()


def test_the_page_names_the_configured_reader_never_a_client(project):
    cfg, wk, docx = _project_with_a_draft(project)
    r = G.run_gate(cfg, "2026-03-02", docx, project / "reports", reader="Opal Brennick", port=1)
    page = (wk / f"presend-{r['stamp']}" / "presend.html").read_text()
    assert "Tick what Opal Brennick must see" in page and "the reader must see" not in page


def test_the_cli_runs_the_gate_and_exits_1_when_a_blocking_gate_fails(project):
    from conftest import run_cli
    cfg, wk, docx = _project_with_a_draft(project)
    r = run_cli(project, "gate", "2026-03-02", "--docx", str(docx), "--reports", str(project / "reports"),
                "--reader", "Opal Brennick", "--port", "1", check=False)
    assert r["status"] == "blocked" and "G0" in r["failed"] and r["page"].endswith("presend.html")
