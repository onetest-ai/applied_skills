"""Inputs that used to crash or silently lose data: a leap-day due date, an empty working week, a 7-digit
timestamp fraction, a scheduled run's PATH, a plan without activities, a stuck subprocess, revisions under
a pinned title, and a note without text. All names are invented."""
import textwrap

import pytest

from conftest import run_cli

Q_WAIT = "we are waiting for Opal to reset the passwords before we can pull the stock reports"
Q_LOGIN = "I successfully logged in with the new credentials this morning"


def test_resolve_due_leap_day_rollforward_does_not_crash():
    from taska_lib.text import resolve_due
    # "Feb 29" said in the back half of a leap year rolls to the next (non-leap) year -> must not raise
    assert resolve_due("Feb 29", "2024-09-10") is None


def test_empty_working_days_is_rejected(tmp_path):
    from taska_lib import config as C
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
        working_days = []
        [plan]
        epics = ["E"]
        [[people]]
        name = "A"
        side = "internal"
    """))
    with pytest.raises(C.ConfigError):
        C.load(tmp_path)


def test_code_host_local_handles_azure_7digit_fraction():
    from taska_lib.sources.code_host import _local
    # Azure .NET timestamps carry 7 fractional digits; must parse, not ValueError
    assert _local("2026-01-05T12:00:00.1234567Z", "UTC")[:10] == "2026-01-05"


def test_cron_line_makes_path_available_to_the_run(project):
    line = run_cli(project, "schedule")["line"]
    assert "PATH=" in line
    # the broken form was `PATH=<p> cd <proj> && ... claude` (PATH binds to cd only);
    # the fix puts PATH after the cd, on the actual run.
    assert line.index("PATH=") > line.index("cd "), f"PATH binds only to cd: {line}"


def test_plan_json_without_activities_key_does_not_crash(tmp_path):
    from taska_lib import plan as PL
    (tmp_path / "plan.json").write_text('{"epics":[{"name":"Build"}]}')
    assert PL.load_epics({"file": str(tmp_path / "plan.json")}) == []


def test_media_probe_passes_a_subprocess_timeout(monkeypatch):
    from taska_lib import media
    monkeypatch.setattr(media.shutil, "which", lambda x: "/usr/bin/" + x)
    seen = {}

    class R:
        returncode = 0
        stdout = '{"format": {}, "streams": []}'
        stderr = ""

    monkeypatch.setattr(media.subprocess, "run", lambda cmd, **kw: (seen.update(timeout=kw.get("timeout")), R())[1])
    media.probe("x.mp4")
    assert seen.get("timeout") is not None, "ffprobe runs with no timeout -> can hang the unattended pass"


def test_revise_ac_add_is_audited_even_when_title_pinned(project):
    from taska_lib import config as C, ledger as L
    run_cli(project, "setup"); run_cli(project, "sync")
    cfg = C.load(project); st = cfg.store
    d1, d2 = [x["id"] for x in run_cli(project, "pending")["items"]]
    L.apply(st, cfg, d1, [{"op": "create", "kind": "story", "parent": "E01", "title": "Stock reporting",
                           "ac": ["First report pulled"], "quote": Q_WAIT}])
    L.mark_processed(st, d1)
    L.human_event(st, {"op": "edit", "node": "S001", "set": {"title": "Stock reporting (pinned)"}})
    L.apply(st, cfg, d2, [{"op": "revise", "node": "S001", "title": "Agent rename",
                           "ac_add": ["Numbers reconcile"], "quote": Q_LOGIN}])
    L.mark_processed(st, d2)
    N, *_ = L.fold(st)
    assert N["S001"]["title"] == "Stock reporting (pinned)"                       # pin held
    assert any(a["text"] == "Numbers reconcile" for a in N["S001"]["ac"])         # ac_add applied
    rev = next(h for h in N["S001"]["history"] if h.get("op") == "revise")
    assert rev.get("applied") is True, "ac_add applied but the audit records the revise as not applied"


def test_two_revisions_of_one_node_from_one_quote_both_apply(project):
    from taska_lib import config as C, ledger as L
    run_cli(project, "setup"); run_cli(project, "sync")
    cfg = C.load(project); st = cfg.store
    d1, _ = [x["id"] for x in run_cli(project, "pending")["items"]]
    L.apply(st, cfg, d1, [{"op": "create", "kind": "story", "parent": "E01", "title": "Stock reporting",
                           "ac": ["a"], "quote": Q_WAIT}])
    L.mark_processed(st, d1)
    L.apply(st, cfg, d1, [{"op": "revise", "node": "S001", "title": "Renamed", "quote": Q_WAIT},
                          {"op": "revise", "node": "S001", "ac_add": ["extra criterion"], "quote": Q_WAIT}])
    N, *_ = L.fold(st)
    assert N["S001"]["title"] == "Renamed"
    assert any(a["text"] == "extra criterion" for a in N["S001"]["ac"]), "second revise silently dropped (mid collision)"


def test_note_without_note_text_is_rejected_not_stored_empty(project):
    """A `note` whose text sits under the wrong key ("text") must be rejected so the agent fixes it, not
    stored as an empty note that loses the content silently."""
    from taska_lib import config as C, ledger as L, store as S
    run_cli(project, "setup"); run_cli(project, "sync")
    cfg = C.load(project); st = cfg.store
    d1, _ = [x["id"] for x in run_cli(project, "pending")["items"]]
    L.apply(st, cfg, d1, [{"op": "create", "kind": "story", "parent": "E01", "title": "Stock reporting",
                           "ac": ["a"], "quote": Q_WAIT}])
    r = L.apply(st, cfg, d1, [{"op": "note", "node": "S001", "text": "LCP up 10%", "quote": Q_WAIT}])
    assert r["rejected"] and "note" in r["rejected"][0]["why"], r
    assert not [e for e in S.events(st) if e.get("type") == "note"]
