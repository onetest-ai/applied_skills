import json

import pytest

from taska_lib import config as C, store as S
from taska_lib.sources import code_host, msgraph
from conftest import run_cli

GH_PRS = [{"number": 7, "title": "Add stock report export", "body": "Closes #3. Exports the daily stock report.",
           "state": "MERGED", "author": {"login": "rtamsin"}, "createdAt": "2026-03-02T10:00:00Z",
           "updatedAt": "2026-03-03T09:00:00Z", "mergedAt": "2026-03-03T09:00:00Z", "closedAt": "2026-03-03T09:00:00Z",
           "url": "https://github.example/o/r/pull/7", "headRefName": "feat/stock-export"}]
GH_ISSUES = [{"number": 3, "title": "Stock report export", "body": "", "state": "CLOSED", "author": {"login": "nquill"},
              "createdAt": "2026-03-01T10:00:00Z", "updatedAt": "2026-03-03T09:01:00Z", "closedAt": "2026-03-03T09:01:00Z",
              "url": "https://github.example/o/r/issues/3", "labels": [{"name": "reporting"}]}]


def _fake(responses):
    calls = []

    def run(args, timeout=120):
        calls.append(args)
        for k, v in responses.items():
            if k in " ".join(args):
                return v
        return []
    return run, calls


@pytest.fixture
def cfg(project):
    c = C.load(project)
    c.people.append(C.Person("Ravi Tamsin", "internal", ["rtamsin"]))
    S.ensure(c.store)
    return c


def test_github_prs_and_issues_become_evidence_once_per_state(cfg, monkeypatch):
    run, calls = _fake({"pr list": GH_PRS, "issue list": GH_ISSUES})
    monkeypatch.setitem(code_host.FETCH, "github", lambda scfg, since: code_host.fetch_github(scfg, since, run=run))
    scfg = {"adapter": "code_host", "provider": "github", "repo": "o/r", "since": "2026-03-01"}
    cur, rep = code_host.sync(cfg, "app", scfg, cfg.store, None)
    items = [S.evidence(cfg.store)[i] for i in rep["new"]]
    pr = next(x for x in items if x["kind"] == "pr")
    assert pr["text"].startswith("Pull request #7 “Add stock report export” merged (2026-03-03; by Ravi Tamsin; feat/stock-export).")
    assert "Closes #3" in pr["text"] and pr["url"].endswith("/pull/7")
    assert any(x["kind"] == "issue" and x["meta"]["state"] == "closed" for x in items)
    assert "updated:>=2026-03-01" in " ".join(calls[0])
    assert code_host.sync(cfg, "app", scfg, cfg.store, cur)[1]["new"] == []  # same state: nothing new


def test_gitlab_and_azure_map_to_the_same_shape():
    run, _ = _fake({"mr list": [{"iid": 12, "title": "Fix routing", "description": "d", "state": "merged",
                                 "author": {"username": "a"}, "created_at": "2026-03-02T00:00:00Z",
                                 "updated_at": "2026-03-04T00:00:00Z", "merged_at": "2026-03-04T00:00:00Z",
                                 "closed_at": None, "web_url": "u", "source_branch": "fix"}],
                    "issue list": []})
    gl = code_host.fetch_gitlab({"repo": "g/p"}, "2026-03-01", run=run)
    assert gl[0]["key"] == "mr!12" and gl[0]["kind"] == "pr" and gl[0]["merged"]
    run, _ = _fake({"repos pr list": [{"pullRequestId": 5, "title": "Billing API", "description": "", "status": "completed",
                                       "createdBy": {"displayName": "Ada"}, "creationDate": "2026-03-02T00:00:00Z",
                                       "closedDate": "2026-03-05T00:00:00Z", "repository": {"webUrl": "https://dev.azure.example/r"},
                                       "sourceRefName": "refs/heads/billing"}],
                    "boards query": [{"id": 42, "fields": {"System.Title": "Billing API", "System.State": "Closed",
                                                           "System.ChangedDate": "2026-03-05T00:00:00Z",
                                                           "System.WorkItemType": "User Story"}}]})
    az = code_host.fetch_azure({"organization": "https://dev.azure.example/org", "project": "P"}, "2026-03-01", run=run)
    pr, wi = az
    assert pr["state"] == "merged" and pr["url"].endswith("/pullrequest/5") and pr["ref"] == "billing"
    assert wi["key"] == "wi#42" and wi["state"] == "closed" and wi["ref"] == "User Story"


