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
    assert run_cli(tmp_path, "done", "--evidence", EV, "--leftovers", token, *_none_for(1, 2, 3))["status"] == "ok"
    assert EV in S.processed(S.events(cfg.store))


def test_done_on_a_long_meeting_with_no_leftovers_needs_no_token(tmp_path):
    cfg = _project(tmp_path, n_filler=260, extra=False)
    for i, line in enumerate(WORK):
        _note(cfg, line, f"n{i}")
        _note(cfg, line, f"m{i}")
    assert run_cli(tmp_path, "done", "--evidence", EV, *_none_for(1, 2, 3))["status"] == "ok"


def test_short_meetings_are_not_token_gated(tmp_path):
    cfg = _project(tmp_path, n_filler=40)
    for i, line in enumerate(WORK):
        _note(cfg, line, f"n{i}")
    assert run_cli(tmp_path, "leftovers", "--evidence", EV)["count"] == 1
    assert run_cli(tmp_path, "done", "--evidence", EV, *_none_for(1, 2, 3))["status"] == "ok"


def test_extract_tells_the_reader_about_leftovers():
    from pathlib import Path
    ext = " ".join((Path(__file__).resolve().parent.parent / "skills" / "run" / "EXTRACT.md").read_text().split())
    assert '"status": "leftovers"' in ext and "leftovers --evidence" in ext and "--leftovers" in ext
    assert "owner cannot be read" in ext and "never dropped" in ext


def test_markers_match_curly_quotes_and_skip_small_talk():
    item = {"id": "x", "title": "Sync", "turns": [
        {"ts": "00:00:01", "speaker": "Nora Quill", "text": "We’ll send the revised vendor list to the client on Monday morning."},
        {"ts": "00:00:02", "speaker": "Ravi Tamsin", "text": "Can you hear me now, is the audio okay for everyone on the call?"},
        {"ts": "00:00:03", "speaker": "Ravi Tamsin", "text": "It should work once the deploy finishes tonight for everyone here."},
        {"ts": "00:00:04", "speaker": "Opal Reyes", "text": "We are still blocked on the crane permit from the port authority."}]}
    assert [s.ts for s in LO.sentences(item)] == ["00:00:01", "00:00:04"]


def test_the_leftovers_hint_says_to_repeat_the_same_done_command(tmp_path):
    cfg = _project(tmp_path, n_filler=260)
    for i, line in enumerate(WORK):
        _note(cfg, line, f"n{i}")
        _note(cfg, line, f"m{i}")
    r = run_cli(tmp_path, "done", "--evidence", EV, check=False)
    assert r["status"] == "leftovers" and "same done command" in r["hint"]


# ---------- decisions: every third of every meeting is read for them (Nov 2: a cue-word gate missed every decision of
# a blind re-read, said without "decide"); a recorded decision, or an 8+ word quote, per third. A recorded risk does not
# account for a third (Nov 2 paid run: two risks accounted for all three thirds of a meeting holding six decisions) --

DECIDE = "So we decided to lock the berth plan the night before, it is locked in after that"
RISK = "My concern is the crane dependency on the port authority permit for the tender"


def test_markers_match_decision_and_decisions():
    assert LO.MARKERS.search("the big decision is made today") and LO.MARKERS.search("two decisions are open")


def test_decision_cues_list_only_uncovered_sentences():
    for s in ("we will go with the second vendor", "that's the rationale to deprioritize billing",
              "rather than keeping it in our sharepoint we move it", "there is a bug on the signup page"):
        assert LO.DECISION_CUES.search(s), s
    assert not LO.DECISION_CUES.search("Nora will send the forklift list")


def _decision(cfg, quote, mid, kind="decision", evid=EV, node=""):
    S.append(cfg.store, [{"type": "create", "mid": mid, "by": "agent", "evidence": evid, "date": "2026-03-02", "kind": kind,
                          "id": node or ("D001" if kind == "decision" else "R001"), "quote": quote, "fields": {"title": "x"}}])


def _task(cfg, quote, mid, evid=EV):
    S.append(cfg.store, [{"type": "create", "mid": mid, "by": "agent", "evidence": evid, "date": "2026-03-02", "kind": "task",
                          "id": "T001", "quote": quote, "fields": {"title": "x"}}])


def _none_for(*parts):
    """--no-decisions with an 8+ word quote from each named third (WORK[i] sits in third i + 1)."""
    return [x for p in parts for x in ("--no-decisions", WORK[p - 1])]


def _processed(cfg):
    return [e for e in S.events(cfg.store) if e["type"] == "processed"][-1]


