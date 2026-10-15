"""A `mention` changes nothing. A passing remark that cites a date ("that was targeted for Christmas Eve")
must not set the story's due date: a mention is not verified, so letting it write `due_history` would let
an unverified remark drive overdue/slipping and the asks.

End to end through the CLI. All names are invented (the fictional world in conftest.py)."""
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

    # Guard: both proposals were accepted (nothing silently rejected), so the test exercises the real path.
    assert applied["rejected"] == [], applied["rejected"]
    assert applied["by_op"].get("create_story") == 1 and applied["by_op"].get("status") == 1

    # The story, as the tree now stands:
    ctx = run_cli(tmp_path, "context")
    story = next(s for e in ctx["epics"] for s in e.get("stories", []) if s["id"] == "S001")

    # A mention changes nothing, so the story has no due date.
    assert story.get("due") is None, f"a mention moved the due date to {story.get('due')!r}"
