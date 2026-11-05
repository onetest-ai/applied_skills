"""The workbench edits a project's schedules: GET /api/schedules shows them with their next run and what is registered,
POST saves them to taska.toml and registers the jobs. Nothing is launched from a test: registration goes to a tmp
agents dir through a recorder. Invented project only."""
import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from taska_lib import config as C, schedules as SCH, store as S, workbench as W

TOML = '''[project]
name = "Harbor Logistics"
timezone = "Europe/Kyiv"

[run]
scheduled = "python3 bin/weekly.py {kind}"

[[schedules]]
name = "Friday preview"
kind = "preview"
days = [5]
time = "20:00"
'''


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    return port


@pytest.fixture
def served(tmp_path, monkeypatch):
    (tmp_path / "taska.toml").write_text(TOML)
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    calls, agents = [], tmp_path / "agents"
    monkeypatch.setattr(SCH.sys, "platform", "darwin")
    srv = W.server(cfg.store, cfg, _free_port(), register_opts={"agents_dir": agents, "launchctl": lambda *a: calls.append(a)})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path, agents, calls
    finally:
        srv.shutdown()


def _post(base, body, ctype="application/json"):
    req = urllib.request.Request(base + "/api/schedules", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": ctype})
    try:
        r = urllib.request.urlopen(req)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_get_lists_schedules_with_next_run_and_registration(served):
    base, proj, agents, _ = served
    d = json.loads(urllib.request.urlopen(base + "/api/schedules").read())
    assert [s["name"] for s in d["schedules"]] == ["Friday preview"]
    assert d["schedules"][0]["next"] and d["schedules"][0]["days"] == [5] and d["schedules"][0]["include"]["pptx"] is False
    assert d["registered"] == {} and d["platform"] == "darwin" and d["can_register"] is True
    assert d["command"] == "python3 bin/weekly.py {kind}"


def test_post_saves_and_registers(served):
    base, proj, agents, calls = served
    rows = [{"name": "Friday preview", "kind": "preview", "days": [5], "time": "20:00", "to": ["opal@example.com"]},
            {"name": "Monday send", "kind": "send", "days": [1], "time": "14:00", "to": ["reader@example.com"],
             "cc": ["lead@example.com"], "include": {"pptx": True}, "on": True}]
    code, d = _post(base, {"schedules": rows})
    assert code == 200, d
    assert [r["action"] for r in d["registered"]] == ["loaded", "loaded"]
    assert [s.name for s in C.load(proj).schedules] == ["Friday preview", "Monday send"]
    assert (agents / "com.taska.harbor-logistics.monday-send.plist").is_file()
    assert (proj / "taska.toml.bak").read_text() == TOML
    got = json.loads(urllib.request.urlopen(base + "/api/schedules").read())
    assert [s["name"] for s in got["schedules"]] == ["Friday preview", "Monday send"]
    assert set(got["registered"]) == {"com.taska.harbor-logistics.friday-preview", "com.taska.harbor-logistics.monday-send"}


def test_post_with_a_bad_address_changes_nothing(served):
    base, proj, agents, calls = served
    code, d = _post(base, {"schedules": [{"name": "Monday send", "kind": "send", "days": [1], "time": "14:00", "to": ["not-an-address"]}]})
    assert code == 400 and "malformed address" in d["error"]
    assert (proj / "taska.toml").read_text() == TOML and not (proj / "taska.toml.bak").exists() and not agents.exists() and calls == []


def test_post_needs_json(served):
    base, *_ = served
    assert _post(base, {"schedules": []}, ctype="text/plain")[0] == 415


def test_workbench_page_has_no_schedules():
    page = W.page("workbench.html").decode()
    assert 'data-v="schedules"' not in page and "renderSchedules" not in page and "/api/schedules" not in page


def test_schedules_page_is_its_own_page():
    page = W.page("schedules.html").decode()
    assert 'id="schedules"' in page and "/api/schedules" in page and "renderSchedules" in page
    assert "Saving…" in page and 'aria-live="polite"' in page and 'role="alert"' in page
    assert "beforeunload" in page and 'inputmode="email"' in page and 'data-f="owner"' in page and "SCH.owner" in page
    for s in ("/api/schedules/preview", "/api/schedules/delete", "/api/schedules/retire", "Copy into a schedule",
              "two jobs", "<dialog", "not described in taska.toml", 'data-f="tz"', "s.when", "fires_local", "SCH.local_zone"):
        assert s in page, s
    for gone in ("data-review", "data-send", "Send now", "Review first", "&send="):
        assert gone not in page, gone
    assert 'dlg.returnValue = ""' in page and "schErr = esc(p.error" in page and 'get("theme")' in page

def test_tokens_and_helpers_are_inlined_in_both_pages_and_the_export(tmp_path):
    tokens = (W.RUN_DIR / "tokens.css").read_text()
    shared = (W.RUN_DIR / "shared.js").read_text()
    for name in ("workbench.html", "schedules.html"):
        page = W.page(name).decode()
        assert "/*__TOKENS__*/" not in page and "/*__SHARED__*/" not in page and tokens in page and shared in page, name
    (tmp_path / "taska.toml").write_text(TOML)
    cfg = C.load(tmp_path); S.ensure(cfg.store)
    exported = W.export_html(cfg.store, cfg)
    assert tokens in exported and shared in exported and "/*__DATA__*/null" not in exported

def test_the_page_opens_on_a_view_named_in_the_query(served):
    base, *_ = served
    r = urllib.request.urlopen(base + "/?view=schedules")
    assert r.status == 200 and r.headers["Content-Type"].startswith("text/html")


def test_run_now_spawns_the_schedule_detached(tmp_path, monkeypatch):
    (tmp_path / "taska.toml").write_text(TOML)
    cfg = C.load(tmp_path); S.ensure(cfg.store)
    spawned = []
    srv = W.server(cfg.store, cfg, _free_port(), register_opts={"agents_dir": tmp_path / "a", "launchctl": lambda *a: None,
                                                               "spawn": lambda argv, log: spawned.append((argv, log)) or 4242})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        req = urllib.request.Request(base + "/api/schedules/run", data=json.dumps({"name": "Friday preview"}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        d = json.loads(urllib.request.urlopen(req).read())
        assert d["started"] is True and d["pid"] == 4242 and d["week"] and d["dir"].endswith(d["week"])
        argv, log = spawned[0]
        assert argv == ["/bin/zsh", "-lic", "python3 bin/weekly.py preview"] and str(log).endswith("friday-preview.log")   # what launchd runs
        req = urllib.request.Request(base + "/api/schedules/run", data=json.dumps({"name": "nope"}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 400
        got = json.loads(urllib.request.urlopen(base + "/api/schedules").read())
        assert got["reports_dir"] == str(tmp_path / "reports")
    finally:
        srv.shutdown()


def _serve(tmp_path, toml, spawned):
    (tmp_path / "taska.toml").write_text(toml)
    cfg = C.load(tmp_path); S.ensure(cfg.store)
    srv = W.server(cfg.store, cfg, _free_port(), register_opts={"agents_dir": tmp_path / "a", "launchctl": lambda *a: None,
                                                               "spawn": lambda argv, log: spawned.append(argv) or 1})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _run(base, body):
    req = urllib.request.Request(base + "/api/schedules/run", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


TWO_JOBS = TOML + '''
[[schedules]]
name = "Monday report"
kind = "send"
when = "Mon 14:00 Europe/Kyiv"
to = ["reader@example.com"]
cc = ["lead@example.com"]
on = false

[report]
owner = "owner@example.com"
'''


def test_run_now_has_no_send_variant(tmp_path, monkeypatch):
    monkeypatch.setattr(SCH.sys, "platform", "darwin")
    spawned = []
    srv, base = _serve(tmp_path, TWO_JOBS, spawned)
    try:
        _run(base, {"name": "Monday report", "send": True})
        assert spawned[-1][-1] == "python3 bin/weekly.py send"
    finally:
        srv.shutdown()


def test_rows_say_what_each_job_does_and_when_in_its_zone(tmp_path, monkeypatch):
    import datetime as dt
    monkeypatch.setattr(SCH.sys, "platform", "darwin")
    srv, base = _serve(tmp_path, TWO_JOBS, [])
    try:
        d = json.loads(urllib.request.urlopen(base + "/api/schedules").read())
        prev, rep = d["schedules"]
        assert "review" not in rep and rep["when"] == "Mon 14:00 Europe/Kyiv" and rep["tz"] == "Europe/Kyiv"
        assert prev["what"] == "A preview of the week to owner@example.com"
        assert rep["what"] == "The weekly report to reader@example.com, cc lead@example.com: the copy you were mailed this week, held if the tree changed since"
        assert d["owner"] == "owner@example.com" and rep["next"] is None   # off
    finally:
        srv.shutdown()


def test_post_delete_and_retire_routes(tmp_path, monkeypatch):
    import plistlib
    monkeypatch.setattr(SCH.sys, "platform", "darwin")
    (tmp_path / "taska.toml").write_text(TOML)
    cfg = C.load(tmp_path); S.ensure(cfg.store)
    agents, calls = tmp_path / "agents", []
    opts = {"agents_dir": agents, "launchctl": lambda *a: calls.append(a), "state": lambda lbl: {"loaded": True, "last_exit": 0}}
    agents.mkdir()
    foreign = agents / "com.example.nightly.plist"
    foreign.write_bytes(plistlib.dumps({"Label": "com.example.nightly", "ProgramArguments": ["/bin/zsh", "-lic", f"{tmp_path}/bin/run.sh"],
                                         "StartCalendarInterval": {"Hour": 6, "Minute": 40}}))
    srv = W.server(cfg.store, cfg, _free_port(), register_opts=opts)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
        try:
            r = urllib.request.urlopen(req); return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        assert post("/api/schedules", {"schedules": [{"name": "Friday preview", "kind": "preview", "days": [5], "time": "20:00", "to": ["opal@example.com"]}]})[0] == 200
        view = json.loads(urllib.request.urlopen(base + "/api/schedules").read())
        states = {r["schedule"] or r["label"]: r["state"] for r in view["os"]}
        assert states == {"Friday preview": "active", "com.example.nightly": "foreign"}
        code, d = post("/api/schedules/retire", {"label": "com.example.nightly"})
        assert code == 200 and d["action"] == "retired" and not foreign.exists()
        assert post("/api/schedules/retire", {"label": "com.example.nightly"})[0] == 400
        code, d = post("/api/schedules/delete", {"name": "Friday preview"})
        assert code == 200 and C.load(tmp_path).schedules == [] and not list(agents.glob("com.taska.*.plist"))
        assert post("/api/schedules/delete", {"name": "nope"})[0] == 400
    finally:
        srv.shutdown()


def test_schedules_page_is_framable_only_locally(served):
    base, *_ = served
    r = urllib.request.urlopen(base + "/schedules")
    assert r.status == 200 and "frame-ancestors 'self' http://127.0.0.1:* http://localhost:*" in r.headers["Content-Security-Policy"]
    for path in ("/", "/?view=schedules&embed", "/index.html"):   # every HTML page carries it: ?embed shows the same controls
        assert "frame-ancestors 'self'" in urllib.request.urlopen(base + path).headers["Content-Security-Policy"]
    assert "http://[::1]:*" in r.headers["Content-Security-Policy"]
    d = json.loads(urllib.request.urlopen(base + "/api/schedules/preview?name=Friday%20preview").read())
    assert d["schedule"] == "Friday preview" and d["mail"]["include"]


def test_post_refuses_a_send_without_a_reader_or_a_preview_without_an_address(served):
    base, proj, agents, calls = served
    code, d = _post(base, {"schedules": [{"name": "Monday send", "kind": "send", "days": [1], "time": "14:00"}]})
    assert code == 400 and "To address" in d["error"] and (proj / "taska.toml").read_text() == TOML
    code, d = _post(base, {"schedules": [{"name": "Friday preview", "kind": "preview", "days": [5], "time": "20:00"}]})
    assert code == 400 and "owner" in d["error"]
    code, d = _post(base, {"schedules": [{"name": "Friday preview", "kind": "preview", "days": [5], "time": "20:00"}], "owner": "opal@example.com"})
    assert code == 200


# ---------- the page explains itself: will this project send a report, and if not, what is missing ----------

SILENT = '''[project]
name = "Harbor Logistics"

[[schedules]]
name = "Harbor nightly"
kind = "update"
when = "Mon-Fri 06:40 Europe/Kyiv"
to = ["opal@example.com"]
command = "/opt/harbor/bin/full-run.sh"
'''

READY = TOML + '''
[report]
owner = "nora@example.com"
title = "Harbor weekly"
client_label = "Harbor"
greet = "Nora"
'''


def _status(tmp_path, text):
    (tmp_path / "taska.toml").write_text(text)
    return W.schedules_view(C.load(tmp_path), {"agents_dir": tmp_path / "agents"})


def test_a_project_with_only_an_update_job_says_why_no_report_goes_out(tmp_path, monkeypatch):
    monkeypatch.setattr(SCH.sys, "platform", "darwin")
    d = _status(tmp_path, SILENT)
    rs = d["report_status"]
    assert rs["set_up"] is False and [c["id"] for c in rs["checks"]] == ["report_schedule", "owner", "details", "sender"]
    assert [c["state"] for c in rs["checks"]] == ["missing", "missing", "missing", "missing"]
    assert "Harbor nightly" in rs["checks"][0]["text"] and "never mail" in rs["checks"][0]["text"]
    assert rs["project_path"] == str(tmp_path) and rs["toml"].endswith("taska.toml") and rs["label_prefix"] == "com.taska.harbor-logistics."
    row = d["schedules"][0]
    assert row["never_mails"] is True and row["unused_to"] == ["opal@example.com"] and row["absolute_command"] is True


def test_a_project_with_a_report_schedule_owner_details_and_its_own_command_is_set_up(tmp_path, monkeypatch):
    monkeypatch.setattr(SCH.sys, "platform", "darwin")
    rs = _status(tmp_path, READY)["report_status"]
    assert rs["set_up"] is True and all(c["state"] == "ok" for c in rs["checks"])
    assert "python3 bin/weekly.py" in rs["checks"][3]["text"]
    assert rs["report"] == {"owner": "nora@example.com", "title": "Harbor weekly", "client_label": "Harbor", "greet": "Nora"}


def test_without_its_own_command_the_mail_is_written_but_nothing_sends_it(tmp_path, monkeypatch):
    monkeypatch.setattr(SCH.sys, "platform", "linux")
    text = READY.replace('[run]\nscheduled = "python3 bin/weekly.py {kind}"\n', "")
    rs = _status(tmp_path, text)["report_status"]
    assert rs["checks"][3]["state"] == "missing" and "mail.md" in rs["checks"][3]["text"] and rs["set_up"] is False


def test_the_folder_is_shown_from_home_and_never_typed(tmp_path, monkeypatch):
    monkeypatch.setattr(SCH.sys, "platform", "darwin")
    monkeypatch.setattr(W.Path, "home", classmethod(lambda cls: tmp_path.parent))
    assert _status(tmp_path, SILENT)["report_status"]["folder"] == f"~/{tmp_path.name}"


def test_the_last_run_is_the_newest_run_status_with_its_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(SCH.sys, "platform", "darwin")
    for week, ok in (("2026-03-02", False), ("2026-03-09", True)):
        (tmp_path / "reports" / week).mkdir(parents=True)
        (tmp_path / "reports" / week / "run-status.json").write_text(json.dumps(
            {"started": f"{week}T06:40:05", "finished": f"{week}T06:41:00", "steps": [{"id": "pull", "ok": ok, "detail": "done: 0 new file(s)"}]}))
    last = _status(tmp_path, SILENT)["report_status"]["last_run"]
    assert last["week"] == "2026-03-09" and last["steps"] == [{"id": "pull", "ok": True, "detail": "done: 0 new file(s)"}]
    assert last["finished"] == "2026-03-09T06:41:00"


def test_post_saves_the_report_details_and_only_those_keys(served):
    base, proj, agents, calls = served
    rows = [{"name": "Weekly review", "kind": "send", "when": "Mon 14:00 Europe/Kyiv", "to": ["nora@example.com"]}]
    code, d = _post(base, {"schedules": rows, "report": {"owner": "nora@example.com", "title": "Harbor weekly",
                                                          "client_label": "Harbor", "greet": "Nora"}})
    assert code == 200, d
    cfg = C.load(proj)
    assert cfg.report == {"owner": "nora@example.com", "title": "Harbor weekly", "client_label": "Harbor", "greet": "Nora"}
    assert d["report_status"]["set_up"] is True
    text = (proj / "taska.toml").read_text()
    assert text.startswith(TOML.split("[[schedules]]")[0])   # every byte before the schedules is kept


def test_post_refuses_an_unknown_or_multi_line_report_detail_and_writes_nothing(served):
    base, proj, agents, calls = served
    rows = [{"name": "Weekly review", "kind": "send", "when": "Mon 14:00 Europe/Kyiv", "to": ["nora@example.com"]}]
    for bad in ({"footer": "x"}, {"title": "two\nlines"}, {"greet": ""}):
        code, d = _post(base, {"schedules": rows, "report": bad})
        assert code == 400, bad
    assert (proj / "taska.toml").read_text() == TOML and not agents.exists()


def test_report_details_replace_their_lines_in_place_and_add_missing_ones_after_the_header():
    text = '[project]\nname = "x"\n\n[report]\ntitle = "Old"   # kept comment goes\nfooter = "keep me"\n\n[plan]\na = 1\n'
    out = SCH.with_report(text, {"title": "New", "greet": "Nora"})
    assert 'title = "New"\n' in out and 'greet = "Nora"\n' in out and 'footer = "keep me"' in out and out.endswith("[plan]\na = 1\n")
    assert out.index('greet = "Nora"') < out.index("[plan]")
    assert SCH.with_report('[project]\nname = "x"\n', {"owner": "a@example.com"}).endswith('[report]\nowner = "a@example.com"\n')


def test_the_schedules_page_explains_itself_from_the_report_status():
    page = W.page("schedules.html").decode()
    for s in ('id="sch-report"', "report_status", "Set up a weekly report", 'id="sch-setup"', "label_prefix", "rs.folder",
              "never_mails", "unused_to", "absolute_command", "last_run", "written, not sent", "Save and register",
              "schedules --set", "schedules --preview", "watchRun"):
        assert s in page, s
    for typed in ("/Users/", "/home/", "C:\\Users", "~/taska-", "com.taska."):
        assert typed not in page, typed   # every path and job name comes from /api/schedules