def _extracted(tmp_path, eid=EV, *flags):
    """Extraction's `done`: coverage and leftovers only; the meeting is then pending its decision pass."""
    r = run_cli(tmp_path, "done", "--evidence", eid, *flags)
    assert r["status"] == "ok"
    return r


def test_short_meeting_is_one_part(tmp_path):
    cfg = _project(tmp_path, n_filler=4)
    d = LO.decisions(_item(cfg), S.events(cfg.store))
    assert [p["part"] for p in d["parts"]] == [1] and len(d["starts"]) == 1 and d["count_unrecorded"] == 1
    long = _item(cfg) | {"turns": _item(cfg)["turns"] * 10}   # over coverage.MIN_CHARS: thirds
    assert [p["part"] for p in LO.decisions(long, [])["parts"]] == [1, 2, 3]


def test_recorded_counts_only_decisions(tmp_path):
    cfg = _project(tmp_path)
    _task(cfg, WORK[1], "t1")
    _note(cfg, WORK[2], "n1")
    assert [p["recorded"] for p in LO.decisions(_item(cfg), S.events(cfg.store))["parts"]] == [0, 0, 0]
    _decision(cfg, EXTRA, "d1")
    _decision(cfg, WORK[2], "r1", kind="risk")
    d = LO.decisions(_item(cfg), S.events(cfg.store))
    assert [p["recorded"] for p in d["parts"]] == [0, 1, 0] and d["count_unrecorded"] == 2
    _decision(cfg, WORK[2], "d2", node="D002")
    assert [p["recorded"] for p in LO.decisions(_item(cfg), S.events(cfg.store))["parts"]] == [0, 1, 1]


def test_a_decision_quoting_the_title_accounts_for_no_third(tmp_path):
    # review: full_text ends with the title, so a quote of it landed in the last third; part_of already refuses it
    cfg = _project(tmp_path)
    _decision(cfg, "Daily sync", "d1")
    assert [p["recorded"] for p in LO.decisions(_item(cfg), S.events(cfg.store))["parts"]] == [0, 0, 0]


def test_an_undone_or_deleted_decision_accounts_for_no_third(tmp_path):
    cfg = _project(tmp_path)
    _decision(cfg, WORK[2], "d1")
    assert [p["recorded"] for p in LO.decisions(_item(cfg), S.events(cfg.store))["parts"]] == [0, 0, 1]
    S.append(cfg.store, [{"type": "human", "op": "undo", "mid": "u1", "by": "human", "target": "d1", "date": "2026-03-02"}])
    assert [p["recorded"] for p in LO.decisions(_item(cfg), S.events(cfg.store))["parts"]] == [0, 0, 0]
    _decision(cfg, EXTRA, "d2", node="D002")
    assert [p["recorded"] for p in LO.decisions(_item(cfg), S.events(cfg.store))["parts"]] == [0, 1, 0]
    S.append(cfg.store, [{"type": "human", "op": "delete", "mid": "x1", "by": "human", "node": "D002", "date": "2026-03-02"}])
    assert [p["recorded"] for p in LO.decisions(_item(cfg), S.events(cfg.store))["parts"]] == [0, 0, 0]


def test_extraction_done_asks_for_no_decision_read_and_leaves_it_pending(tmp_path):
    # Nov 2 paid run: in the extraction context the decision gate caused 9 of 27 refusals, cleared mostly with
    # "none" quotes, and one meeting lost 3 of 7 commitments around them; decisions are read in their own pass
    cfg = _project(tmp_path)
    for i, line in enumerate(WORK):   # coverage satisfied, no decision recorded
        _note(cfg, line, f"n{i}")
    r = _extracted(tmp_path)
    p = _processed(cfg)
    assert p["decisions"] == "pending" and "decisions_read" not in p and r["pending"] == 1
    assert _rows(tmp_path) == [(EV, "decisions")]


def test_extraction_done_ignores_no_decisions_quotes(tmp_path):
    # an old habit is tolerated, not refused: the meeting still gets its own decision pass
    cfg = _project(tmp_path)
    for i, line in enumerate(WORK):
        _note(cfg, line, f"n{i}")
    _extracted(tmp_path, EV, *_none_for(1, 2, 3), "--no-decisions", "these eight words were never said in this meeting")
    p = _processed(cfg)
    assert p["decisions"] == "pending" and "decisions_read" not in p and _rows(tmp_path) == [(EV, "decisions")]


