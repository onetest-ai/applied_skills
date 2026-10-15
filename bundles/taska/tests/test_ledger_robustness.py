"""Ledger robustness: merge cycles, criterion numbering after a drop, idempotent re-apply, human edits
that survive later passes, atomic writes, a safe export and a workbench server that refuses foreign writes.
All names are invented."""
import json
import threading
import time
import http.client
import socket
from pathlib import Path

import pytest

from taska_lib import config as C, ledger as L, store as S, staleness as ST, workbench as W
from taska_lib.text import resolve_due


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "taska.toml").write_text('[project]\nname = "P"\n[[people]]\nname = "Nora Quill"\n')
    c = C.load(tmp_path)
    S.ensure(c.store)
    L.init_epics(c.store, [{"name": "Alpha"}])
    return c


def meeting(cfg, n, text):
    item = {"id": f"2026-03-0{n}_x_{n}", "source": "x", "kind": "meeting", "meeting_kind": "internal",
            "at": f"2026-03-0{n}T10:00:00", "title": f"call {n}",
            "turns": [{"ts": "00:00:01", "speaker": "Nora Quill", "text": text, "bc": False}]}
    S.write_evidence(cfg.store, item)
    return item["id"]


def story(cfg, n, title, quote, ac=("done when shipped",), **kw):
    return {"op": "create", "kind": "story", "parent": "E01", "title": title, "ac": list(ac), "quote": quote, **kw}


def stories(cfg):
    N, *_ = L.fold(cfg.store)
    return {i: n for i, n in N.items() if n["kind"] == "story"}


def verdict(cfg, **by_op):
    mids = [e["mid"] for e in S.events(cfg.store) if e["type"] == "agent" and L.needs_verdict(e)]
    L.verify_apply(cfg.store, [{"mid": m, "verdict": "supports"} for m in mids])


def test_two_accepted_merges_pointing_at_each_other_do_not_hang_the_fold(cfg):
    e1 = meeting(cfg, 1, "Story one is the billing flow and story two is the invoice flow they overlap")
    L.apply(cfg.store, cfg, e1, [story(cfg, 1, "Billing", "Story one is the billing flow"),
                                 story(cfg, 1, "Invoice", "story two is the invoice flow")])
    L.mark_processed(cfg.store, e1)
    e2 = meeting(cfg, 2, "We should merge billing into invoice and actually invoice into billing too")
    L.apply(cfg.store, cfg, e2, [
        {"op": "suggest_merge", "node": "S001", "into": "S002", "quote": "merge billing into invoice"},
        {"op": "suggest_merge", "node": "S002", "into": "S001", "quote": "invoice into billing too"}])
    L.mark_processed(cfg.store, e2)
    for e in [e for e in S.events(cfg.store) if e.get("op") == "suggest_merge"]:
        L.human_event(cfg.store, {"op": "accept", "target": e["mid"]})
    L.human_event(cfg.store, {"op": "edit", "node": "S001", "set": {"title": "Billing and invoicing"}})
    N, *_ = L.fold(cfg.store)  # used to loop forever in resolve()
    assert N["S001"]["merged_into"] == "S002" and not N["S002"]["merged_into"]


def test_criterion_numbers_count_what_the_agent_was_shown_after_a_human_drop(cfg):
    e1 = meeting(cfg, 1, "Story billing needs three things the export the totals and the signoff done")
    L.apply(cfg.store, cfg, e1, [story(cfg, 1, "Billing", "Story billing needs three things",
                                       ac=("export", "totals", "signoff"))])
    L.mark_processed(cfg.store, e1)
    L.human_event(cfg.store, {"op": "ac_set", "node": "S001", "ac": 1, "drop": True})
    e2 = meeting(cfg, 2, "The signoff is confirmed by the client in writing this morning really")
    r = L.apply(cfg.store, cfg, e2, [{"op": "ac_met", "node": "S001", "ac": 2,
                                      "quote": "The signoff is confirmed by the client"}])  # "2." = signoff
    assert not r["rejected"]
    verdict(cfg)
    L.mark_processed(cfg.store, e2)
    ac = {a["text"]: a["met"] for a in L.fold(cfg.store)[0]["S001"]["ac"]}
    assert ac == {"totals": False, "signoff": True}
    assert [e["ac_text"] for e in S.events(cfg.store) if e.get("op") == "ac_met"] == ["signoff"]
    # the verifier is shown the criterion the agent meant, even after a later drop shifts the numbering
    L.human_event(cfg.store, {"op": "ac_set", "node": "S001", "ac": 1, "drop": True})
    L.verify_apply(cfg.store, [])
    packets = L.verify_prep(cfg.store, weak=True)
    assert packets == []  # already judged; claim text is exercised by the next assertion
    ev = [e for e in S.events(cfg.store) if e.get("op") == "ac_met"][0]
    claim = "acceptance criterion 2 met: " + ev["ac_text"]
    assert claim.endswith("signoff")


