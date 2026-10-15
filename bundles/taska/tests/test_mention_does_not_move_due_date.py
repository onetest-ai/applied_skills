"""End-to-end reproduction for finding B3. All data below is invented (same fictional world as
conftest.py); it must never contain client-identifying content — this is a public repo.

Scenario: a story exists, and later someone merely *mentions* a closed pull request in passing,
citing a date it had been targeted for. The extraction agent is told (EXTRACT.md) to use
effect="mention" for a closed-unmerged PR — "referred to, no change".

The taska invariant (CLAUDE.md) says an unverified claim "changes nothing". But a `mention` is
not in the verifier's STATUS set, so it skips verification AND `_apply_status` still appends the
resolved date to `due_history`. Result: a passing remark sets a due date on the story, with no
verdict — and that date drives overdue / slipping / the asks.

This test asserts the CORRECT behavior (a mention leaves the due unset). It FAILS on the
as-submitted code, which is the reproduction. Throwaway repro — convert to a regression test
alongside the fix, or delete.
"""
import json
import textwrap

from conftest import run_cli

TRANSCRIPT = """WEBVTT

00:00:05.000 --> 00:00:14.000
<v Nora Quill>The main job this sprint is to automate the weekly stock report so we stop doing it by hand.</v>

00:00:15.000 --> 00:00:27.000
<v Ravi Tamsin>Also, the old inventory fix pull request was closed without merging, and we had targeted December 24 for that one.</v>
"""


def test_a_mention_of_a_date_must_not_set_the_due_date(tmp_path):
    # --- a project with one epic, two people, and a meetings folder (all invented) ---
    calls = tmp_path / "calls"
    calls.mkdir()
    (calls / "2026-06-01 Daily sync.vtt").write_text(TRANSCRIPT)
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
        [plan]
        epics = ["Onboarding"]
        [[people]]
        name = "Nora Quill"
        side = "internal"
        [[people]]
        name = "Ravi Tamsin"
        side = "internal"
        [sources.daily]
        adapter = "meetings_folder"
        path = "%s"
    """ % calls))

    run_cli(tmp_path, "setup")                       # creates epic E01
    run_cli(tmp_path, "sync")                         # the transcript -> one evidence item
    evid = run_cli(tmp_path, "pending")["items"][0]["id"]

    # What the extraction agent would emit for this meeting: create the real story, then record
    # the closed PR as a mention that happens to carry a spoken date.
    proposals = [
        {"op": "create", "ref": "s1", "kind": "story", "parent": "E01",
         "title": "Automate the weekly stock report",
         "ac": ["Report generates with no manual steps", "Numbers reconcile against the warehouse system"],
         "internal_owner": "Nora Quill",
         "quote": "automate the weekly stock report so we stop doing it by hand"},
        {"op": "status", "node": "s1", "effect": "mention", "due_text": "December 24",
         "quote": "we had targeted December 24 for that one"},
    ]
    (tmp_path / "proposals.json").write_text(json.dumps(proposals))
    applied = run_cli(tmp_path, "apply", "--evidence", evid, "--proposals", str(tmp_path / "proposals.json"))

    # Guard: both proposals were accepted (nothing silently rejected) — so the test exercises the
    # real path, and a failure below is the finding, not a quote mismatch.
    assert applied["rejected"] == [], applied["rejected"]
    assert applied["by_op"].get("create_story") == 1 and applied["by_op"].get("status") == 1

    # The story, as the tree now stands:
    ctx = run_cli(tmp_path, "context")
    story = next(s for e in ctx["epics"] for s in e.get("stories", []) if s["id"] == "S001")

    # CORRECT: a mention changes nothing, so the story has no due date.
    # AS-SUBMITTED: the mention set due = 2026-12-24 with an empty verdict list (finding B3).
    assert story.get("due") is None, f"a mention moved the due date to {story.get('due')!r}"