def test_a_re_extracted_meeting_is_read_for_decisions_again(tmp_path):
    # a requeued meeting (re-transcribed, or `redo`) has new text: its earlier decision read no longer stands
    cfg = _project(tmp_path)
    for i, line in enumerate(WORK):
        _note(cfg, line, f"n{i}")
    _extracted(tmp_path)
    run_cli(tmp_path, "done", "--evidence", EV, *_none_for(1, 2, 3))
    assert _rows(tmp_path) == []
    S.requeue(cfg.store, [EV], "re-transcribed after it was processed")
    assert _rows(tmp_path) == [(EV, "extract")]
    _extracted(tmp_path)
    assert _rows(tmp_path) == [(EV, "decisions")]
    run_cli(tmp_path, "done", "--evidence", EV, *_none_for(1, 2, 3))
    assert _rows(tmp_path) == []


def test_extraction_done_on_a_non_meeting_leaves_no_decision_pass(tmp_path):
    cfg = _project(tmp_path)
    eid = "2026-03-03_files_00000001"
    S.write_evidence(cfg.store, {"id": eid, "kind": "file", "at": "2026-03-03T10:00:00", "title": "berth plan.xlsx",
                                 "source": "files", "text": "x"})
    S.append(cfg.store, [{"type": "processed", "evidence": EV, "by": "agent", "note": "", "at": "2026-03-02T12:00:00",
                          "reads": {"decisions": 1}}])   # EV already read: only the file is in question
    _extracted(tmp_path, eid)
    assert "decisions" not in _processed(cfg) and _rows(tmp_path) == []


def test_decision_pass_refuses_a_third_with_no_decision_and_no_quote(tmp_path):
    cfg = _project(tmp_path)
    for i, line in enumerate(WORK):   # coverage satisfied
        _note(cfg, line, f"n{i}")
    _extracted(tmp_path)
    r = run_cli(tmp_path, "done", "--evidence", EV, check=False)
    assert r["status"] == "decisions" and r["parts"] == [1, 2, 3] and [s["part"] for s in r["starts"]] == [1, 2, 3]
    assert "--no-decisions" in r["hint"] and "same done command" in r["hint"] and "decisions_read" not in _processed(cfg)
    assert _rows(tmp_path) == [(EV, "decisions")]
    r = run_cli(tmp_path, "done", "--evidence", EV, *_none_for(1, 2, 3))
    assert r["status"] == "ok" and r["pending"] == 0
    assert _processed(cfg)["decisions_read"] == {"1": WORK[0], "2": WORK[1], "3": WORK[2]} and _rows(tmp_path) == []


def test_a_recorded_decision_accounts_for_its_third(tmp_path):
    cfg = _project(tmp_path)
    for i, line in enumerate(WORK):
        _note(cfg, line, f"n{i}")
    _decision(cfg, EXTRA, "d1")
    _extracted(tmp_path)
    r = run_cli(tmp_path, "done", "--evidence", EV, *_none_for(1), check=False)
    assert r["status"] == "decisions" and r["parts"] == [3]
    assert run_cli(tmp_path, "done", "--evidence", EV, *_none_for(1, 3))["status"] == "ok"


def test_a_third_holding_only_a_recorded_risk_is_unaccounted(tmp_path):
    cfg = _project(tmp_path)
    for i, line in enumerate(WORK):
        _note(cfg, line, f"n{i}")
    _decision(cfg, EXTRA, "r1", kind="risk")
    _extracted(tmp_path)
    r = run_cli(tmp_path, "done", "--evidence", EV, *_none_for(1, 3), check=False)
    assert r["status"] == "decisions" and r["parts"] == [2] and "decisions_read" not in _processed(cfg)
    assert run_cli(tmp_path, "done", "--evidence", EV, *_none_for(1, 2, 3))["status"] == "ok"
    assert _processed(cfg)["decisions_read"] == {"1": WORK[0], "2": WORK[1], "3": WORK[2]}


def test_no_decisions_quote_is_located_not_declared(tmp_path):
    cfg = _project(tmp_path)
    for i, line in enumerate(WORK):
        _note(cfg, line, f"n{i}")
    _extracted(tmp_path)
    r = run_cli(tmp_path, "done", "--evidence", EV, "--no-decisions", WORK[2], "--no-decisions", WORK[2], check=False)
    assert r["status"] == "decisions" and r["parts"] == [1, 2]
    assert run_cli(tmp_path, "done", "--evidence", EV, *_none_for(3, 2, 1))["status"] == "ok"


