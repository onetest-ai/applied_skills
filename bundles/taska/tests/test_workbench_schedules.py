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
    rows = [{"name": "Friday preview", "kind": "preview", "days": [5], "time": "20:00"},
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


def test_page_has_the_schedules_panel():
    page = W.PAGE.read_text()
    assert 'id="schedules"' in page and "/api/schedules" in page and 'data-v="schedules"' in page and "renderSchedules" in page
    assert "Send now" in page and 'data-send=' in page and "Review first" in page and "Send to the reader automatically" in page
    assert "SCH.timezone" in page and "s.what" in page   # the sentence and the machine's time come from the API, not the page
    assert "SCH.owner" in page   # a preview with no To shows the owner it goes to
    assert 'data-f="owner"' in page   # the owner address is edited here, saved with Save


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


REVIEWED = TOML.replace("{kind}", "{kind} {flags}") + '''
[[schedules]]
name = "Monday send"
kind = "send"
days = [1]
time = "14:00"
to = ["reader@example.com"]
cc = ["lead@example.com"]
review = true

[report]
owner = "owner@example.com"
'''


def test_send_now_adds_the_flag_and_run_now_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr(SCH.sys, "platform", "darwin")
    spawned = []
    srv, base = _serve(tmp_path, REVIEWED, spawned)
    try:
        _run(base, {"name": "Monday send", "send": True})
        _run(base, {"name": "Monday send"})
        assert [a[-1] for a in spawned] == ["python3 bin/weekly.py send --send", "python3 bin/weekly.py send"]
    finally:
        srv.shutdown()


def test_rows_say_what_each_job_does_and_the_owner(tmp_path, monkeypatch):
    import datetime as dt
    monkeypatch.setattr(SCH.sys, "platform", "darwin")
    srv, base = _serve(tmp_path, REVIEWED, [])
    try:
        d = json.loads(urllib.request.urlopen(base + "/api/schedules").read())
        prev, send = d["schedules"]
        assert prev["review"] is False and send["review"] is True
        assert prev["what"] == "A preview of the week to owner@example.com"
        assert send["what"] == "The weekly report to reader@example.com, cc lead@example.com, after your review"
        assert d["owner"] == "owner@example.com" and d["timezone"] == dt.datetime.now().astimezone().tzname()
        nxt = dt.datetime.fromisoformat(send["next"])
        assert nxt.isoweekday() == 1 and nxt.strftime("%H:%M") == "14:00" and nxt.utcoffset() == dt.datetime.now().astimezone().utcoffset()
    finally:
        srv.shutdown()


def test_post_saves_the_owner(served):
    base, proj, agents, calls = served
    code, d = _post(base, {"schedules": [{"name": "Friday preview", "kind": "preview", "days": [5], "time": "20:00"}], "owner": "opal@example.com"})
    assert code == 200 and d["owner"] == "opal@example.com" and C.load(proj).report["owner"] == "opal@example.com"
    code, d = _post(base, {"schedules": [], "owner": "nope"})
    assert code == 400 and "owner" in d["error"] and C.load(proj).report["owner"] == "opal@example.com"
