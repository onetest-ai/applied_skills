"""EXTRACT.md is the rule the nightly extractor follows. It must aim at the same definition of work as the readers, the
judges and the scorer (the user's tracking rule), and it must never cap or drop work said in a meeting."""
import re
import textwrap
from pathlib import Path

import pytest

from taska_lib import config as C, ledger as L, store as S

ROOT = Path(__file__).resolve().parent.parent
EXTRACT = (ROOT / "skills" / "run" / "EXTRACT.md").read_text(encoding="utf-8")
FLAT = " ".join(EXTRACT.split())


def _blocks(text):
    return {m.group(1): m.group(2) for m in
            re.finditer(r"<!-- READER-BLOCK:([\w-]+):START -->\n(.*?)\n<!-- READER-BLOCK:\1:END -->", text, re.S)}


@pytest.mark.parametrize("name", ["commitment", "not-work-bullets", "client-owed"])
def test_extract_carries_the_tracking_rule_byte_identical(name):
    ext = _blocks(EXTRACT)
    for doc in ("READER.md", "JUDGE.md"):
        other = _blocks((ROOT / "evals" / doc).read_text(encoding="utf-8"))
        assert ext.get(name) == other[name], f"EXTRACT.md block {name!r} differs from {doc}"


def test_no_count_cap():
    assert "rarely creates more than" not in EXTRACT


def test_work_is_never_silently_dropped():
    assert "never silently dropped" in FLAT


def test_owner_rule_for_room_recordings():
    assert "room recording" in FLAT and "never from the speaker tag" in FLAT


def test_conditional_decision_rule():
    assert "a conditional" in FLAT.lower() and "trigger date" in FLAT