def test_a_no_decisions_quote_for_a_recorded_third_is_ignored(tmp_path):
    cfg = _project(tmp_path)
    for i, line in enumerate(WORK):
        _note(cfg, line, f"n{i}")
    _decision(cfg, EXTRA, "d1")
    _extracted(tmp_path)
    assert run_cli(tmp_path, "done", "--evidence", EV, *_none_for(1, 2, 3))["status"] == "ok"
    assert _processed(cfg)["decisions_read"] == {"1": WORK[0], "2": "recorded", "3": WORK[2]}


def test_short_or_missing_quote_is_ack_unquoted(tmp_path):
    cfg = _project(tmp_path)
    for i, line in enumerate(WORK):
        _note(cfg, line, f"n{i}")
    unsaid = "these eight words were never said in this meeting"
    _extracted(tmp_path)
    r = run_cli(tmp_path, "done", "--evidence", EV, *_none_for(1, 2), "--no-decisions", "forklift",
                "--no-decisions", unsaid, "--no-decisions", "Daily sync", check=False)
    assert r["status"] == "ack_unquoted" and r["quotes_not_found"] == ["forklift", unsaid, "Daily sync"]
    assert len(r["starts"]) == 3 and "decisions_read" not in _processed(cfg)


def test_processed_records_decisions_read(tmp_path):
    cfg = _project(tmp_path)
    for i, line in enumerate(WORK):
        _note(cfg, line, f"n{i}")
    _decision(cfg, EXTRA, "d1")
    _extracted(tmp_path)
    assert run_cli(tmp_path, "done", "--evidence", EV, *_none_for(1, 3))["status"] == "ok"
    assert _processed(cfg)["decisions_read"] == {"1": WORK[0], "2": "recorded", "3": WORK[2]}


def test_a_processed_event_without_decisions_read_has_no_such_field(tmp_path):
    from taska_lib import ledger as L
    cfg = _project(tmp_path)
    L.mark_processed(cfg.store, EV, "x")
    assert "decisions_read" not in _processed(cfg)


def test_cue_sentences_are_hints_only(tmp_path):
    cfg, eid = _with_lines(tmp_path, [DECIDE, RISK])
    d = LO.decisions(S.evidence(cfg.store)[eid], S.events(cfg.store))
    assert [x["text"] for p in d["parts"] for x in p["hints"]] == [DECIDE, RISK]
    lo = run_cli(tmp_path, "leftovers", "--evidence", eid)
    assert [x["text"] for p in lo["decisions"]["parts"] for x in p["hints"]] == [DECIDE, RISK]
    assert "token" not in lo["decisions"]
    assert run_cli(tmp_path, "done", "--evidence", eid, *_none_for(1, 2, 3))["status"] == "ok"


def test_a_recorded_decision_removes_its_hint(tmp_path):
    cfg, eid = _with_lines(tmp_path, [DECIDE, RISK])
    S.append(cfg.store, [{"type": "create", "mid": "d1", "by": "agent", "evidence": eid, "date": "2026-03-03", "kind": "decision",
                          "id": "D001", "quote": "we decided to lock the berth plan the night before", "fields": {"title": "Lock the berth plan"}}])
    d = LO.decisions(S.evidence(cfg.store)[eid], S.events(cfg.store))
    assert [x["text"] for p in d["parts"] for x in p["hints"]] == [RISK] and d["parts"][0]["recorded"] == 1


def test_no_decisions_help_says_no_decision_recorded():
    import subprocess
    import sys
    from pathlib import Path
    r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / "skills" / "run" / "taska.py"), "done", "-h"],
                       capture_output=True, text=True, check=True)
    flat = " ".join(r.stdout.split())
    assert "with no decision recorded in it" in flat and "decision or risk" not in flat


def test_done_no_longer_takes_decisions_token(tmp_path):
    cfg, eid = _with_lines(tmp_path, [])
    r = run_cli(tmp_path, "done", "--evidence", eid, "--decisions", "abc", *_none_for(1, 2, 3), check=False)
    assert r.get("status") != "ok" and "--decisions" in r["err"]
    assert eid not in S.processed(S.events(cfg.store))


def _with_lines(tmp_path, lines, n_filler=40):
    cfg = _project(tmp_path, n_filler=n_filler)
    item = _item(cfg)
    turns = list(item["turns"])
    for k, line in enumerate(lines):
        turns.insert(1 + 3 * k, {"speaker": "Opal Rivers", "ts": f"00:{3 + 10 * k:02d}:30", "text": line})
    eid = "2026-03-03_meetings_lo000002"
    S.write_evidence(cfg.store, dict(item, id=eid, at="2026-03-03T10:00:00", turns=turns))
    for i, w in enumerate(WORK + [EXTRA]):
        for k in ("w", "v"):   # two events per third: a long meeting's coverage gate wants at least THIN
            S.append(cfg.store, [{"type": "note", "mid": f"{k}{i}", "by": "agent", "evidence": eid, "date": "2026-03-03",
                                  "node": "E01", "quote": w, "text": "x"}])
    return cfg, eid


