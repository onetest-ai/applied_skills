"""Follow-ups from the review of the taska 0.1.2 merge: each pins one behaviour a person relies on. Invented data only."""
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from taska_lib import infer as I, ledger as L, pack as PK, store as S, workbench as W
from taska_lib.text import date_in_name
import test_infer as TI

RUN_DIR = Path(__file__).resolve().parents[1] / "skills" / "run"


class Cfg(TI.Cfg):
    def team(self, who):
        return None


def _store(items, events):
    d = tempfile.mkdtemp()
    S.ensure(d)
    for x in items:
        S.write_evidence(d, x)
    S.append(d, events)
    return d


def _meeting(i, at, text="", speaker="Sam Lee"):
    return {"id": i, "kind": "meeting", "at": at, "title": "Weekly sync", "source": "meetings", "text": "",
            "turns": [{"speaker": speaker, "ts": "00:05:00", "text": text}]}


# ---- a dotted archive name is dated by its dotted date, not by a time in the subject ----
@pytest.mark.parametrize("name, want", [
    ("10.19.2026_1101 sync.vtt", dt.date(2026, 10, 19)),     # the archive's MM.DD.YYYY_<subject>
    ("10.19.2026_Weekly.vtt", dt.date(2026, 10, 19)),
    ("20261019_1101 recording.mp4", dt.date(2026, 10, 19)),  # a compact stamp still wins when there is no dotted date
    ("2026-10-19 Weekly.vtt", dt.date(2026, 10, 19)),
])
def test_an_archive_name_is_dated_by_its_dotted_date(name, want):
    assert date_in_name(name, dt.date(2026, 10, 30)) == want


# ---- the client pack shows the year of a date from another year ----
def test_client_pack_dates_from_another_year_carry_the_year():
    d = _store([_meeting("2026-03-04_meetings_aaaa0001", "2026-03-04T10:00:00", "report status")], [
        TI._create("E01", "epic", "Reporting"),
        {**TI._create("S001", "story", "Annual report", "E01", ac=["Signed off"]),
         "fields": {"title": "Annual report", "parent": "E01", "ac": ["Signed off"], "depends_on": [], "due": "2025-12-01"}},
    ])
    S.append(d, [{"type": "human", "op": "edit", "by": "human", "node": "S001", "set": {"due": "2025-12-01"},
                  "evidence": "2026-03-04_meetings_aaaa0001", "date": "2026-03-04", "at": "2026-03-04T11:00:00", "mid": "h1"}])
    L.mark_processed(d, "2026-03-04_meetings_aaaa0001")   # the pack's "today" is the newest processed meeting
    out = PK.build_client(d, Cfg(), 7)
    assert out["slide6"][0]["planned_final"] == "Dec 1, 2025", out["slide6"]   # the deliverables slide


# ---- a decision cited in the pack carries its year across a new year ----
def test_pack_decisions_cite_the_year_when_it_differs():
    q = "we decided to keep the vendor for next year"
    d = _store([_meeting("2025-12-30_meetings_aaaa0002", "2025-12-30T10:00:00", q),
                _meeting("2026-01-02_meetings_aaaa0003", "2026-01-02T10:00:00", "happy new year")], [
        TI._create("E01", "epic", "Vendors", created=None),
    ])
    r = L.apply(d, Cfg(), "2025-12-30_meetings_aaaa0002",
                [{"op": "create", "kind": "decision", "parent": "E01", "title": "Keep the current vendor", "quote": q}])
    assert r["by_op"].get("create_decision") == 1, r
    L.mark_processed(d, "2026-01-02_meetings_aaaa0003")   # the pack's "today" is the newest processed meeting
    md = PK.build(d, Cfg(), 7)
    line = next(l for l in md.splitlines() if "Keep the current vendor" in l)
    assert "Dec 30, 2025" in line, line


# ---- infer's closure proposal says which criterion text it read ----
def test_inferred_criterion_proposal_carries_the_text_it_read(monkeypatch):
    seen = []
    real = L.apply
    monkeypatch.setattr(L, "apply", lambda store, cfg, evid, props: seen.extend(props) or real(store, cfg, evid, props))
    d = TI.store()
    I.run(d, TI.Cfg(), ask=TI.fake_ask({(TI.CRIT, "workshop is held"): 0.95}))
    met = [p for p in seen if p["op"] == "ac_met"]
    assert met and met[0]["ac_text"] == "The workshop is held with the client"


