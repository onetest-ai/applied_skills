"""evals/relabel.py: one page asks a person about every missed key item and every node the key does not have, and the
answers become a new key version; nothing changes without an answer. Invented data only."""
import json
import sys

from conftest import TASKA_ROOT

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import relabel as R  # noqa: E402
from taska_lib.evalscore import load_gold  # noqa: E402

MEET = "2026-03-02_meetings_ab12cd34"
KEY = [
    {"cid": "ab12cd34-001", "evidence": MEET, "quote": "Nora will polish the onboarding deck colours before Friday",
     "title": "Polish onboarding deck", "kind": "task", "commitment": "committed", "owner": "Nora Quill"},
    {"cid": "ab12cd34-002", "evidence": MEET, "quote": "Ravi will write the forklift safety checklist for the yard team",
     "title": "Write forklift safety checklist", "kind": "task", "commitment": "committed", "owner": "Ravi Tamsin"},
    {"cid": "ab12cd34-003", "evidence": MEET, "quote": "Opal will send the dock schedule export to us by Monday",
     "title": "Send dock schedule export", "kind": "task", "commitment": "committed", "owner": "Opal Brennick"},
]
ROWS = [
    {"bucket": "B7", "key_id": "ab12cd34-001", "verdict": "miss", "stage": "not_proposed", "meeting": MEET},
    {"bucket": "B7", "key_id": "ab12cd34-002", "verdict": "miss", "stage": "not_proposed", "meeting": MEET},
    {"bucket": "B7", "key_id": "ab12cd34-003", "verdict": "hit", "stage": "matched", "meeting": MEET},
    {"bucket": "B15", "key_id": "", "verdict": "invented", "meeting": MEET, "run_node": "R004", "run_kind": "risk",
     "run_title": "Crane permit may slip", "run_owner": "", "quote": "the crane permit could slip past the audit window"},
    {"bucket": "B15", "key_id": "", "verdict": "hit", "meeting": MEET, "run_node": "T001", "run_kind": "task",
     "run_title": "Send dock schedule export", "run_owner": "Opal Brennick", "quote": KEY[2]["quote"]},
]
NODES = {"T007": {"title": "Forklift safety checklist for the yard", "kind": "task", "created_in": MEET, "by": "agent",
                  "quote": "the yard checklist for forklifts goes to Opal next week"}}


def test_the_page_asks_about_every_miss_and_every_extra_node_only():
    items = R.page_items(ROWS, KEY, NODES)
    assert [i["cid"] for i in items] == ["ab12cd34-001", "ab12cd34-002", "node:R004"]
    assert items[1]["existing"] == {"id": "T007", "title": "Forklift safety checklist for the yard",
                                    "quote": "the yard checklist for forklifts goes to Opal next week"}
    assert "existing" not in items[0]   # no node with a similar title: "taska has nothing that looks like this"
    assert "missed key item" in items[0]["meeting"] and "node the key does not have" in items[2]["meeting"]


def test_answers_become_the_next_key_and_nothing_moves_without_one():
    items = R.page_items(ROWS, KEY, NODES)
    answers = {"ab12cd34-001": {"kind": "not_work", "new": None, "note": "deck tweak"},
               "ab12cd34-002": {"kind": "committed", "new": "tracked", "note": ""},
               "node:R004": {"kind": "committed", "new": "new", "note": "a real risk"}}
    out = R.apply_answers(KEY, items, answers, record="20260302-120000-2")
    assert [g["cid"] for g in out] == ["ab12cd34-002", "ab12cd34-003", "ab12cd34-x001"]
    assert out[0]["quotes"] == [KEY[1]["quote"], NODES["T007"]["quote"]]
    assert out[1] == KEY[2]
    new = out[2]
    assert (new["evidence"], new["quote"], new["kind"], new["commitment"], new["source"]) == (
        MEET, "the crane permit could slip past the audit window", "risk", "committed", "user")
    assert R.apply_answers(KEY, items, {}, record="r") == KEY


def test_apply_writes_a_new_key_version_with_a_changelog_line(tmp_path):
    items = R.page_items(ROWS, KEY, NODES)
    path = R.write_key(tmp_path, R.apply_answers(KEY, items, {"ab12cd34-001": {"kind": "not_work"}}, record="r"),
                       "v1.2", "2026-03-03", "labels from record r")
    head, gold = load_gold(path)
    assert head["version"] == "v1.2" and [g["cid"] for g in gold] == ["ab12cd34-002", "ab12cd34-003"]
    assert "labels from record r" in (tmp_path / "CHANGELOG.md").read_text()