def test_context_lists_room_recordings(tmp_path):
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
        [[people]]
        name = "Nora Quill"
    """))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    room = {"id": "2026-03-02_meetings_aa000001", "kind": "meeting", "at": "2026-03-02T10:00:00", "title": "Readout",
            "source": "meetings", "turns": [{"speaker": "Nora Quill", "ts": "00:00:01", "text": "word " * 2000}]}
    S.write_evidence(cfg.store, room)
    ctx = L.context(cfg.store, cfg)
    assert ctx["room_recordings"] == ["2026-03-02_meetings_aa000001"]


def test_extract_says_what_to_do_with_similar():
    assert "`similar`" in EXTRACT and "suggest_merge" in FLAT and "never keep both silently" in FLAT


def test_new_nodes_pass_the_tracking_rule_before_the_admission_bar():
    # Oct 30: 17 of 20 invented nodes were deck tweaks and booked calls; they have an owner or are a "session", so the
    # admission bar let them in although the rule says they are not work. The rule decides first, the bar second.
    p2 = EXTRACT[EXTRACT.index("## Pass 2"):EXTRACT.index("## Schema")]
    first = p2[:p2.index("**Admission bar:**")]
    assert "not work by the tracking rule" in first and "never created" in first
    assert "a `session` task is a deliverable session" in p2.lower()


def test_a_long_transcript_is_worked_through_in_sections():
    # Oct 31: a fully readable 20,000-word readout still yielded 0.9 events per 1,000 words against 4 for a short
    # sync, and its middle third one event: one meeting was one unit of attention whatever its length
    step2 = EXTRACT[EXTRACT.index("2. Read the item's"):EXTRACT.index("3. Write")]
    assert "10,000 words" in step2 and "sections of about 3,000 words" in step2 and "never summarise" in step2.lower()
    skill = (ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8")
    assert "over 10,000 words" in skill and "on its own" in skill


def _decision_pass():
    return EXTRACT[EXTRACT.index("## Second read"):EXTRACT.index("## What each kind of evidence")]


def test_extract_tells_the_reader_about_the_decisions_check():
    for s in ('"status": "decisions"', "`--no-decisions`", "`create` a `decision`", "a `risk`"):
        assert s in _decision_pass(), s


def test_extract_describes_the_decision_pass():
    # Nov 2 paid run: in the extraction context the decision gate caused 9 of 27 refusals and coincided with lost
    # commitments; decisions are now read in their own pass, one meeting per subagent
    read = " ".join(_decision_pass().split())
    assert '"need": "decisions"' in read and "never re-extracts what is listed" in read and "--no-decisions" in read
    assert "## Decision pass" not in EXTRACT and "do not extract tasks" not in EXTRACT
    assert "done --decisions" not in EXTRACT and "need: decisions" not in EXTRACT
    assert "under 6,000 characters is one part" in read and "fewer than 8 words" in read and "repeat the whole command" in read
    assert all("Second read" not in b and "--no-decisions" not in b for b in _blocks(EXTRACT).values())
    top = EXTRACT[EXTRACT.index("For each evidence id"):EXTRACT.index("1. `$TASKA context`")]
    assert '"need": "decisions"' in top and "**Second read**" in top
    step5 = " ".join(EXTRACT[EXTRACT.index("5. `$TASKA done"):EXTRACT.index("## Second read")].split())
    assert "--no-decisions" not in step5 and '"status": "decisions"' not in step5 and "before** your last" not in step5
    assert "`done` does not ask for them" in step5 and "[--leftovers <token>]`" in step5 and "repeat the whole command" in step5
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "--decisions TOKEN" not in readme and "with its own token" not in readme


def test_the_decision_pass_never_re_records_a_decision():
    read = " ".join(_decision_pass().split())
    for s in ("Decisions already recorded from this meeting stand",
              "`$TASKA leftovers --evidence <id>` counts them per third under `recorded`", "Never create one again",
              "a third with a recorded decision is still read for other decisions, never recorded again in other words"):
        assert s in read, s


def test_extraction_points_to_the_decision_pass_for_what_a_decision_is():
    step5 = " ".join(EXTRACT[EXTRACT.index("5. `$TASKA done"):EXTRACT.index("## Second read")].split())
    assert "as before" not in step5 and "what counts as a decision is under **Second read**" in step5


def test_skill_counts_decision_rows_toward_max_items_and_scopes_dependencies():
    skill = (ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8")
    flat = " ".join(skill.split())
    assert "decision rows count toward `[run].max_items` together with the extraction rows, oldest first" in flat
    deps = " ".join(skill[skill.index("**Dependencies.**"):skill.index("**Verification.**")].split())
    assert "the ids extracted in step 4" in deps and "not the second-read rows" in deps


def test_skill_runs_the_decision_pass_after_extraction_in_parallel():
    skill = (ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8")
    flat = " ".join(skill.split())
    ext, dec, deps = (skill.index(s) for s in ("**Extraction.**", "**Second read.**", "**Dependencies.**"))
    assert ext < dec < deps
    extraction = " ".join(skill[ext:dec].split())
    assert "one after another, never in parallel" in extraction and '"need": "decisions"' not in extraction
    step = " ".join(skill[dec:deps].split())
    for s in ('"need": "decisions"', "its own subagent", "in parallel", "up to 5 at a time",
              "second read: decisions and work the first read left out; do not re-extract what is listed",
              '"Second read" section', "EXTRACT.md", "no extraction rows"):
        assert s in step, s
    assert "Decision read only: do not re-extract" not in flat and "decision pass only" not in flat
    assert "adds only decisions and risks" not in flat


def test_the_decision_read_says_what_a_decision_is():
    # Nov 2 paid run: ~7 of 30 reference decisions recorded; the missed ones were working agreements, and a third with
    # only a recorded risk passed the gate
    read = " ".join(_decision_pass().split())
    for s in ("a decision is any choice the group settled that changes what someone does next",
              "what is in or out of scope this week", "a standing cadence or forum", "what is shown to the client and how",
              "who owns a workstream", "what is paused or dropped", 'often with no "decide"',
              "Read the whole third for decisions before deciding it has none",
              "a recorded risk does not answer the decision question", "is staffing and never recorded"):
        assert s in read, s
    assert "decision or risk of yours" not in read and "no decision and no risk" not in read
    assert "as before" not in read


def _decision_read():
    return " ".join(_decision_pass().split())


def test_a_decision_is_settled_and_reaches_beyond_the_meeting():
    # review of Task 6c: the definition must not record suggestions, open questions or how the call itself runs, and
    # must agree with the not-work bullets on sessions and decks
    read = _decision_read()
    assert ("Record one only when the group, or someone with the authority to, settled it (not a suggestion nobody "
            "took up, not an open question) and it changes work beyond this meeting.") in read
    for s in ("how a meeting or workshop session itself runs", "which topics go to which session",
              "booking or moving a session", "how a deck or slide is made or sent"):
        assert s in read, s
    assert "no deck for" not in read


def test_the_workshop_order_is_no_decision_example_and_a_scope_change_still_is():
    # the manager's key resolution of 2 Nov dropped the order of workshop topics as not work: the read must not teach it
    read = _decision_read()
    assert "A choice about the order or method the work follows is a decision even when" not in read
    assert "the Harbor workshops start with" not in read
    assert "only how a single session is run" not in read
    assert "A choice that changes the engagement's scope is a decision." in read


def test_the_second_read_names_what_never_counts():
    # 2 Nov paid runs: the second read recorded agendas, posters, session topics and how-might-we edits as work
    read = _decision_read()
    assert ("Never counted in this read, as work, a decision or a risk: agendas, invites, posters and workshop "
            "materials, which topics go to which session or in what order, who joins a planning group, how-might-we "
            "wording edits, and deck handling.") in read


def test_the_second_read_turns_a_restated_item_into_a_note():
    read = _decision_read()
    for s in ("refused as `similar_exists`", "already recorded from this meeting",
              "never reword it to get past the check",
              "a `note` or a `status` `mention` on that node"):
        assert s in read, s


def test_owning_a_workstream_is_a_decision_staffing_is_not():
    read = _decision_read()
    assert '"From now on Opal\'s team owns the pricing workstream" is a decision' in read
    assert '"Nora runs the yard stream at 50% until May" is staffing and never recorded' in read
    assert "how much of a person's time, at what percentage or rate, until when, extended or released" in read


def test_the_decision_examples_carry_no_decision_cue():
    # the paragraph says decisions come "often with no decide": its examples must prove it, or the agent learns cue words
    from taska_lib import leftovers as LO
    from taska_lib.text import norm
    examples = [q for q in re.findall(r'"([^"<$`]+)"', _decision_read()) if len(q.split()) >= 5]
    assert len(examples) >= 7, examples
    for q in examples:
        assert not LO.DECISION_CUES.search(norm(q)), q


def test_not_work_names_people_matters():
    block = _blocks(EXTRACT)["not-work-bullets"]
    assert "People matters: who is staffed, allocated, extended, released or rolled off" in " ".join(block.split())


# ---------- the second read (Nov 2: 12 of 14 unproposed misses sat in meetings the leftovers list never reached, and a
# fresh reader recorded twice the decisions of the first; three paraphrased quotes were refused with no way back) ----------

def test_the_second_read_records_the_work_the_first_read_left_out():
    read = _decision_read()
    for s in ("for its decisions and for the work the first read left out",
              "Then, in the same third, the work the first read left out",
              "check the `items` of every third, not only this one, and the tree (`context`, with its `proposed_work`)",
              "never record what is already there in other words",
              "`create` it when it is `committed` (our side is expected to act)",
              "`propose` it when it is `proposed` (a specific deliverable asked for or suggested that nobody on our "
              "side has taken)", "exactly as the commitment block defines them",
              "The first read may have left a sentence out on purpose: whatever the not-work bullets cover stays out.",
              "Never re-record a listed item in other words",
              "People matters (the not-work bullets) are never recorded, as work, a decision or a risk"):
        assert s in read, s
    assert "`propose` when nobody on our side took it" not in read   # review I2: looser than the commitment block
    assert read.index("Decisions already recorded from this meeting stand") < read.index("Then, in the same third")


def test_the_second_read_sees_what_each_third_already_holds_and_hints_for_every_meeting():
    read = _decision_read()
    for s in ("`items` (every node already recorded from this meeting whose quote lies in that third: id, kind, title",
              "a card proposed from it that waits for the manager or was rejected carries `card` and is never proposed again",
              "under `recorded`", "Both cue lists are hints for every meeting, whatever its length, not a list to clear"):
        assert s in read, s


def test_a_refused_quote_is_re_applied_from_its_closest_span():
    step4 = " ".join(EXTRACT[EXTRACT.index("4. `$TASKA apply"):EXTRACT.index("5. `$TASKA done")].split())
    assert "`closest`" in step4 and "re-apply with `closest` if it says the same thing; never invent a span" in step4
    assert "re-apply with `closest` if it says the same thing; never invent a span" in _decision_read()


def test_hypotheses_and_the_clients_own_operations_are_not_work():
    # the manager's 2 Nov rulings, in the shared block (byte-identical in READER.md and JUDGE.md, pinned above)
    block = " ".join(_blocks(EXTRACT)["not-work-bullets"].split())
    assert "Workshop brainstorming: future-state ideas and hypotheses, how-might-we statements" in block
    assert ("- The client's own operational changes (what the client will do in its own business), unless the client "
            "owes us a deliverable from them.") in block