# ---- evidence is rewritten atomically: a failure leaves the old .json whole ----
def test_redating_a_copy_never_leaves_a_half_written_index(monkeypatch):
    folder = _meeting("2026-10-21_meetings_aaaa0004", "2026-10-21T09:00:00", "the plan is agreed")
    d = _store([folder], [])
    p = Path(S.P(d, "evidence", folder["id"] + ".json"))
    before = p.read_text()
    monkeypatch.setattr(S, "render_md", lambda x: (_ for _ in ()).throw(RuntimeError("disk full")))
    with pytest.raises(RuntimeError):
        S.take_teams_time(d, folder["id"], {"id": "2026-10-20_teams_aaaa0005", "at": "2026-10-20T15:00:00",
                                            "source": "teams", "kind": "meeting"})
    assert p.read_text() == before and not list(p.parent.glob("*.tmp"))


def test_repair_writes_evidence_through_the_store():
    src = (RUN_DIR / "taska_lib" / "repair.py").read_text()
    assert '".json").write_text(' not in src and "S.save_evidence(" in src


# ---- a proposal whose parent was merged away is confirmed under the survivor ----
def test_a_proposal_under_a_merged_parent_lands_on_the_survivor():
    q = "someone should draft the onboarding guide"
    d = _store([_meeting("2026-03-04_meetings_aaaa0006", "2026-03-04T10:00:00", q)], [
        TI._create("E01", "epic", "Onboarding", created="2026-01-01"),
        TI._create("S001", "story", "Onboarding pack", "E01", ac=["Guide drafted"], created="2026-01-01"),
        TI._create("S002", "story", "Onboarding pack (copy)", "E01", ac=["Guide drafted"], created="2026-01-01"),
    ])
    r = L.apply(d, Cfg(), "2026-03-04_meetings_aaaa0006",
                [{"op": "propose", "kind": "task", "parent": "S002", "title": "Draft the onboarding guide",
                  "type": "action", "quote": q}])
    assert r["by_op"].get("propose") == 1, r
    mid = next(e["mid"] for e in S.events(d) if e.get("op") == "propose")
    L.human_event(d, {"op": "merge", "node": "S002", "into": "S001"})
    L.human_event(d, {"op": "accept", "target": mid})
    N, *_ = L.fold(d)
    made = [n for n in N.values() if n["title"] == "Draft the onboarding guide"]
    assert made and made[0]["parent"] == "S001"


# ---- a whitespace-only proposal title is rejected, as for create ----
def test_a_proposal_needs_a_real_title():
    q = "someone should draft the onboarding guide"
    d = _store([_meeting("2026-03-04_meetings_aaaa0007", "2026-03-04T10:00:00", q)], [
        TI._create("E01", "epic", "Onboarding", created="2026-01-01"), TI._create("S001", "story", "Onboarding pack", "E01", ac=["Guide drafted"], created="2026-01-01")])
    r = L.apply(d, Cfg(), "2026-03-04_meetings_aaaa0007",
                [{"op": "propose", "kind": "task", "parent": "S001", "title": "   ", "type": "action", "quote": q}])
    assert not r["by_op"].get("propose") and any("title" in x["why"] for x in r["rejected"]), r


# ---- the Recap shows the tree after your edit, not before it ----
def test_the_recap_is_refetched_after_an_edit():
    page = (RUN_DIR / "workbench.html").read_text()
    for fn in ("async function send(", "async function acceptAll("):
        body = page[page.index(fn):].split("\n}\n", 1)[0]
        assert "recapFor = null" in body, fn


# ---- the workbench no longer folds the ledger a second time for a list nothing reads ----
def test_workbench_data_has_no_unread_deliverables_list():
    page = (RUN_DIR / "workbench.html").read_text()
    assert "D.deliverables" not in page
    assert '"deliverables": deliverables(' not in (RUN_DIR / "taska_lib" / "workbench.py").read_text()