def test_answers_are_read_from_the_downloaded_file_and_its_stamp_checked(tmp_path):
    f = tmp_path / "key-labels-s1.json"
    f.write_text(json.dumps({"stamp": "s1", "answers": {"ab12cd34-001": {"kind": "not_work"}}}))
    assert R.read_answers(f, "s1") == {"ab12cd34-001": {"kind": "not_work"}}
    try:
        R.read_answers(f, "s2")
    except ValueError as e:
        assert "s1" in str(e) and "s2" in str(e)
    else:
        raise AssertionError("an answer file for another page was accepted")


POOL = {MEET: [{"cid": "ab12cd34-001", "evidence": MEET, "quote": KEY[0]["quote"]},
               {"cid": "ab12cd34-002", "evidence": MEET, "quote": KEY[1]["quote"]},
               {"cid": "ab12cd34-007", "evidence": MEET, "quote": "the crane permit could slip past the audit window"},
               {"cid": "ab12cd34-009", "evidence": MEET, "quote": "Ravi said again he will write the forklift checklist this week"}]}


def _files(tmp_path):
    page = tmp_path / "answers.json"
    page.write_text(json.dumps({"saved_at": "2026-10-25T08:21:07Z", "answers": {
        "ab12cd34-001": {"kind": "committed"}, "ab12cd34-009": {"kind": "committed"}}}))
    labels = tmp_path / "labels_user.json"
    labels.write_text(json.dumps({"date": "2026-10-24", "items": [
        {"evidence": MEET, "quote": "the crane permit could slip past the audit window", "user": {"kind": "not_work"}}]}))
    chat = tmp_path / "chat_rulings.json"
    chat.write_text(json.dumps({"ab12cd34-003": {"kind": "proposed", "why": "said in chat"}}))
    judged = tmp_path / "judged_01.json"
    judged.write_text(json.dumps([{"cid": "ab12cd34-007", "commitment": "committed"},
                                  {"cid": "ab12cd34-009", "commitment": "committed", "duplicate_of": "ab12cd34-002"}]))
    recheck = tmp_path / "recheck_out.json"
    recheck.write_text(json.dumps([{"cid": "ab12cd34-007", "verdict": "not_work", "rule": "Scheduling"}]))
    return [page, labels, chat], [judged, recheck]


def test_earlier_labels_are_read_by_shape(tmp_path):
    yours, hints = _files(tmp_path)
    y, h = R.read_labels(yours, hints, POOL)
    assert y["ab12cd34-001"] == {"kind": "committed", "by": "you, 2026-10-25 (answers)"}
    assert y["ab12cd34-007"] == {"kind": "not_work", "by": "you, 2026-10-24 (labels_user)"}
    assert y["ab12cd34-003"]["kind"] == "proposed" and "chat" in y["ab12cd34-003"]["by"]
    assert h["ab12cd34-007"] == {"judge": "committed", "recheck": "not_work (Scheduling)"}
    assert h["ab12cd34-009"]["duplicate_of"] == "ab12cd34-002"


def test_the_page_puts_new_questions_first_and_carries_earlier_answers(tmp_path):
    yours, hints = _files(tmp_path)
    y, h = R.read_labels(yours, hints, POOL)
    items = R.with_labels(R.page_items(ROWS, KEY, NODES), y, h, POOL, {})
    assert [i["cid"] for i in items] == ["ab12cd34-002", "ab12cd34-001", "node:R004"]   # never asked first, then yours
    assert "prior" not in items[0] and items[1]["prior"]["kind"] == "committed"
    assert items[2]["prior"]["kind"] == "not_work"   # the node's quote is candidate ab12cd34-007, which you labelled
    assert items[2]["hint"] == "judge: committed; recheck: not_work (Scheduling)"


def test_a_dropped_second_mention_you_called_work_becomes_a_quote_of_its_item(tmp_path):
    yours, hints = _files(tmp_path)
    y, h = R.read_labels(yours, hints, POOL)
    extra = R.dropped_mentions(KEY, y, h, POOL)
    assert extra == {"ab12cd34-002": ["Ravi said again he will write the forklift checklist this week"]}
    out = R.apply_answers(KEY, [], {}, record="r", extra_quotes=extra)
    assert out[1]["quotes"] == [KEY[1]["quote"], "Ravi said again he will write the forklift checklist this week"]


