"""The workbench says which code it runs, so a server started before a deploy can never pass for a current one."""
import json
import re
import shutil
import socket
import threading
import urllib.request
from pathlib import Path

from taska_lib import workbench as W


def _free_port():
    # the server accepts only its own Host/Origin (127.0.0.1:<port>), so it must know the real port: never 0
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    return port

RUN_DIR = Path(W.__file__).resolve().parent.parent


def _copy_run_dir(tmp_path):
    dst = tmp_path / "run"
    shutil.copytree(RUN_DIR, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def test_code_version_changes_when_a_code_file_changes(tmp_path):
    run = _copy_run_dir(tmp_path)
    before = W.code_version(run)
    assert len(before) == 10 and W.code_version(run) == before
    (run / "taska_lib" / "ledger.py").write_text((run / "taska_lib" / "ledger.py").read_text() + "\n# changed\n")
    assert W.code_version(run) != before


def test_version_info_flags_a_stale_server():
    assert W.version_info("abc", "abc", "2026-03-02T10:00:00")["stale"] is False
    stale = W.version_info("abc", "def", "2026-03-02T10:00:00")
    assert stale == {"running": "abc", "on_disk": "def", "started": "2026-03-02T10:00:00", "stale": True}


def test_api_version_and_api_data_report_the_code(project):
    from taska_lib import config as C
    cfg = C.load(project)
    srv = W.server(cfg.store, cfg, _free_port())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        v = json.loads(urllib.request.urlopen(base + "/api/version").read())
        assert v["running"] == W.code_version(RUN_DIR) and v["stale"] is False
        d = json.loads(urllib.request.urlopen(base + "/api/data").read())
        assert d["code"]["running"] == v["running"]
    finally:
        srv.shutdown()


def test_page_shows_a_banner_for_stale_code():
    page = (RUN_DIR / "workbench.html").read_text()
    assert 'id="stale"' in page and "D.code && D.code.stale" in page
    assert "This workbench runs older code" in page


def test_a_static_export_says_it_cannot_save_edits(tmp_path):
    # an exported page looked editable but dropped every edit without a word (Oct 26: four status changes lost)
    page = (RUN_DIR / "workbench.html").read_text()
    assert 'id="readonly"' in page and "This is a read-only copy" in page
    send = page[page.index("async function send"):page.index("async function send") + 400]
    assert "if (!LIVE) return;" not in send and "read-only copy" in send
    bulk = page[page.index("async function acceptAll"):page.index("async function acceptAll") + 300]
    assert "read-only copy" in bulk


def test_the_past_call_notice_says_what_editing_does():
    # the notice said "Edits apply to the latest call" while the item editor was disabled on a past call (Oct 26);
    # since edits work from any view (test_workbench_timeline.py), the notice says where an edit goes
    page = (RUN_DIR / "workbench.html").read_text()
    assert "Edits apply to the latest call" not in page and "Editing is off" not in page
    assert "You can still edit: changes apply to the current tree" in page


def test_the_latest_step_shows_an_edit_stamped_on_a_hidden_message(tmp_path):
    # a Teams message that produced no proposal is not a timeline step, but the ledger stamps your edit with it when it
    # is the last processed item: the page then never showed the edit (Oct 26: T114 "done" saved, still "In progress")
    import textwrap
    from taska_lib import config as C, ledger as L, store as S
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
        [[people]]
        name = "Nora Quill"
    """))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    S.write_evidence(cfg.store, {"id": "2026-03-02_meetings_aa000001", "kind": "meeting", "at": "2026-03-02T10:00:00",
                                 "title": "Daily sync", "source": "meetings", "text": "x"})
    S.write_evidence(cfg.store, {"id": "2026-03-03_teams_bb000002", "kind": "message", "at": "2026-03-03T09:00:00",
                                 "title": "Thanks", "source": "teams", "text": "thanks"})
    S.append(cfg.store, [
        {"type": "create", "mid": "e1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None, "date": "2026-03-01",
         "fields": {"title": "Ops"}},
        {"type": "create", "mid": "s1", "id": "S001", "kind": "story", "by": "agent", "evidence": "2026-03-02_meetings_aa000001",
         "date": "2026-03-02", "fields": {"title": "Dock plan", "parent": "E01", "ac": ["x"]}},
        {"type": "processed", "evidence": "2026-03-02_meetings_aa000001", "by": "agent"},
        {"type": "processed", "evidence": "2026-03-03_teams_bb000002", "by": "agent"}])
    e = L.human_event(cfg.store, {"op": "edit", "node": "S001", "set": {"state": "done"}})
    assert e["evidence"] == "2026-03-03_teams_bb000002"
    d = W.data(cfg.store, cfg)
    cur = {}
    for c in d["calls"]:
        s = d["snaps"][c["id"]]
        cur.update(s.get("nodes") or {})
        for g in s.get("gone") or []:
            cur.pop(g, None)
    assert cur["S001"]["state"] == "done"


def test_a_wait_on_a_person_is_shown_and_can_be_cleared(tmp_path):
    import textwrap
    from taska_lib import config as C, ledger as L, store as S
    (tmp_path / "taska.toml").write_text('[project]\nname = "Harbor Logistics"\n[[people]]\nname = "Nora Quill"\n')
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    S.write_evidence(cfg.store, {"id": "2026-03-02_meetings_aa000001", "kind": "meeting", "at": "2026-03-02T10:00:00",
                                 "title": "Daily sync", "source": "meetings", "text": "x"})
    S.append(cfg.store, [
        {"type": "create", "mid": "e1", "id": "E01", "kind": "epic", "by": "plan", "evidence": None, "date": "2026-03-01",
         "fields": {"title": "Ops"}},
        {"type": "create", "mid": "s1", "id": "S001", "kind": "story", "by": "agent", "evidence": "2026-03-02_meetings_aa000001",
         "date": "2026-03-02", "fields": {"title": "Dock plan", "parent": "E01", "ac": ["x"]}},
        {"type": "agent", "op": "waiting", "mid": "w1", "by": "agent", "evidence": "2026-03-02_meetings_aa000001",
         "date": "2026-03-02", "node": "S001", "who": "Nora Quill", "what": "the dock slot", "quote": "x"},
        {"type": "verdict", "mid": "w1", "verdict": "supports"},
        {"type": "processed", "evidence": "2026-03-02_meetings_aa000001", "by": "agent"}])
    assert L.fold(cfg.store)[0]["S001"]["waiting_for"]["who"] == "Nora Quill"
    L.human_event(cfg.store, {"op": "edit", "node": "S001", "set": {"waiting_for": None}})
    assert L.fold(cfg.store)[0]["S001"]["waiting_for"] is None
    page = (RUN_DIR / "workbench.html").read_text()
    assert 'id="f-wait-clear"' in page and "set:{waiting_for:null}" in page


def test_the_download_button_returns_a_read_only_copy_to_share(project):
    # Oct 29: a manager wants to share a project's workbench with a teammate as an example, as it is, with one click
    from taska_lib import config as C
    cfg = C.load(project)
    srv = W.server(cfg.store, cfg, _free_port())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/export")
        body = r.read().decode()
        assert r.status == 200 and "attachment" in r.headers["Content-Disposition"]
        assert "harbor-logistics-workbench-" in r.headers["Content-Disposition"]
        assert "const EMBED = /*__DATA__*/null" not in body and "Harbor Logistics" in body   # data embedded: read-only
    finally:
        srv.shutdown()
    page = (RUN_DIR / "workbench.html").read_text()
    assert 'id="download"' in page and "/api/export" in page


def test_the_recap_is_served_for_a_period_and_embedded_in_a_copy(project):
    # Oct 29, a new owner: "hard to see what to look for and where": the recap (taska pack) is the key-items view
    from taska_lib import config as C
    cfg = C.load(project)
    srv = W.server(cfg.store, cfg, _free_port())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        r = json.loads(urllib.request.urlopen(base + "/api/recap?days=14").read())
        assert r["days"] == 14 and "status pack" in r["markdown"].lower()     # (no meeting read yet in this fixture)
        assert json.loads(urllib.request.urlopen(base + "/api/recap").read())["days"] == 7
    finally:
        srv.shutdown()
    assert "status pack" in W.data(cfg.store, cfg)["recap"].lower()          # a downloaded copy has it too


def test_the_workbench_opens_on_the_recap_and_remembers_the_view():
    page = (RUN_DIR / "workbench.html").read_text()
    assert 'data-v="recap"' in page and "renderRecap" in page and "/api/recap" in page
    assert 'view = store.get("taska.view") || "recap"' in page


def test_the_saved_view_is_read_only_after_the_store_helper_exists():
    # Oct 29: reading the saved view before `const store` was defined stopped the whole page script (blank workbench)
    page = (RUN_DIR / "workbench.html").read_text()
    assert page.index("const store = {get(k)") < page.index('view = store.get("taska.view")')


def test_criteria_checkboxes_are_locked_on_an_earlier_call():
    # a checkbox sends its criterion's number; on an earlier call the numbers can differ from the current tree
    # (a criterion dropped since), so ticking there could mark a different criterion. Everything else stays
    # editable from any view (test_workbench_timeline.py); only these wait for the latest call.
    page = (RUN_DIR / "workbench.html").read_text()
    box = next(l for l in page.splitlines() if 'type="checkbox" data-ac=' in l)
    assert "${acLock}" in box
    assert re.search(r"const acLock = dis \|\| \(!latest\(\) \? `disabled title=\"[^\"]*latest call", page)