def test_done_refuses_until_each_part_is_read_for_decisions_on_a_short_meeting(tmp_path):
    cfg = _project(tmp_path, n_filler=4)
    for i, line in enumerate(WORK):
        _note(cfg, line, f"n{i}")
    _extracted(tmp_path)
    r = run_cli(tmp_path, "done", "--evidence", EV, check=False)
    assert r["status"] == "decisions" and r["parts"] == [1]
    assert run_cli(tmp_path, "done", "--evidence", EV, *_none_for(3))["status"] == "ok"
    assert _processed(cfg)["decisions_read"] == {"1": WORK[2]}


def test_a_meeting_whose_every_third_is_accounted_for_is_not_blocked(tmp_path):
    cfg, eid = _with_lines(tmp_path, [DECIDE])
    S.append(cfg.store, [{"type": "create", "mid": "d1", "by": "agent", "evidence": eid, "date": "2026-03-03", "kind": "decision",
                          "id": "D001", "quote": DECIDE, "fields": {"title": "Lock the berth plan"}}])
    assert run_cli(tmp_path, "done", "--evidence", eid, *_none_for(2, 3))["status"] == "ok"


def test_long_meeting_needs_the_leftovers_token_then_its_own_decision_pass(tmp_path):
    cfg, eid = _with_lines(tmp_path, [DECIDE], n_filler=260)
    lo = run_cli(tmp_path, "leftovers", "--evidence", eid)
    assert lo["count"] and run_cli(tmp_path, "done", "--evidence", eid, *_none_for(1, 2, 3), check=False)["status"] == "leftovers"
    _extracted(tmp_path, eid, "--leftovers", lo["token"])
    assert _processed(cfg)["decisions"] == "pending"
    r = run_cli(tmp_path, "done", "--evidence", eid, check=False)   # the decision pass asks for no leftovers token
    assert r["status"] == "decisions" and r["parts"] == [1, 2, 3]
    assert run_cli(tmp_path, "done", "--evidence", eid, *_none_for(1, 2, 3))["status"] == "ok"


# ---------- [run].decisions_since: processed meetings are re-read for decisions only ----------

def _since(tmp_path, day="2026-03-02", n_filler=40, run=""):
    """[run].decisions_since is a deprecated alias: meetings on or after it are requested reads (why: requested)."""
    cfg = _project(tmp_path, n_filler=n_filler)
    (tmp_path / "taska.toml").write_text((tmp_path / "taska.toml").read_text() + f'\n[run]\ndecisions_since = "{day}"\n{run}')
    S.append(cfg.store, [{"type": "processed", "evidence": EV, "by": "agent", "note": "", "at": "2026-03-02T12:00:00"}])
    return C.load(tmp_path)


def _rows(tmp_path):
    return [(r["id"], r["need"]) for r in run_cli(tmp_path, "pending")["items"]]


def test_a_meeting_read_for_decisions_before_decisions_since_is_not_listed_again(tmp_path):
    cfg = _since(tmp_path, day="2026-03-03")   # EV (2 Mar) predates decisions_since
    S.append(cfg.store, [{"type": "processed", "evidence": EV, "by": "agent", "note": "", "at": "2026-03-02T12:00:00",
                          "decisions": "pending"}])
    assert _rows(tmp_path) == [(EV, "decisions")]   # pending lists it whatever its date
    run_cli(tmp_path, "done", "--evidence", EV, *_none_for(1, 2, 3))
    assert _rows(tmp_path) == []


def test_backfill_lists_only_the_decision_read(tmp_path):
    _since(tmp_path)
    assert _rows(tmp_path) == [(EV, "decisions")]
    assert [r["why"] for r in run_cli(tmp_path, "pending")["items"]] == ["requested"]


def test_outside_the_window_nothing_is_backfilled_and_the_gap_is_reported(tmp_path):
    # before read versions this was "without decisions_since nothing is backfilled": the window now backfills recent
    # meetings by itself, so an older one stays out and is only counted
    cfg = _project(tmp_path)
    S.append(cfg.store, [{"type": "processed", "evidence": EV, "by": "agent", "note": "", "at": "2026-03-02T12:00:00"}])
    later = "2026-03-30_meetings_lo000002"
    S.write_evidence(cfg.store, {"id": later, "kind": "meeting", "at": "2026-03-30T10:00:00", "title": "t", "source": "s",
                                 "turns": [{"speaker": "Nora Quill", "ts": "00:00:00", "text": "x"}]})
    S.append(cfg.store, [{"type": "processed", "evidence": later, "by": "agent", "note": "", "at": "2026-03-30T12:00:00",
                          "reads": {"decisions": 1}}])
    assert _rows(tmp_path) == []
    assert run_cli(tmp_path, "pending")["gaps"]["decisions"]["outside_window"] == 1