def test_missing_cli_is_reported(cfg, monkeypatch):
    monkeypatch.setattr(code_host.shutil, "which", lambda x: None)
    _, rep = code_host.sync(cfg, "app", {"provider": "gitlab", "repo": "g/p"}, cfg.store, None)
    assert rep["status"] == "unavailable" and "glab" in rep["reason"]


def test_teams_transcript_found_by_title_and_time(monkeypatch, tmp_path):
    msgraph._listing_cache.clear()
    listing = ("Meeting: Harbor Ops Session - Dispatch\nStart  : 2026-03-09 15:05\nMeeting ID: M1\n"
               "Transcript ID: T1  Created: 2026-03-09 15:01\n"
               "Meeting: Harbor Ops Session - Billing\nStart  : 2026-03-09 11:00\nMeeting ID: M2\n"
               "Transcript ID: T2  Created: 2026-03-09 11:02\n")
    seen = []

    def run(scfg, *args, timeout=120):
        seen.append(args)
        if "--vtt" in args:
            (tmp_path / "out.vtt").write_text("WEBVTT\n")
            return ""
        return listing if args[-1] == "Harbor" else ""
    monkeypatch.setattr(msgraph, "run", run)
    got = msgraph.find_teams_transcript({}, "Mar 9 Harbor Ops Session - Dispatch-20260309_150210UTC-Meeting Recording",
                                        "2026-03-09T15:02:10", str(tmp_path / "out.vtt"))
    assert got == "Harbor Ops Session - Dispatch" and ("--transcript", "T1") == seen[-1][3:5]
    assert msgraph.find_teams_transcript({}, "Harbor Ops Session - Dispatch", "2026-03-09T07:00:00",
                                         str(tmp_path / "x.vtt")) is None  # too far in time


def test_ingest_takes_agent_fetched_items(project, tmp_path):
    run_cli(project, "setup")
    f = tmp_path / "items.json"
    f.write_text(json.dumps([{"kind": "pr", "at": "2026-03-03T09:00:00", "title": "#9 Billing API",
                              "text": "Pull request #9 merged: billing API live.", "url": "https://x/9"},
                             {"kind": "nonsense", "at": "2026-03-03", "title": "x", "text": "y"}]))
    r = run_cli(project, "ingest", "--file", str(f), "--source", "github_mcp")
    assert len(r["new"]) == 1 and r["rejected"][0]["i"] == 1
    assert run_cli(project, "ingest", "--file", str(f), "--source", "github_mcp")["new"] == []


def test_people_lists_unknown_speakers_with_likely_side(project):
    run_cli(project, "sync")
    r = run_cli(project, "people")
    assert r["known"] == 3 and r["unknown"] == []  # every speaker in the fixture is in [[people]] (Teams suffix too)
    import textwrap
    (project / "calls" / "Mar 6 Harbor weekly status.vtt").write_text(textwrap.dedent("""\
        WEBVTT

        00:00:01.000 --> 00:00:05.000
        <v Lena Ostrova>We will send the dock schedule export by Friday afternoon.</v>
        """))
    run_cli(project, "sync")
    u = run_cli(project, "people")["unknown"]
    assert u[0]["name"] == "Lena Ostrova" and u[0]["likely_side"] == "client"


def test_whisper_hallucination_phrases_dropped_only_from_unlabelled_speech():
    from taska_lib import transcripts as TR
    cues = [{"start": 0, "speaker": "", "text": "We moved the cutover to May."},
            {"start": 70, "speaker": "", "text": "Thanks for watching!"},
            {"start": 80, "speaker": "Nora Quill", "text": "Thanks for watching!"}]
    turns = TR.stitch(cues)
    assert [t["text"] for t in turns] == ["We moved the cutover to May.", "Thanks for watching!"]
    assert turns[1]["speaker"] == "Nora Quill"
