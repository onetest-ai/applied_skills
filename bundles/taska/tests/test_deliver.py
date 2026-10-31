"""Delivery (taska_lib/deliver.py): a week's mail content as deliver.json (the shape the mail step and the dashboard
read) and a ready-to-send mail.md the owner can paste, with exactly the attachments the schedule's Include checklist
selects and that exist. The send decision: only the file the newest gate passed, only while the ledger is unchanged
since that gate, never the same week twice; the owner is always in cc of a live send. Invented data only."""
import json
import textwrap

from taska_lib import config as C, deliver as D, store as S
from taska_lib.schedules import Schedule

OK = {"ok": True, "why": "gated by presend-x"}


def test_a_gated_unchanged_unsent_file_is_sent():
    assert D.decide(OK, ledger_mtime=100, gate_mtime=200, sent=False)[0] == "send"


def test_a_blocked_gate_holds():
    act, why = D.decide({"ok": False, "why": "blocked by G3"}, 100, 200, False)
    assert act == "hold" and "G3" in why


def test_a_ledger_changed_after_the_gate_holds():
    act, why = D.decide(OK, ledger_mtime=300, gate_mtime=200, sent=False)
    assert act == "hold" and "changed" in why


def test_a_week_already_sent_is_never_sent_again_even_as_a_new_file():
    assert D.decide(OK, 100, 200, sent=True)[0] == "skip"
    assert D.decide(OK, 100, 200, sent=True, resend=True)[0] == "send"


def test_the_newest_report_docx_is_picked_never_a_word_lock_file(tmp_path):
    for n in ["Harbor weekly report 2026-03-08.docx", "~$rbor weekly report 2026-03-09.docx", "Harbor weekly report 2026-03-09.docx"]:
        (tmp_path / n).write_bytes(b"x")
    assert D.report_docx(tmp_path).name == "Harbor weekly report 2026-03-09.docx"


def test_live_mode_sends_to_the_reader_with_the_owner_always_in_cc():
    assert D.recipients("live", "opal@x", ["nora@x", "ravi@x"], "nora@x") == (["opal@x"], ["nora@x", "ravi@x"], "")
    assert D.recipients("live", "opal@x", [], "nora@x")[1] == ["nora@x"]
    assert D.recipients("live", "opal@x", ["NORA@x", "ravi@x"], "nora@x")[1] == ["NORA@x", "ravi@x"]   # never twice


def test_shadow_mode_sends_to_the_owner_only_and_says_who_it_was_for():
    to, cc, prefix = D.recipients("shadow", "opal@x", ["nora@x", "ravi@x"], "nora@x")
    assert to == ["nora@x"] and cc == [] and prefix == "[Shadow: would go to opal@x, cc nora@x, ravi@x] "
    assert D.recipients("", "opal@x", [], "nora@x")[0] == ["nora@x"]


def test_the_mode_file_defaults_to_shadow(tmp_path):
    assert D.read_mode(tmp_path / "missing.json") == "shadow"
    (tmp_path / "m.json").write_text('{"mode": "live"}')
    assert D.read_mode(tmp_path / "m.json") == "live"


def test_a_mail_accepted_for_some_recipients_counts_as_sent():
    assert D.was_sent(0) and D.was_sent(14) and not D.was_sent(15) and not D.was_sent(1)


def test_a_held_notice_needs_nothing_but_the_reason():
    d = D.minimal_held("2026-03-02", "opal@x", "the content could not be built", owner="nora@x", title="Harbor weekly report")
    assert d["to"] == ["nora@x"] and "could not be built" in d["lead"] and "opal@x" in d["subject"] and "Harbor" in d["subject"]


# ---- the week's content: deliver.json and mail.md ----

def _project(tmp_path):
    (tmp_path / "taska.toml").write_text(textwrap.dedent('''
        [project]
        name = "Harbor Logistics"
        [[people]]
        name = "Nora Quill"
        role = "Delivery lead"
        [report]
        title = "Harbor North Star"
        greet = "Opal"
        sender = "Nora Quill"
        org = "Harbor Logistics"
        folder_url = "https://example.sharepoint.com/sites/harbor/weekly/{week}"
    '''))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    S.append(cfg.store, [{"type": "create", "mid": "c1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None,
                          "date": None, "fields": {"title": "Yard", "ac": [], "depends_on": []}}])
    wk = tmp_path / "reports" / "2026-03-02"
    wk.mkdir(parents=True)
    return cfg, wk


def test_a_quiet_week_still_writes_mail_md(tmp_path):
    cfg, wk = _project(tmp_path)              # no prose, no docx: nothing happened this week
    d = D.deliver(cfg, wk, "opal@example.com")
    md = (wk / "mail.md").read_text()
    assert d["subject"] == "Harbor North Star – Weekly status, Mar 2–6, 2026" and d["attach"] == [] and d["link"] is None
    assert md.startswith("Subject: Harbor North Star – Weekly status, Mar 2–6, 2026\nTo: opal@example.com\n")
    assert "Nothing changed this week" in md and "Attachments: none" in md and "Hi Opal" in md


def test_mail_md_lists_only_existing_included_files(tmp_path):
    cfg, wk = _project(tmp_path)
    (wk / "Harbor weekly report 2026-03-06.docx").write_bytes(b"d")
    (wk / "workbench 2026-03-06.html").write_bytes(b"w")        # no deck on disk
    (wk / "prose.json").write_text(json.dumps({"lead": "A good week [T001].", "slide3": {"summary": ["Yard plan shared [T001]."]}, "priorities": []}))
    s = Schedule(name="Monday send", kind="send", days=(1,), time="14:00", to=["opal@example.com"], cc=["ravi@example.com"],
                 include={"body": True, "docx": True, "link": True, "pptx": True, "workbench": True})
    d = D.deliver(cfg, wk, "opal@example.com", schedule=s)
    md = (wk / "mail.md").read_text()
    assert [p.split("/")[-1] for p in d["attach"]] == ["Harbor weekly report 2026-03-06.docx", "workbench 2026-03-06.html"]
    assert "Cc: ravi@example.com" in md and "- Harbor weekly report 2026-03-06.docx" in md and "- workbench 2026-03-06.html" in md
    assert ".pptx" not in md and "A good week." in md and "[T001]" not in md
    assert d["link"] == "https://example.sharepoint.com/sites/harbor/weekly/2026-03-02"


def test_a_failed_step_makes_the_mail_need_attention(tmp_path):
    cfg, wk = _project(tmp_path)
    status = wk / "run-status.json"
    status.write_text(json.dumps({"steps": [{"id": "sync", "ok": False, "detail": "folder unreadable"}, {"id": "sync", "ok": True, "detail": "later ok"}]}))
    d = D.deliver(cfg, wk, "opal@example.com", status=status)
    assert d["needs_attention"] is False                 # a step is judged by its latest result
    status.write_text(json.dumps({"steps": [{"id": "gate", "ok": False, "detail": "G4"}]}))
    d = D.deliver(cfg, wk, "opal@example.com", status=status)
    assert d["needs_attention"] is True and d["hold_reason"] == "gate: G4"


def test_the_cli_writes_deliver_json_and_mail_md(tmp_path):
    from conftest import run_cli
    cfg, wk = _project(tmp_path)
    r = run_cli(tmp_path, "deliver", "2026-03-02", "--to", "opal@example.com", "--reports", str(tmp_path / "reports"))
    assert r["deliver"].endswith("deliver.json") and r["failed"] == 0 and (wk / "mail.md").exists()