def test_ac_met_beyond_the_visible_criteria_is_rejected(cfg):
    e1 = meeting(cfg, 1, "Story billing needs one thing only the export to be done fully")
    L.apply(cfg.store, cfg, e1, [story(cfg, 1, "Billing", "Story billing needs one thing")])
    r = L.apply(cfg.store, cfg, e1, [{"op": "ac_met", "node": "S001", "ac": 3, "quote": "needs one thing only the export"}])
    assert r["rejected"] and not r["events"]


def test_a_mention_never_moves_a_date(cfg):
    e1 = meeting(cfg, 1, "Story billing is on the board and nothing else is said here")
    L.apply(cfg.store, cfg, e1, [story(cfg, 1, "Billing", "Story billing is on the board")])
    L.mark_processed(cfg.store, e1)
    e2 = meeting(cfg, 2, "Someone mentioned billing again maybe by 2026-12-24 or so")
    L.apply(cfg.store, cfg, e2, [{"op": "status", "node": "S001", "effect": "mention", "due_text": "2026-12-24",
                                  "quote": "mentioned billing again maybe by 2026-12-24"}])
    assert L.fold(cfg.store)[0]["S001"]["due"] is None


def test_applying_the_same_batch_twice_adds_nothing(cfg):
    e1 = meeting(cfg, 1, "Story billing is on the board and nothing else is said here")
    batch = [story(cfg, 1, "Billing", "Story billing is on the board", ref="a"),
             {"op": "create", "kind": "task", "parent": "a", "type": "action", "title": "Export", "ref": "t",
              "internal_owner": "Nora Quill", "quote": "billing is on the board and nothing"}]
    L.apply(cfg.store, cfg, e1, batch)
    r = L.apply(cfg.store, cfg, e1, batch)  # a crash before `done` makes the next run re-extract the item
    assert r["events"] == 0
    assert sorted(stories(cfg)) == ["S001"]


def test_create_without_a_title_is_rejected_not_a_traceback(cfg):
    e1 = meeting(cfg, 1, "Story billing is on the board and nothing else is said here")
    r = L.apply(cfg.store, cfg, e1, [{"op": "create", "kind": "story", "parent": "E01", "ac": ["x"],
                                      "quote": "Story billing is on the board"}])
    assert r["events"] == 0 and "title" in r["rejected"][0]["why"]


@pytest.mark.parametrize("text", ["2026-02-30", "due 2026-13-01"])
def test_an_impossible_iso_date_is_not_a_date(text):
    assert resolve_due(text, "2026-03-01") is None


def test_a_human_cannot_write_a_value_that_breaks_every_later_pass(cfg):
    e1 = meeting(cfg, 1, "Story billing is on the board and nothing else is said here")
    L.apply(cfg.store, cfg, e1, [story(cfg, 1, "Billing", "Story billing is on the board")])
    L.mark_processed(cfg.store, e1)
    for bad in ({"waiting_for": [1]}, {"due": "2026-02-30"}, {"state": "banana"}, {"title": ""}, {"parent": "X9"}):
        with pytest.raises(ValueError):
            L.human_event(cfg.store, {"op": "edit", "node": "S001", "set": bad})
    L.human_event(cfg.store, {"op": "edit", "node": "S001", "set": {"due": "2026-03-20", "state": "blocked"}})
    ST.classify(cfg.store, cfg)


def test_a_human_unlink_survives_a_later_verified_agent_link(cfg):
    e1 = meeting(cfg, 1, "Story billing and story invoice exist and billing depends on invoice for sure")
    L.apply(cfg.store, cfg, e1, [story(cfg, 1, "Billing", "Story billing and story invoice exist"),
                                 story(cfg, 1, "Invoice", "and story invoice exist and billing")])
    L.mark_processed(cfg.store, e1)
    L.human_event(cfg.store, {"op": "link", "node": "S001", "depends_on": "S002"})
    L.human_event(cfg.store, {"op": "unlink", "node": "S001", "depends_on": "S002"})
    e2 = meeting(cfg, 2, "Billing cannot finish until the invoice flow is ready said again today")
    L.apply(cfg.store, cfg, e2, [{"op": "link", "node": "S001", "depends_on": "S002",
                                  "quote": "Billing cannot finish until the invoice flow is ready"}])
    verdict(cfg)
    L.mark_processed(cfg.store, e2)
    assert L.fold(cfg.store)[0]["S001"]["depends_on"] == []
    L.human_event(cfg.store, {"op": "link", "node": "S001", "depends_on": "S002"})  # the human can still re-link
    assert L.fold(cfg.store)[0]["S001"]["depends_on"] == ["S002"]


def test_dropping_evidence_keeps_human_edits_made_against_it(cfg):
    e1 = meeting(cfg, 1, "Story billing is on the board and nothing else is said here")
    L.apply(cfg.store, cfg, e1, [story(cfg, 1, "Billing", "Story billing is on the board")])
    L.mark_processed(cfg.store, e1)
    e2 = meeting(cfg, 2, "A short unrelated call about the weather and lunch plans for everybody")
    L.mark_processed(cfg.store, e2)
    L.human_event(cfg.store, {"op": "edit", "node": "S001", "set": {"title": "Billing (Q3)"}})
    (cfg.store / "evidence" / (e2 + ".json")).unlink()  # what `redo --drop` does
    n = L.fold(cfg.store)[0]["S001"]
    assert n["title"] == "Billing (Q3)" and "title" in n["pinned"]