def test_unprocessed_rows_are_extract(tmp_path):
    _project(tmp_path)
    assert _rows(tmp_path) == [(EV, "extract")]


def test_backfill_leaves_meetings_before_the_date(tmp_path):
    # while decisions_since is set it yields exactly its old rows: window backfill waits until it is removed
    cfg = _project(tmp_path)
    (tmp_path / "taska.toml").write_text((tmp_path / "taska.toml").read_text() + '\n[run]\ndecisions_since = "2026-10-26"\n')
    days = ["2026-10-14", "2026-10-20", "2026-10-22", "2026-10-24", "2026-10-27", "2026-10-29", "2026-11-02"]
    ids = [f"{d}_meetings_ds{i:06d}" for i, d in enumerate(days)]
    for eid, d in zip(ids, days):
        S.write_evidence(cfg.store, {"id": eid, "kind": "meeting", "at": f"{d}T10:00:00", "title": "t", "source": "s",
                                     "turns": [{"speaker": "Nora Quill", "ts": "00:00:00", "text": "x"}]})
    S.append(cfg.store, [{"type": "processed", "evidence": c, "by": "agent", "note": "", "at": "2026-11-02T12:00:00"}
                         for c in [EV] + ids])
    r = run_cli(tmp_path, "pending")
    assert [(x["id"], x["need"], x["why"]) for x in r["items"]] == [(c, "decisions", "requested") for c in ids[4:]]
    g = r["gaps"]["decisions"]   # the window (from 19 Oct) still counts the 20, 22 and 24 Oct meetings
    assert g["in_window"] == 3 and g["waiting"] == 3 and g["outside_window"] == 2


def test_backfill_skips_messages_calendar_and_duplicates(tmp_path):
    cfg = _since(tmp_path)
    for i, (kind, extra) in enumerate([("message", {"text": "hello"}), ("event", {"text": "Sync"}),
                                       ("meeting", {"turns": [{"speaker": "Nora Quill", "ts": "00:00:00", "text": "x"}],
                                                    "duplicate_of": EV})]):
        eid = f"2026-03-02_other_{i:08d}"
        S.write_evidence(cfg.store, dict({"id": eid, "kind": kind, "at": "2026-03-02T11:00:00", "title": "t",
                                          "source": "s"}, **extra))
        S.append(cfg.store, [{"type": "processed", "evidence": eid, "by": "agent", "note": "", "at": "2026-03-02T12:00:00"}])
    assert _rows(tmp_path) == [(EV, "decisions")]


def test_backfill_done_checks_only_decisions(tmp_path):
    _since(tmp_path)   # nothing recorded in any third: a first read would stop at coverage
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _project(fresh)
    assert run_cli(fresh, "done", "--evidence", EV, check=False)["status"] == "coverage"
    r = run_cli(tmp_path, "done", "--evidence", EV, check=False)
    assert r["status"] == "decisions"
    r = run_cli(tmp_path, "done", "--evidence", EV, *_none_for(*r["parts"]))
    assert r["status"] == "ok" and r["pending"] == 0 and "decisions_read" in _processed(C.load(tmp_path))


def test_read_meeting_leaves_pending(tmp_path):
    _since(tmp_path)
    run_cli(tmp_path, "done", "--evidence", EV, *_none_for(1, 2, 3))
    assert _rows(tmp_path) == []


def test_a_requeued_meeting_is_a_full_extract_again(tmp_path):
    cfg = _since(tmp_path)
    S.requeue(cfg.store, [EV], "changed")
    assert _rows(tmp_path) == [(EV, "extract")]


def test_pending_rows_stay_in_time_order(tmp_path):
    cfg = _since(tmp_path)
    for eid, at in (("2026-03-01_m_00000001", "2026-03-01T09:00:00"), ("2026-03-03_m_00000002", "2026-03-03T09:00:00")):
        S.write_evidence(cfg.store, {"id": eid, "kind": "meeting", "at": at, "title": "t", "source": "s",
                                     "turns": [{"speaker": "Nora Quill", "ts": "00:00:00", "text": "x"}]})
    S.append(cfg.store, [{"type": "processed", "evidence": "2026-03-01_m_00000001", "by": "agent", "note": "",
                          "at": "2026-03-02T12:00:00"}])
    assert _rows(tmp_path) == [(EV, "decisions"), ("2026-03-03_m_00000002", "extract")]