# ---------- eval-labels/1: the quality page's labels file ----------
REC = "20260302-120000-2"
STORE_NODES = {"R004": {"title": "Crane permit may slip", "kind": "risk", "internal_owner": None, "client_owner": "Opal Brennick",
                        "created_in": MEET, "by": "agent", "quote": "the crane permit could slip past the audit window"},
               "T009": {"title": "Second forklift checklist mention", "kind": "task", "internal_owner": "Ravi Tamsin",
                        "client_owner": None, "created_in": MEET, "by": "agent",
                        "quote": "Ravi said again he will write the forklift checklist this week"},
               "T011": {"title": "Another dock export", "kind": "task", "internal_owner": None, "client_owner": None,
                        "created_in": MEET, "by": "agent", "quote": "the dock export again"},
               "E001": {"title": "Yard programme", "kind": "epic", "internal_owner": None, "client_owner": None,
                        "created_in": MEET, "by": "agent", "quote": "the whole yard programme"},
               "T020": {"title": "Later meeting task", "kind": "task", "internal_owner": None, "client_owner": None,
                        "created_in": "2026-03-09_meetings_ef56ab78", "by": "agent", "quote": "a later meeting's task"}}


def _labels(tmp_path, answers, stamp="20260302-120000-2-abcdef12", record=REC, key_sha="abcdef12" + "0" * 56, **extra):
    f = tmp_path / "labels.json"
    f.write_text(json.dumps({"schema": "eval-labels/1", "stamp": stamp, "record": record, "key_version": "v1.2",
                             "key_sha": key_sha, "saved_at": "2026-03-03T09:00:00Z", "answers": answers} | extra))
    return f


def _read(f):
    return R.read_answers(f, "20260302-120000-2-abcdef12", key_sha="abcdef12" + "0" * 56, record=REC, key_version="v1.2")


def _refused(fn, *words):
    try:
        fn()
    except ValueError as e:
        for w in words:
            assert w in str(e), (w, str(e))
    else:
        raise AssertionError(f"accepted, expected a refusal naming {words}")


def test_a_labels_file_is_read_when_stamp_record_and_key_sha_match(tmp_path):
    ans = {"ab12cd34-001": {"action": "drop", "reason": "client_own", "note": "their own bug"},
           f"node:{MEET}:R004": {"action": "add", "kind": "risk", "commitment": "proposed"},
           f"judge:node:{MEET}:R004:is_work": {"action": "agree"}}
    assert _read(_labels(tmp_path, ans)) == ans


def test_a_labels_file_for_another_key_or_record_is_refused(tmp_path):
    _refused(lambda: _read(_labels(tmp_path, {}, stamp="20260302-120000-2-99999999")), "99999999", "abcdef12")
    _refused(lambda: _read(_labels(tmp_path, {}, key_sha="99999999" + "0" * 56)), "key")
    _refused(lambda: _read(_labels(tmp_path, {}, record="20260101-000000-1")), "20260101-000000-1")


def test_unknown_actions_and_fields_are_refused_naming_the_entry(tmp_path):
    _refused(lambda: _read(_labels(tmp_path, {"ab12cd34-001": {"action": "rename"}})), "ab12cd34-001", "rename")
    _refused(lambda: _read(_labels(tmp_path, {"ab12cd34-001": {"action": "drop", "colour": "red"}})), "ab12cd34-001", "colour")
    _refused(lambda: _read(_labels(tmp_path, {"ab12cd34-001": {"action": "modify", "fields": {"quote": "x"}}})),
             "ab12cd34-001", "quote")
    _refused(lambda: _read(_labels(tmp_path, {"ab12cd34-001": {"action": "drop", "reason": "boring"}})), "ab12cd34-001", "boring")
    _refused(lambda: _read(_labels(tmp_path, {f"node:{MEET}:R004": {"action": "drop"}})), f"node:{MEET}:R004", "drop")
    _refused(lambda: _read(_labels(tmp_path, {f"node:{MEET}:R004": {"action": "add", "commitment": "maybe"}})), "R004", "maybe")
    _refused(lambda: _read(_labels(tmp_path, {f"judge:node:{MEET}:R004:is_work": {"action": "add"}})), "judge:", "add")
    _refused(lambda: _read(_labels(tmp_path, {"ab12cd34-001": {"action": "drop"}}, schema="eval-labels/9")), "eval-labels/9")


