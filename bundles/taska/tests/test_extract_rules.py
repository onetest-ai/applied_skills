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


@pytest.mark.parametrize("name", ["commitment", "not-work-bullets", "client-owed", "owner"])
def test_extract_carries_the_tracking_rule_byte_identical(name):
    ext = _blocks(EXTRACT)
    for doc in ("READER.md", "JUDGE.md"):
        other = _blocks((ROOT / "evals" / doc).read_text(encoding="utf-8"))
        assert ext.get(name) == other[name], f"EXTRACT.md block {name!r} differs from {doc}"


def test_the_owner_block_carries_the_four_approved_cases():
    block = " ".join(_blocks(EXTRACT)["owner"].split())
    assert '"We\'ll draft it" with a clear action is a team commitment: `committed`, `owner` null' in block
    assert 'Never make the speaker the owner only because they said "we"' in block
    assert '"Anna will draft it" makes Anna the owner when the context confirms the assignment' in block
    assert '"I\'ll draft it" makes the speaker the owner when the speaker tag is reliable' in block
    assert "An unreliable speaker tag" in block and "identifies nobody" in block


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
    assert "sections of about 3,000 words" in step2 and "never summarise" in step2.lower()
    assert "over 30,000 characters" in " ".join(step2.split()) and "3,000 words or more" not in step2
    skill = (ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8")
    assert "more than one entry" in skill and "on its own" in skill
    assert "over 30,000 characters" in _skill_step4() and "3,000+ words" not in skill


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


def test_skill_runs_the_decision_pass_after_extraction_one_after_another():
    skill = (ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8")
    flat = " ".join(skill.split())
    ext, dec, deps = (skill.index(s) for s in ("**Extraction.**", "**Second read.**", "**Dependencies.**"))
    assert ext < dec < deps
    extraction = " ".join(skill[ext:dec].split())
    assert "one after another, never in parallel" in extraction and '"need": "decisions"' not in extraction
    step = " ".join(skill[dec:deps].split())
    for s in ('"need": "decisions"', "its own subagent", "one after another, never in parallel",
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


def _flat(text, start, end):
    return " ".join(text[text.index(start):text.index(end)].split())


def _skill_step4():
    skill = (ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8")
    return _flat(skill, "4. **Extraction.**", "5. **Second read.**")


def test_skill_step_four_dispatches_section_readers_one_after_another():
    step4 = _skill_step4()
    assert "$TASKA sections --evidence <id>" in step4
    assert "one fresh subagent per section" in step4
    assert "one after another" in step4 and "never in parallel" in step4
    assert "Section read" in step4 and "ONE fresh re-read agent" in step4


def test_extraction_keeps_date_order_across_batches_and_sectioned_meetings():
    step4 = _skill_step4()
    assert "strictly in date order" in step4
    assert "a batch never reaches past a meeting with more than one section" in step4


def test_second_reads_run_one_after_another_and_a_sectioned_meeting_is_one_row():
    skill = (ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8")
    step5 = _flat(skill, "5. **Second read.**", "6. **Dependencies.**")
    assert "rows run one after another, never in parallel" in step5
    assert "(a sectioned meeting counts as one row)" in step5 and "one row per section" not in step5


def test_the_orchestrator_never_rereads_or_acknowledges():
    step4 = _skill_step4()
    assert "you do not run `reread` or `done --ack` yourself" in step4
    assert "`$TASKA reread" not in step4 and "`$TASKA done --evidence <id>` with the `--ack`" not in step4
    assert "Re-read of refused parts" in step4


def test_a_stale_md_is_re_rendered_before_sections_are_dispatched():
    step4 = _skill_step4()
    assert "null" in step4 and "`$TASKA render-evidence`" in step4 and "never send a reader to read the whole file" in step4


def test_extract_has_a_section_read_block_with_apply_section_and_reread():
    block = _flat(EXTRACT, "## Section read", "## Re-read of refused parts")
    assert "--section 2" in block and "apply --evidence <id> --proposals" in block
    assert "outside_section" in block and "node id from `context`" in block
    assert "$TASKA reread --evidence <id> --part 2" in block
    assert "every meeting of 6,000+ characters" in FLAT


def test_the_section_readers_done_never_acknowledges():
    block = _flat(EXTRACT, "## Section read", "## Re-read of refused parts")
    done = block[block.index("Then run `$TASKA done"):]
    assert "$TASKA done --evidence <id> [--leftovers <token>]" in done
    assert "`--ack`" in done and "never `--ack`, `--quote` or `--reread`" in done
    assert 'done --evidence <id> [--ack' not in block
    assert "WITHOUT `--section`" in block


def test_the_reread_block_applies_without_section_and_runs_done_itself():
    block = _flat(EXTRACT, "## Re-read of refused parts", "## Second read")
    assert "WITHOUT `--section`" in block and "apply --evidence <id> --proposals <that file>`" in block
    assert "--section <" not in block and "apply --evidence <id> --proposals <that file> --section" not in block
    assert "Run `$TASKA done --evidence <id>` yourself" in block
    assert "only agent that acknowledges" in block and "`--ack" in block and "`--reread" in block
    assert "<id>_p<part>.json" in block


def test_the_read_ban_has_an_exception_for_a_leftovers_sentences_part():
    assert "except the part of a leftovers sentence, via `reread`" in _skill_step4()
    block = _flat(EXTRACT, "## Section read", "## Re-read of refused parts")
    assert "except the part of a leftovers sentence, via `reread`" in block


def test_the_reread_block_decides_leftovers_before_the_final_reread_and_acks_only_what_it_read():
    block = _flat(EXTRACT, "## Re-read of refused parts", "## Second read")
    assert block.index("2. Run `$TASKA leftovers") < block.index("3. Run `$TASKA reread") < block.index("4. Run `$TASKA done")
    assert "every refused part still in the gap that you read" in block
    assert "A part you did not read is never acknowledged" in block


def test_skill_bounds_the_reread_to_one_round_per_meeting_per_run():
    step4 = _skill_step4()
    assert "only re-read round for that meeting in this run" in step4
    assert "unfinished in the run summary" in step4 and "do not dispatch again" in step4 and "comes back in `pending` next run" in step4
    assert "stop and report the parts" in _flat(EXTRACT, "## Re-read of refused parts", "## Second read")


def test_a_fresh_leftovers_token_is_taken_after_the_last_apply():
    reread = _flat(EXTRACT, "## Re-read of refused parts", "## Second read")
    assert reread.index("3. Run `$TASKA reread") < reread.index("`$TASKA leftovers --evidence <id>` again for a fresh leftovers token") < reread.index("4. Run")
    section = _flat(EXTRACT, "## Section read", "## Re-read of refused parts")
    assert section.index("run `$TASKA leftovers --evidence <id>` again for a fresh leftovers token") < section.index("Then run `$TASKA done")


def test_the_run_summary_names_meetings_left_unfinished_after_the_reread_round():
    skill = (ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8")
    step9 = _flat(skill, "9. Report to the user", "## Handing a project over")
    assert "meetings left unfinished after their re-read round" in step9


def test_every_section_reader_decides_its_own_sections_leftovers():
    block = _flat(EXTRACT, "## Section read", "## Re-read of refused parts")
    own = block[block.index("$TASKA leftovers --evidence <id> --section 2"):]
    assert "sentence by sentence" in own and "--section 2" in own and "not work" in own
    assert block.index("$TASKA leftovers --evidence <id> --section 2") < block.index("Not the last section")
    assert "decide the whole meeting" not in block.lower()


def test_the_last_section_refreshes_the_whole_meeting_token_and_rereads_before_deciding_elsewhere():
    block = _flat(EXTRACT, "## Section read", "## Re-read of refused parts")
    last = block[block.index("The last section"):]
    assert "$TASKA leftovers --evidence <id> --section 2" in block
    assert "outside your section" in last and "$TASKA reread --evidence <id> --part 2" in last
    assert last.index("$TASKA reread") < last.index("for a fresh leftovers token") < last.index("Then run `$TASKA done")


def test_a_meeting_whose_sections_have_no_usable_lines_gets_one_reader():
    step4 = _skill_step4()
    assert "still null after `$TASKA render-evidence`" in step4 and "`last_line` below its `first_line`" in step4
    assert "one reader" in step4 and "run summary" in step4


def test_an_agent_without_subagents_works_the_sections_itself():
    step2 = " ".join(EXTRACT[EXTRACT.index("2. Read the item's"):EXTRACT.index("3. Write")].split())
    assert "cannot dispatch subagents" in step2 and "one after another" in step2
    assert "`$TASKA context --compact` again" in step2


def test_skill_step_four_gives_a_meeting_without_section_lines_its_own_dispatch_line():
    step4 = _skill_step4()
    assert "has no usable section lines: read its .md whole, 120 lines at a time, by extraction steps 2-5; do not use the Section read block" in step4
    assert "told the meeting has no usable section lines, read the whole `.md` in 120-line steps and follow steps 2-5, not the **Section read** block" in FLAT


def test_the_second_read_never_uses_the_section_read_flow():
    start = EXTRACT.index("## Second read")
    rest = EXTRACT[start + 5:]
    nxt = rest.find("\n## ")
    second = " ".join((EXTRACT[start:] if nxt < 0 else EXTRACT[start:start + 5 + nxt]).split())
    assert "never use the **Section read** block, `apply --section` or `leftovers --section`" in second
    assert "as step 2" not in second


# ---------- round 1b (Nov 4): B13 4/7 — one second reader over a 30-40k-char meeting recorded ~1 decision per meeting,
# used a key risk sentence as its --no-decisions proof; B10 owners named a collaborator; B12 under-way work got no status ----------

def _skill_step5():
    skill = (ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8")
    return _flat(skill, "5. **Second read.**", "6. **Dependencies.**").replace(" > ", " ")   # prompt lines read as one


def test_a_sectioned_meeting_gets_one_second_reader_per_section_one_after_another():
    step5 = _skill_step5()
    for s in ("$TASKA sections --evidence <id>", "more than one entry", "one fresh second reader per section",
              "one after another", "section 1 first and each only after the previous one has finished",
              "only its section's lines", "in 120-line steps", "from `sections`",
              "The second reader of the last section", "`$TASKA leftovers --evidence <id>`", "`$TASKA done",
              "only for thirds whose `recorded` is 0 (any live decision from this meeting counts)",
              "A meeting whose `sections` has one entry keeps one second reader"):
        assert s in step5, s
    assert "Section read" not in step5.replace("never the **Section read** block", "")


def test_the_second_read_has_a_section_mode_whose_last_reader_runs_done():
    read = _decision_read()
    for s in ("Second read of a section", "section <n> of <count>", "Read only your lines",
              "Not the last section: do not run `done`", "The last section",
              "a fresh `$TASKA leftovers --evidence <id>`", "`recorded` is 0",
              "$TASKA reread --evidence <id> --part 2"):
        assert s in read, s


def test_the_second_read_records_every_decision_and_every_risk():
    read = _decision_read()
    assert "record EVERY decision and EVERY risk said in your lines, not one per third" in read
    assert "That gate is a floor, not a quota" in read


def test_a_risk_includes_a_conditional_threat_a_consequence_and_a_dated_client_dependency():
    read = _decision_read()
    for s in ("a conditional threat", '"if X does not happen by <date>, …"', "a stated consequence",
              '"they will still have the same issues"', "a client dependency with a deadline"):
        assert s in read, s


def test_a_no_decisions_proof_is_neither_a_decision_nor_a_risk():
    read = _decision_read()
    assert "a sentence that is neither a decision nor a risk" in read
    assert "Never use a risk or a decision sentence as that proof" in read


def _owners():
    p2 = " ".join(EXTRACT[EXTRACT.index("## Pass 2"):EXTRACT.index("## Schema")].split())
    return p2[p2.index("Owners:"):]


def test_the_owner_is_who_does_the_work_never_a_named_collaborator():
    owners = _owners()
    for s in ("the person who will do the work", '"I\'ll", "I will", "I\'m going to" or "let me" is the speaker',
              '"can you", "you\'ll" or "could you" is the person addressed',
              'by "with" ("I\'ll work with Ravi on the berth list") is a collaborator, never the owner'):
        assert s in owners, s
    assert '"we" = null' not in owners and "When unsure" not in owners


def test_the_owner_is_the_speaker_who_states_work_and_null_only_without_a_doer():
    owners = _owners()
    for s in ("The owner is the speaker when the speaker states work they do or will do, including a \"we'll\" or "
              "\"we're\" said by the person who then describes doing it",
              "the owner is null only when no doer can be read (a room recording, or \"we\" with nobody leading)"):
        assert s in owners, s


def test_future_work_stays_open_and_only_work_already_happening_gets_progress():
    p2 = " ".join(EXTRACT[EXTRACT.index("## Pass 2"):EXTRACT.index("## Schema")].split())
    for s in ("`progress` only for work said to be already happening",
              'present progressive ("we\'re gathering", "working on")',
              'Future work ("we\'ll have", "going to", "will") stays open, with no status op'):
        assert s in p2, s


def test_an_existing_node_said_to_be_ongoing_gets_a_progress_op_on_that_node():
    p2 = " ".join(EXTRACT[EXTRACT.index("## Pass 2"):EXTRACT.index("## Schema")].split())
    assert "An existing node that the meeting says is ongoing gets a `status` `progress` op on that node" in p2


def test_work_under_way_gets_a_progress_status_on_the_new_node():
    p2 = " ".join(EXTRACT[EXTRACT.index("## Pass 2"):EXTRACT.index("## Schema")].split())
    for s in ("work said to be already happening", '"started"', '"ongoing"',
              '"in the middle of"', "a `status` op with effect `progress` on the new node, using its `ref`",
              "quoting the same sentence", 'A promise ("I\'ll send it") is not progress'):
        assert s in p2, s


# ---------- round 1b review fixes (Opus review: I1-I3, collaborator scope, per-section proposals file) ----------

def test_the_under_way_status_follows_an_admitted_create_only():
    p2 = " ".join(EXTRACT[EXTRACT.index("## Pass 2"):EXTRACT.index("## Schema")].split())
    for s in ("The status op follows the create in the same file and takes effect only if that create is admitted",
              "Work that becomes a `propose`, or a create refused `similar_exists`, gets no status on the new ref",
              "a `similar_exists` refusal (second read only) is handled as **Second read** step 4 says",
              "a `progress` needs the verifier's `supports` before it changes the node"):
        assert s in p2, s


def test_the_second_read_notes_an_existing_decision_or_risk_instead_of_recording_it_again():
    read = _decision_read()
    for s in ("Before recording a decision or a risk, check `context` for a live decision or risk that already says the "
              "same thing", "apply a `note` (or a `status` `mention`) on that node instead of a new one",
              "Record that client dependency as a risk only when no dependency task and no risk already holds it",
              "otherwise record only its stated consequence, or nothing"):
        assert s in read, s
    assert read.index("Before recording a decision or a risk") < read.index("`create` a `decision` quoting the sentence")


def test_the_last_second_reader_may_read_a_third_outside_its_section_and_a_risk_is_no_decision():
    step5 = _skill_step5()
    assert "except a third the **Second read of a section** paragraph sends you to" in step5
    read = _decision_read()
    for s in ("a third that needs a quote and is not wholly inside your lines",
              "A risk recorded in a third does not count as its decision",
              "that third still needs a `--no-decisions` quote, one that is neither a decision nor a risk, unless a "
              "decision is recorded there", "a risk is recorded and applied as well as the quote"):
        assert s in read, s


def test_each_section_second_reader_has_its_own_proposals_file():
    assert "<store>/proposals/<id>_decisions_s<n>.json" in _skill_step5()
    assert "<store>/proposals/<evidence_id>_decisions_s<n>.json" in _decision_read()


def test_the_collaborator_rule_is_limited_to_one_clause():
    owners = _owners()
    for s in ("A person joined to the doer in the same clause", '"Nora and Ravi will draft it") are both doers',
              "the owner is null unless the sentence says who leads",
              "That holds only within one clause",
              'in "Nora will check it and Ravi will send the list" Ravi has his own commitment'):
        assert s in owners, s


# ---------- round 1b fix round 2 (re-review) ----------

def test_a_similar_exists_refusal_is_handled_once_in_second_read_step_four():
    p2 = " ".join(EXTRACT[EXTRACT.index("## Pass 2"):EXTRACT.index("## Schema")].split())
    assert "put the `progress` status on the existing node named in the refusal" not in p2
    step4 = _decision_read()[_decision_read().index("4. Write the decisions"):]
    for s in ("When `node` is a live node and the sentence says the work is under way, apply a `status` `progress` on it",
              "apply a `note` or a `status` `mention` on that node instead",
              "When `node` is a card (a pending proposal), apply nothing"):
        assert s in step4, s


def test_the_decision_gate_counts_any_live_decision_from_the_meeting():
    read = _decision_read()
    assert "a decision of yours" not in read and "per third you recorded no decision in" not in read
    assert "any live decision from this meeting counts, whoever recorded it" in read
    assert "one `--no-decisions` per third whose `recorded` is 0" in read


def test_the_last_section_second_reader_gets_a_whole_prompt():
    step5 = _skill_step5()
    assert "replace" not in step5
    last = step5[step5.index("For the last section, this prompt instead"):step5.index("The second reader of the last section")]
    for s in ("This is a second read", "for section <n> of <count> of evidence id <id>", "`$TASKA` is",
              "except a third the **Second read of a section** paragraph sends you to",
              "<store>/proposals/<id>_decisions_s<n>.json", "You are the last section",
              "`$TASKA leftovers --evidence <id>` and `$TASKA done`"):
        assert s in last, s


def test_a_reread_find_goes_into_the_section_file_and_is_re_applied():
    read = _decision_read()
    sec = read[read.index("**Second read of a section.**"):]
    assert ("add it to your `<store>/proposals/<evidence_id>_decisions_s<n>.json` and apply that file again "
            "(already applied entries are recognised by their mid and add nothing)") in sec


def test_the_commitment_block_counts_an_offer_or_plan_said_by_the_person_who_acts():
    sentence = ("An offer or plan said by the person who will act is a commitment too: \"I can go check which "
                "fields the export uses\", \"gonna start on the berth schedule next week\", \"we can now reach "
                "out to get the site visit booked\", \"we're in the process of writing that up\".")
    blocks = [_blocks(EXTRACT)["commitment"]] + [
        _blocks((ROOT / "evals" / doc).read_text(encoding="utf-8"))["commitment"] for doc in ("READER.md", "JUDGE.md")]
    for block in blocks:
        assert sentence in " ".join(block.split())
    assert blocks[0] == blocks[1] == blocks[2]


def test_extract_pass_two_makes_a_planned_step_a_task_beside_its_decision():
    pass2 = EXTRACT.split("## Pass 2", 1)[1]
    flat = " ".join(pass2.split())
    assert ("A planned step with someone to do it and an action (\"we'll start to assess early feasibility of the "
            "concepts\") is a task quoting that sentence, even when the same discussion also yields a decision: "
            "record both, each quoting its own sentence.") in flat


def test_a_commitment_by_our_side_is_a_task_and_we_never_makes_the_speaker_the_owner():
    assert ("A commitment by our side is a task, never a card" in FLAT
            and "when no single doer can be read it gets `committed: true` and no owner" in FLAT
            and 'Never make the speaker the owner only because they said "we".' in FLAT
            and "is not committed yet: `propose` it, with the condition in `why`" in FLAT
            and "A card is for work nobody on our side has taken" in FLAT
            and "speak for the team" not in FLAT)


def test_a_same_quote_card_conflict_is_resolved_by_reading_never_by_dropping_distinct_work():
    assert ("`same_quote_card` when the titles differ" in FLAT and "send it again with `distinct_from` set to the `card` the answer names" in FLAT)