def test_events_are_appended_in_one_write(cfg, monkeypatch):
    calls = []
    real = S.os.write
    monkeypatch.setattr(S.os, "write", lambda fd, b: calls.append(len(b)) or real(fd, b))
    S.append(cfg.store, [{"type": "note", "n": i, "pad": "x" * 5000} for i in range(10)])  # ~50 KB
    assert len(calls) == 1
    assert len(S.events(cfg.store)) == 11  # + the init_epics event


def test_a_malformed_item_leaves_no_half_written_evidence(cfg):
    item = {"id": "2026-03-05_ingest_ab", "source": "ingest", "kind": "meeting", "at": "2026-03-05T10:00:00",
            "title": "t", "turns": [{"speaker": "A", "text": "hello world there"}]}  # no ts
    with pytest.raises(KeyError):
        S.write_evidence(cfg.store, item)
    assert not list((cfg.store / "evidence").iterdir())


def test_export_cannot_be_broken_out_of_its_script_block(cfg, tmp_path):
    e1 = meeting(cfg, 1, "Hello <!--<script> this is a long enough sentence and a few more words")
    L.apply(cfg.store, cfg, e1, [story(cfg, 1, "<!--<script>", "Hello <!--<script> this is a long")])
    L.mark_processed(cfg.store, e1)
    html = Path(W.export(cfg.store, cfg, tmp_path / "w.html")).read_text()
    blob = html[html.index("const EMBED = ") + len("const EMBED = "):]
    blob = blob[:blob.index("\n")]
    assert "<" not in blob and json.loads(blob.rstrip(";"))["project"] == "P"


@pytest.fixture
def server(cfg):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    threading.Thread(target=W.serve, args=(cfg.store, cfg, port), daemon=True).start()
    time.sleep(0.4)
    return cfg, port


def call(port, method, path, body=None, **headers):
    c = http.client.HTTPConnection("127.0.0.1", port)
    h = {"Host": f"127.0.0.1:{port}", **headers}
    c.request(method, path, body=body, headers=h, **({} if "Host" not in headers else {}))
    r = c.getresponse()
    return r.status, r.read()


def test_workbench_refuses_cross_site_writes_and_foreign_hosts(server):
    cfg, port = server
    e1 = meeting(cfg, 1, "Story billing is on the board and nothing else is said here")
    L.apply(cfg.store, cfg, e1, [story(cfg, 1, "Billing", "Story billing is on the board")])
    L.mark_processed(cfg.store, e1)
    body = json.dumps({"op": "edit", "node": "S001", "set": {"title": "HIJACKED"}})
    # a simple cross-site POST: text/plain needs no CORS preflight
    assert call(port, "POST", "/api/event", body, **{"Content-Type": "text/plain", "Origin": "https://evil.example"})[0] in (403, 415)
    assert call(port, "POST", "/api/event", body, **{"Content-Type": "application/json", "Origin": "https://evil.example"})[0] == 403
    assert call(port, "GET", "/api/data", **{"Host": "attacker.rebind.example"})[0] == 403  # DNS rebinding
    assert L.fold(cfg.store)[0]["S001"]["title"] == "Billing"
    assert call(port, "GET", "/api/data")[0] == 200
    ok = call(port, "POST", "/api/event", body, **{"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
    assert ok[0] == 200 and L.fold(cfg.store)[0]["S001"]["title"] == "HIJACKED"


def test_ingest_reports_bad_items_instead_of_crashing(cfg, tmp_path):
    import subprocess, sys
    from conftest import RUN_DIR
    f = tmp_path / "in.json"
    f.write_text(json.dumps(["oops", {"kind": "meeting", "at": "not-a-date", "title": "t", "text": "x"},
                             {"kind": "meeting", "at": "2026-03-05T10:00:00", "title": "ok call",
                              "turns": [{"speaker": "Nora Quill", "text": "hello there everyone"}]}]))
    r = subprocess.run([sys.executable, str(RUN_DIR / "taska.py"), "--project", str(cfg.project_dir),
                        "ingest", "--file", str(f)], capture_output=True, text=True)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and len(out["new"]) == 1 and len(out["rejected"]) == 2


def test_concurrent_writers_never_hand_out_the_same_id(cfg):
    e1 = meeting(cfg, 1, "Story billing is on the board and nothing else is said here")
    L.apply(cfg.store, cfg, e1, [story(cfg, 1, "Billing", "Story billing is on the board")])
    L.mark_processed(cfg.store, e1)
    errs = []

    def add(i):
        try:
            L.human_event(cfg.store, {"op": "create", "kind": "task", "parent": "S001", "title": f"t{i}", "type": "action"})
        except Exception as e:  # pragma: no cover
            errs.append(e)
    ts = [threading.Thread(target=add, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ids = [e["id"] for e in S.events(cfg.store) if e["type"] == "create" and e["kind"] == "task"]
    assert not errs and len(ids) == 12 and len(set(ids)) == 12