# ---------- the second read sees, per part, what this meeting already holds (Nov 2: it saw only a decision count and
# the whole register, and risked recording a listed item again in other words) ----------

def _made(cfg, quote, mid, node, kind, title, evid=EV, by="agent", **extra):
    S.append(cfg.store, [{"type": "create", "mid": mid, "by": by, "evidence": evid, "date": "2026-03-02", "kind": kind,
                          "id": node, "quote": quote, "fields": {"title": title}} | extra])


def _items(cfg):
    return [p["items"] for p in LO.decisions(_item(cfg), S.events(cfg.store))["parts"]]


def test_each_part_lists_the_nodes_recorded_from_it(tmp_path):
    cfg = _project(tmp_path)
    _made(cfg, WORK[0], "t1", "T001", "task", "Send the forklift certification list")
    _made(cfg, EXTRA, "d1", "D001", "decision", "Crane tender shortlist goes first")
    _made(cfg, WORK[1], "s1", "S001", "story", "Warehouse site visit held")
    _made(cfg, WORK[2], "r1", "R001", "risk", "Crane inspection report may slip")
    _note(cfg, WORK[2], "n1")
    assert _items(cfg) == [[{"id": "T001", "kind": "task", "title": "Send the forklift certification list"}],
                           [{"id": "S001", "kind": "story", "title": "Warehouse site visit held"},
                            {"id": "D001", "kind": "decision", "title": "Crane tender shortlist goes first"}],
                           [{"id": "R001", "kind": "risk", "title": "Crane inspection report may slip"}]]


def test_the_items_list_leaves_the_decision_gate_unchanged(tmp_path):
    cfg = _project(tmp_path)
    _made(cfg, WORK[0], "t1", "T001", "task", "Send the forklift certification list")
    _made(cfg, WORK[2], "r1", "R001", "risk", "Crane inspection report may slip")
    d = LO.decisions(_item(cfg), S.events(cfg.store))
    assert [p["recorded"] for p in d["parts"]] == [0, 0, 0] and d["count_unrecorded"] == 3


def test_a_title_only_quote_lists_its_node_in_no_part(tmp_path):
    cfg = _project(tmp_path)
    _made(cfg, "Daily sync", "t1", "T001", "task", "Daily sync follow-up")
    assert _items(cfg) == [[], [], []]


def test_items_are_live_nodes_of_this_meeting_only(tmp_path):
    cfg = _project(tmp_path)
    _made(cfg, WORK[0], "t1", "T001", "task", "Send the forklift certification list", evid="2026-03-01_meetings_other001")
    _made(cfg, WORK[1], "t2", "T002", "task", "Book the warehouse site visit")
    _made(cfg, WORK[2], "t3", "T003", "task", "Share the crane inspection report")
    S.append(cfg.store, [{"type": "human", "op": "undo", "mid": "u1", "by": "human", "target": "t3", "date": "2026-03-02"}])
    S.append(cfg.store, [{"type": "human", "op": "delete", "mid": "x1", "by": "human", "node": "T002", "date": "2026-03-02"}])
    assert _items(cfg) == [[], [], []]


def test_a_confirmed_proposal_from_this_meeting_is_listed(tmp_path):
    cfg = _project(tmp_path)
    S.append(cfg.store, [{"type": "processed", "evidence": EV, "by": "agent", "note": "", "at": "2026-03-02T12:00:00"},
                         {"type": "human", "op": "accept", "mid": "a1", "by": "human", "target": "p1", "date": "2026-03-02"}])
    _made(cfg, WORK[1], "h1", "T001", "task", "Book the warehouse site visit", by="human", from_proposal="p1")
    _made(cfg, WORK[2], "h2", "T002", "task", "Typed by hand", by="human")
    assert _items(cfg) == [[], [{"id": "T001", "kind": "task", "title": "Book the warehouse site visit"}], []]


def test_an_item_shows_its_current_title(tmp_path):
    cfg = _project(tmp_path)
    _made(cfg, WORK[0], "t1", "T001", "task", "Send the forklift list")
    S.append(cfg.store, [{"type": "human", "op": "edit", "mid": "e1", "by": "human", "node": "T001", "date": "2026-03-02",
                          "set": {"title": "Send the forklift certification list to Harbor"}}])
    assert _items(cfg)[0] == [{"id": "T001", "kind": "task", "title": "Send the forklift certification list to Harbor"}]