def test_drop_removes_the_item_and_modify_changes_only_the_given_fields():
    answers = {"ab12cd34-001": {"action": "drop", "reason": "vague"},
               "ab12cd34-002": {"action": "modify", "fields": {"title": "Write the yard forklift checklist", "due": "2026-03-09"}},
               "ab12cd34-003": {"action": "keep"}}
    out = R.apply_answers(KEY, [], answers, record=REC, nodes=STORE_NODES)
    assert [g["cid"] for g in out] == ["ab12cd34-002", "ab12cd34-003"]
    assert out[0] == KEY[1] | {"title": "Write the yard forklift checklist", "due": "2026-03-09"}
    assert out[1] == KEY[2]


def test_a_node_added_to_the_key_carries_its_creating_quote():
    answers = {f"node:{MEET}:R004": {"action": "add", "kind": "risk", "commitment": "proposed"}}
    out = R.apply_answers(KEY, [], answers, record=REC, nodes=STORE_NODES)
    assert out[:3] == KEY
    new = out[3]
    assert new["cid"] == "ab12cd34-x001"
    assert (new["evidence"], new["quote"], new["title"], new["kind"], new["commitment"], new["owner"], new["source"]) == (
        MEET, "the crane permit could slip past the audit window", "Crane permit may slip", "risk", "proposed",
        "Opal Brennick", "user")
    assert REC in new["note"]


def test_a_node_duplicate_of_a_key_item_adds_its_quote_and_nothing_else():
    answers = {f"node:{MEET}:T009": {"action": "duplicate", "dup_of": "ab12cd34-002"},
               f"node:{MEET}:T011": {"action": "duplicate", "dup_of": "T009"},
               f"node:{MEET}:R004": {"action": "not_work", "reason": "brainstorm"},
               f"judge:node:{MEET}:R004:is_work": {"action": "disagree", "expected": "no"}}
    out = R.apply_answers(KEY, [], answers, record=REC, nodes=STORE_NODES)
    assert [g["cid"] for g in out] == [g["cid"] for g in KEY]
    assert out[1] == KEY[1] | {"quotes": [KEY[1]["quote"], "Ravi said again he will write the forklift checklist this week"]}
    assert out[0] == KEY[0] and out[2] == KEY[2]


def test_labels_naming_what_is_not_there_are_refused():
    _refused(lambda: R.apply_answers(KEY, [], {f"node:{MEET}:T404": {"action": "add", "kind": "task", "commitment": "committed"}},
                                     record=REC, nodes=STORE_NODES), "T404", REC)
    _refused(lambda: R.apply_answers(KEY, [], {"ab12cd34-404": {"action": "drop", "reason": "vague"}},
                                     record=REC, nodes=STORE_NODES), "ab12cd34-404")


def test_the_diff_counts_drops_by_reason_adds_modifications_and_quotes():
    answers = {"ab12cd34-001": {"action": "drop", "reason": "vague"},
               "ab12cd34-003": {"action": "modify", "fields": {"owner": "Nora Quill"}},
               f"node:{MEET}:R004": {"action": "add", "kind": "risk", "commitment": "proposed"},
               f"node:{MEET}:T009": {"action": "duplicate", "dup_of": "ab12cd34-002"}}
    out = R.apply_answers(KEY, [], answers, record=REC, nodes=STORE_NODES)
    ch = R.changes(KEY, out, answers)
    text = R.diff_text("v1.2", "v1.3", ch)
    assert text.splitlines()[0] == "v1.2 → v1.3: dropped 1 (vague 1), added 1, modified 1, quotes added 1"
    for word in ("ab12cd34-001", "ab12cd34-x001", "ab12cd34-003", "owner", "ab12cd34-002"):
        assert word in text


def _cli(tmp_path, answers):
    keys = tmp_path / "key"
    keys.mkdir()
    key = R.write_key(keys, KEY, "v1.2", "2026-03-02", "start")
    head, _ = load_gold(key)
    record = tmp_path / REC
    record.mkdir()
    (record / "meta.json").write_text(json.dumps({"project": str(tmp_path / "proj")}))
    stamp = f"{REC}-{head['sha256'][:8]}"
    labels = _labels(tmp_path, answers, stamp=stamp, key_sha=head["sha256"])
    return keys, key, record, labels