def test_leftovers_prints_the_items_under_decisions(tmp_path):
    cfg = _project(tmp_path)
    _made(cfg, WORK[1], "t1", "T001", "task", "Book the warehouse site visit")
    lo = run_cli(tmp_path, "leftovers", "--evidence", EV)
    assert [p["items"] for p in lo["decisions"]["parts"]] == [
        [], [{"id": "T001", "kind": "task", "title": "Book the warehouse site visit"}], []]


def test_a_meeting_recorded_in_full_lists_everything_and_leaves_no_work_hint(tmp_path):
    # review focus: a second read of a meeting whose work is all recorded finds each item listed in its part and no
    # sentence left to hint at; recording a listed item again from the same quote adds nothing
    from taska_lib import ledger as L
    cfg = _project(tmp_path)
    S.append(cfg.store, [{"type": "create", "mid": "e1", "by": "human", "id": "E01", "kind": "epic", "date": "2026-03-01",
                          "fields": {"title": "Harbor yard", "plan": "Plan"}}])
    props = [{"op": "create", "ref": "s1", "kind": "story", "parent": "E01", "title": "Harbor yard ready",
              "ac": ["Certification list sent"], "quote": WORK[0]},
             {"op": "create", "kind": "task", "type": "action", "parent": "s1", "title": "Book the site visit",
              "internal_owner": "Ravi Tamsin", "quote": WORK[1]},
             {"op": "create", "kind": "task", "type": "action", "parent": "s1", "title": "Draft the vendor shortlist",
              "internal_owner": "Nora Quill", "quote": EXTRA},
             {"op": "create", "kind": "task", "type": "action", "parent": "s1", "title": "Share the inspection report",
              "internal_owner": "Opal Brennick", "quote": WORK[2]}]
    assert L.apply(cfg.store, cfg, EV, props)["events"] == 4
    lo = LO.leftovers(_item(cfg), S.events(cfg.store))
    assert lo["count"] == 0
    assert [[x["title"] for x in p["items"]] for p in LO.decisions(_item(cfg), S.events(cfg.store))["parts"]] == [
        ["Harbor yard ready"], ["Book the site visit", "Draft the vendor shortlist"], ["Share the inspection report"]]
    assert L.apply(cfg.store, cfg, EV, props)["events"] == 0


def _card(cfg, quote, mid, kind, title):
    S.append(cfg.store, [{"type": "agent", "op": "propose", "mid": mid, "by": "agent", "evidence": EV, "date": "2026-03-02",
                          "node": "E01", "pending": "human", "quote": quote, "proposal": {"kind": kind, "title": title}}])


def test_cards_from_this_meeting_are_listed_pending_or_rejected(tmp_path):
    # review I1: a card the second reader cannot see is re-proposed in other words, past the mid dedupe
    cfg = _project(tmp_path)
    _card(cfg, WORK[1], "p1", "task", "Book the warehouse site visit")
    _card(cfg, WORK[2], "p2", "story", "Crane inspection shared")
    S.append(cfg.store, [{"type": "human", "op": "reject", "mid": "j1", "by": "human", "target": "p2", "date": "2026-03-02"}])
    assert _items(cfg) == [[], [{"id": "p1", "kind": "task", "title": "Book the warehouse site visit", "card": "pending"}],
                           [{"id": "p2", "kind": "story", "title": "Crane inspection shared", "card": "rejected"}]]


def test_an_accepted_card_is_listed_once_as_its_node(tmp_path):
    cfg = _project(tmp_path)
    _card(cfg, WORK[1], "p1", "task", "Book the warehouse site visit")
    S.append(cfg.store, [{"type": "processed", "evidence": EV, "by": "agent", "note": "", "at": "2026-03-02T12:00:00"},
                         {"type": "human", "op": "accept", "mid": "a1", "by": "human", "target": "p1", "date": "2026-03-02"}])
    _made(cfg, WORK[1], "h1", "T001", "task", "Book the warehouse site visit", by="human", from_proposal="p1")
    assert _items(cfg) == [[], [{"id": "T001", "kind": "task", "title": "Book the warehouse site visit"}], []]


def test_a_card_quoting_only_the_title_or_undone_is_not_listed(tmp_path):
    cfg = _project(tmp_path)
    _card(cfg, "Daily sync", "p1", "task", "Daily sync notes")
    _card(cfg, WORK[0], "p2", "task", "Send the forklift list")
    S.append(cfg.store, [{"type": "human", "op": "undo", "mid": "u1", "by": "human", "target": "p2", "date": "2026-03-02"}])
    assert _items(cfg) == [[], [], []]