def test_apply_dry_run_prints_the_diff_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(R, "_nodes", lambda project: STORE_NODES)
    keys, key, record, labels = _cli(tmp_path, {"ab12cd34-001": {"action": "drop", "reason": "client_own"},
                                                f"node:{MEET}:R004": {"action": "add", "kind": "risk", "commitment": "proposed"}})
    before = sorted(p.name for p in keys.iterdir()), (keys / "CHANGELOG.md").read_text()
    assert R.main(["apply", "--record", str(record), "--key", str(key), "--answers", str(labels), "--version", "v1.3",
                   "--dry-run"]) == 0
    assert "v1.2 → v1.3: dropped 1 (client_own 1), added 1, modified 0, quotes added 0" in capsys.readouterr().out
    assert (sorted(p.name for p in keys.iterdir()), (keys / "CHANGELOG.md").read_text()) == before
    assert not (record / "relabel").exists()


def test_apply_writes_the_next_version_with_the_counts_in_the_changelog(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_nodes", lambda project: STORE_NODES)
    keys, key, record, labels = _cli(tmp_path, {"ab12cd34-001": {"action": "drop", "reason": "client_own"}})
    assert R.main(["apply", "--record", str(record), "--key", str(key), "--answers", str(labels), "--version", "v1.3",
                   "--note", "Nora's labels"]) == 0
    head, gold = load_gold(keys / "items.v1.3.jsonl")
    assert head["version"] == "v1.3" and [g["cid"] for g in gold] == ["ab12cd34-002", "ab12cd34-003"]
    line = (keys / "CHANGELOG.md").read_text().splitlines()[-1]
    assert "Nora's labels" in line and "dropped 1 (client_own 1)" in line and "added 0" in line


def test_apply_refuses_a_labels_file_for_another_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(R, "_nodes", lambda project: STORE_NODES)
    keys, key, record, _ = _cli(tmp_path, {})
    other = _labels(tmp_path, {"ab12cd34-001": {"action": "drop", "reason": "vague"}})
    assert R.main(["apply", "--record", str(record), "--key", str(key), "--answers", str(other), "--version", "v1.3",
                   "--note", "x"]) == 1
    assert "refused" in capsys.readouterr().out
    assert not (keys / "items.v1.3.jsonl").exists()


# ---------- fix round 1 ----------
def test_an_add_whose_evidence_is_not_where_the_node_was_created_is_refused():
    _refused(lambda: R.apply_answers(KEY, [], {f"node:{MEET}:T020": {"action": "add", "kind": "task", "commitment": "committed"}},
                                     record=REC, nodes=STORE_NODES), "T020", "2026-03-09_meetings_ef56ab78")


def test_a_duplicate_of_something_unknown_is_refused():
    _refused(lambda: R.apply_answers(KEY, [], {f"node:{MEET}:T009": {"action": "duplicate", "dup_of": "ab12cd34-777"}},
                                     record=REC, nodes=STORE_NODES), "T009", "ab12cd34-777")
    _refused(lambda: R.apply_answers(KEY, [], {f"node:{MEET}:T009": {"action": "duplicate", "dup_of": "T999"}},
                                     record=REC, nodes=STORE_NODES), "T999")


def test_a_duplicate_of_a_dropped_key_item_is_refused_as_contradictory():
    _refused(lambda: R.apply_answers(KEY, [], {"ab12cd34-002": {"action": "drop", "reason": "vague"},
                                               f"node:{MEET}:T009": {"action": "duplicate", "dup_of": "ab12cd34-002"}},
                                     record=REC, nodes=STORE_NODES), "T009", "ab12cd34-002", "drop")


def test_a_duplicate_of_a_node_added_in_the_same_file_passes_its_quote_to_the_new_item():
    answers = {f"node:{MEET}:R004": {"action": "add", "kind": "risk", "commitment": "proposed"},
               f"node:{MEET}:T011": {"action": "duplicate", "dup_of": f"node:{MEET}:R004"}}
    new = R.apply_answers(KEY, [], answers, record=REC, nodes=STORE_NODES)[3]
    assert new["quotes"] == ["the crane permit could slip past the audit window", "the dock export again"]
    bare = {f"node:{MEET}:R004": answers[f"node:{MEET}:R004"], f"node:{MEET}:T011": {"action": "duplicate", "dup_of": "R004"}}
    assert R.apply_answers(KEY, [], bare, record=REC, nodes=STORE_NODES)[3]["quotes"] == new["quotes"]


def test_modify_values_are_typed(tmp_path):
    for fields, word in (({"title": ""}, "title"), ({"owner": 5}, "owner"), ({"due": "next week"}, "due"),
                         ({"kind": "epic"}, "epic"), ({"commitment": "maybe"}, "maybe")):
        _refused(lambda: _read(_labels(tmp_path, {"ab12cd34-002": {"action": "modify", "fields": fields}})), "ab12cd34-002", word)
    ok = {"ab12cd34-002": {"action": "modify", "fields": {"owner": None, "due": None, "title": "Checklist"}}}
    assert _read(_labels(tmp_path, ok)) == ok


def test_an_add_without_a_kind_needs_a_node_kind_the_key_knows():
    _refused(lambda: R.apply_answers(KEY, [], {f"node:{MEET}:E001": {"action": "add", "commitment": "committed"}},
                                     record=REC, nodes=STORE_NODES), "E001", "epic")
    out = R.apply_answers(KEY, [], {f"node:{MEET}:T011": {"action": "add", "commitment": "committed"}}, record=REC, nodes=STORE_NODES)
    assert out[3]["kind"] == "task"


def test_a_labels_file_for_another_key_version_is_refused(tmp_path):
    _refused(lambda: _read(_labels(tmp_path, {}, key_version="v1.1")), "v1.1", "v1.2")


def test_added_items_record_their_source_record_and_edited_fields_override_the_node():
    answers = {f"node:{MEET}:R004": {"action": "add", "kind": "risk", "commitment": "proposed",
                                     "quote": "the crane permit could slip past the audit window",
                                     "fields": {"title": "Crane permit may slip past the audit", "owner": "Nora Quill", "due": "2026-03-20"}}}
    new = R.apply_answers(KEY, [], answers, record=REC, nodes=STORE_NODES)[3]
    assert new["source_record"] == REC
    assert (new["title"], new["owner"], new["due"]) == ("Crane permit may slip past the audit", "Nora Quill", "2026-03-20")
    old = R.apply_answers(KEY, R.page_items(ROWS, KEY, NODES), {"node:R004": {"kind": "committed"}}, record=REC)
    assert old[3]["source_record"] == REC


def test_a_node_label_is_matched_by_its_quote_when_its_id_is_not_in_this_store():
    answers = {f"node:{MEET}:T777": {"action": "add", "kind": "risk", "commitment": "proposed",
                                     "quote": "the crane permit could slip past the audit window"},
               f"node:{MEET}:T778": {"action": "duplicate", "dup_of": "T779", "quote": "the dock export again",
                                     "dup_of_quote": "Ravi said again he will write the forklift checklist this week"}}
    out = R.apply_answers(KEY, [], answers, record=REC, nodes=STORE_NODES)
    assert out[3]["title"] == "Crane permit may slip" and len(out) == 4
    _refused(lambda: R.apply_answers(KEY, [], {f"node:{MEET}:T777": {"action": "add", "kind": "risk", "commitment": "proposed",
                                                                    "quote": "nobody said this"}},
                                     record=REC, nodes=STORE_NODES), "T777", "quote")


def test_a_drop_note_with_reason_other_reaches_the_changelog_and_recurring_reasons_are_flagged():
    key = KEY + [dict(KEY[0], cid=f"ab12cd34-01{n}") for n in range(3)]
    answers = {"ab12cd34-001": {"action": "drop", "reason": "other", "note": "a duplicate of last week's"}} | {
        f"ab12cd34-01{n}": {"action": "drop", "reason": "client_own"} for n in range(3)}
    out = R.apply_answers(key, [], answers, record=REC, nodes=STORE_NODES)
    ch = R.changes(key, out, answers)
    assert "a duplicate of last week's" in R.counts_text(ch)
    text = R.diff_text("v1.2", "v1.3", ch)
    assert "client_own × 3: the tracking rule may need to say this; READER-block changes are your call" in text
    assert "other ×" not in text


def test_apply_prints_the_held_out_warning_and_the_rescore_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(R, "_nodes", lambda project: STORE_NODES)
    keys, key, record, labels = _cli(tmp_path, {f"node:{MEET}:R004": {"action": "add", "kind": "risk", "commitment": "proposed"}})
    other = tmp_path / "20260303-090000-2"
    other.mkdir()
    (other / "meta.json").write_text(json.dumps({"project": str(tmp_path / "copy2"), "tier": "2", "covered_by": False}))
    assert R.main(["apply", "--record", str(record), "--key", str(key), "--answers", str(labels), "--version", "v1.3",
                   "--records", str(record), str(other)]) == 0
    printed = capsys.readouterr().out
    assert "held out" in printed and REC in printed
    new_key = str(keys / "items.v1.3.jsonl")
    assert f"--project {tmp_path / 'copy2'} --key {new_key}" in printed and "--neutral" in printed
    assert f"--project {tmp_path / 'proj'} --key {new_key}" in printed


def test_apply_without_records_hints_at_the_rescore(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(R, "_nodes", lambda project: STORE_NODES)
    keys, key, record, labels = _cli(tmp_path, {"ab12cd34-001": {"action": "drop", "reason": "vague"}})
    assert R.main(["apply", "--record", str(record), "--key", str(key), "--answers", str(labels), "--version", "v1.3"]) == 0
    assert "--records" in capsys.readouterr().out


def test_expected_errors_are_refusals_not_tracebacks(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(R, "_nodes", lambda project: STORE_NODES)
    keys, key, record, labels = _cli(tmp_path, {f"node:{MEET}:R004": {"action": "add", "kind": "risk", "commitment": "proposed"}})
    args = ["apply", "--record", str(record), "--key", str(key), "--answers", str(labels), "--version", "v1.2"]
    assert R.main(args) == 1 and "refused" in capsys.readouterr().out   # v1.2 exists: never overwritten
    (record / "meta.json").unlink()
    assert R.main(args[:-1] + ["v1.3"]) == 1 and "refused" in capsys.readouterr().out
    bad = tmp_path / "list.json"
    bad.write_text("[]")
    assert R.main(["apply", "--record", str(record), "--key", str(key), "--answers", str(bad), "--version", "v1.3"]) == 1
    assert "refused" in capsys.readouterr().out
    assert not (keys / "items.v1.3.jsonl").exists()


def test_an_old_format_apply_keeps_the_note_first_exactly(tmp_path, monkeypatch, capsys):
    keys, key, record, _ = _cli(tmp_path, {})
    head, _ = load_gold(key)
    stamp = f"{REC}-{head['sha256'][:8]}"
    (record / "relabel").mkdir()
    (record / "relabel" / "items.json").write_text(json.dumps({"stamp": stamp, "items": R.page_items(ROWS, KEY, NODES)}))
    old = tmp_path / "key-labels.json"
    old.write_text(json.dumps({"stamp": stamp, "answers": {"ab12cd34-001": {"kind": "not_work"}}}))
    assert R.main(["apply", "--record", str(record), "--key", str(key), "--answers", str(old), "--version", "v1.3",
                   "--note", "labels from record r, 3 Mar"]) == 0
    line = (keys / "CHANGELOG.md").read_text().splitlines()[-1]
    assert line.split(" — ", 1)[1].startswith("labels from record r, 3 Mar")


# ---------- fix round 2 ----------
def test_a_quote_two_nodes_of_the_meeting_share_is_ambiguous_and_refused():
    nodes = STORE_NODES | {"T030": dict(STORE_NODES["R004"], title="Crane permit, again")}
    _refused(lambda: R.apply_answers(KEY, [], {f"node:{MEET}:T777": {"action": "add", "kind": "risk", "commitment": "proposed",
                                                                    "quote": "the crane permit could slip past the audit window"}},
                                     record=REC, nodes=nodes), "T777", "ambiguous", "R004", "T030")


def test_a_duplicate_of_an_added_node_found_by_quote_passes_its_quote():
    answers = {f"node:{MEET}:T777": {"action": "add", "kind": "risk", "commitment": "proposed",
                                     "quote": "the crane permit could slip past the audit window"},
               f"node:{MEET}:T011": {"action": "duplicate", "dup_of": "T888",
                                     "dup_of_quote": "the crane permit could slip past the audit window"}}
    new = R.apply_answers(KEY, [], answers, record=REC, nodes=STORE_NODES)[3]
    assert new["quotes"] == ["the crane permit could slip past the audit window", "the dock export again"]


def test_a_non_object_answer_in_an_old_format_file_is_refused(tmp_path, capsys):
    keys, key, record, _ = _cli(tmp_path, {})
    head, _ = load_gold(key)
    stamp = f"{REC}-{head['sha256'][:8]}"
    (record / "relabel").mkdir()
    (record / "relabel" / "items.json").write_text(json.dumps({"stamp": stamp, "items": R.page_items(ROWS, KEY, NODES)}))
    old = tmp_path / "key-labels.json"
    old.write_text(json.dumps({"stamp": stamp, "answers": {"ab12cd34-001": "not_work"}}))
    assert R.main(["apply", "--record", str(record), "--key", str(key), "--answers", str(old), "--version", "v1.3"]) == 1
    assert "ab12cd34-001" in capsys.readouterr().out
    assert not (keys / "items.v1.3.jsonl").exists()


def test_the_dry_run_lists_every_label_matched_by_quote_alone(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(R, "_nodes", lambda project: STORE_NODES)
    keys, key, record, labels = _cli(tmp_path, {
        f"node:{MEET}:T777": {"action": "add", "kind": "risk", "commitment": "proposed",
                              "quote": "the crane permit could slip past the audit window"},
        f"node:{MEET}:T011": {"action": "duplicate", "dup_of": "T999",
                              "dup_of_quote": "Ravi said again he will write the forklift checklist this week"},
        f"node:{MEET}:T009": {"action": "duplicate", "dup_of": "ab12cd34-002",
                              "quote": "Ravi said again he will write the forklift checklist this week"}})
    assert R.main(["apply", "--record", str(record), "--key", str(key), "--answers", str(labels), "--version", "v1.3",
                   "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"node:{MEET}:T777 → R004 matched by quote" in out
    assert f"node:{MEET}:T011 dup_of T999 → T009 matched by quote" in out
    assert "T009 matched by quote" not in out.replace(f"T011 dup_of T999 → T009 matched by quote", "")


def test_a_judge_label_may_carry_the_expected_value_on_agree_and_the_nodes_quote(tmp_path):
    ans = {"judge:ab12cd34-001:same_work": {"action": "agree", "expected": "yes", "quote": "Nora said she would polish the deck",
                                            "seen_judge": False},
           "judge:ab12cd34-002:which_node": {"action": "disagree", "expected": "T009", "quote": NODES["T007"]["quote"], "seen_judge": True}}
    assert _read(_labels(tmp_path, ans)) == ans
    _refused(lambda: _read(_labels(tmp_path, {"judge:ab12cd34-001:same_work": {"action": "agree", "expected": 3}})), "expected", "3")
    _refused(lambda: _read(_labels(tmp_path, {"judge:ab12cd34-001:same_work": {"action": "agree", "quote": " "}})), "quote")
    _refused(lambda: _read(_labels(tmp_path, {"judge:ab12cd34-001:same_work": {"action": "agree", "dup_of": "T009"}})), "dup_of")


def test_an_entry_may_say_which_earlier_answer_it_carries(tmp_path):
    ans = {"ab12cd34-001": {"action": "drop", "reason": "vague", "carried_from": "you, 2026-03-01 (key-labels)"},
           "judge:ab12cd34-001:same_work": {"action": "agree", "expected": "yes", "carried_from": "you, 2026-03-01 (key-labels)"}}
    assert _read(_labels(tmp_path, ans)) == ans
    _refused(lambda: _read(_labels(tmp_path, {"ab12cd34-001": {"action": "keep", "carried_from": 7}})), "carried_from")
    _refused(lambda: _read(_labels(tmp_path, {"ab12cd34-001": {"action": "keep", "carried_from": ""}})), "carried_from")


def test_the_dry_run_marks_a_carried_answer_and_counts_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(R, "_nodes", lambda project: STORE_NODES)
    src = "you, 2026-03-01 (key-labels)"
    keys, key, record, labels = _cli(tmp_path, {"ab12cd34-001": {"action": "drop", "reason": "vague", "carried_from": src},
                                                "ab12cd34-002": {"action": "drop", "reason": "vague"}})
    assert R.main(["apply", "--record", str(record), "--key", str(key), "--answers", str(labels), "--version", "v1.3",
                   "--dry-run"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].endswith("carried 1") and "dropped 2" in lines[0]
    one = next(l for l in lines if "- drop ab12cd34-001" in l)
    two = next(l for l in lines if "- drop ab12cd34-002" in l)
    assert one.endswith(f" (carried from {src})") and "carried from" not in two
