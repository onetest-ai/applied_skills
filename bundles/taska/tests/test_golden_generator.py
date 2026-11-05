"""evals/golden/generate.py: invented meetings with exactly known answers, written as task-bank tasks. Invented data
only (the Harbor Logistics cast)."""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_TASKA_ROOT = Path(__file__).resolve().parents[1]
EVALS = _TASKA_ROOT / "evals"
for _p in (_TASKA_ROOT / "skills" / "run", EVALS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bank as B  # noqa: E402
from golden import generate as G  # noqa: E402
from taska_lib import config as C, store as S, transcripts as TR  # noqa: E402
from taska_lib.text import norm  # noqa: E402

SEEDS = [1, 2, 3]
RULINGS = {"BRAINSTORM": "work", "PEOPLE_MATTER": "not_work"}   # STATUS_REPORT, like every absent category, pending

QUESTIONS = {
    "WORKSHOP_HANDLING": "Is preparing or running a workshop (deck edits, session order, agendas, invites, posters, "
                         "how-might-we edits) ever work to track?",
    "OTHER": "Are scheduling, interview logistics and system-access setup work to track?",
    "RISK_NOT_RISK": "Is the client's own business problem (a pain point raised in a workshop) a risk to track, or a "
                     "finding?",
    "DISCUSSION_NOT_DECISION": "Is an opinion or observation in a discussion a decision to track?",
    "BRAINSTORM": "Are workshop hypotheses and brainstorming ideas work to track?",
    "STATUS_REPORT": "Is a status report (what was done) new work to track?",
    "PEOPLE_MATTER": "Are people matters (staffing, rates) ever tracked?",
}
GUARD_WORK = ["Deliver the readout deck to the client", "Present the roadmap at the board meeting",
              "Build the executive readout", "Prepare the workshop materials for the client session",
              "Draft the inventory scope rationale document"]
GUARD_NOT = ["Soften the language of finding #2 in the deck", "Add screenshots to the deck appendix",
             "Send the agenda and invites", "Print the posters", "Reorder the sessions",
             "Merge the how-might-we statements", "Review the slides before Friday"]
MISS_PATTERNS = {"paraphrase", "hedge", "split_turns", "interruption", "backchannel", "asr_speakerless",
                 "owner_not_speaker"}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    project = tmp_path_factory.mktemp("golden") / "proj"
    project.mkdir()
    tasks, problems = G.bank_tasks(SEEDS, project, RULINGS)
    return project, tasks, problems


def _checks(task):
    return [(typ, c) for typ in ("must_create", "must_not_create", "no_duplicate") for c in task.get(typ, [])]


def test_generation_is_byte_identical_per_seed():
    one = json.dumps(G.generate(1), sort_keys=True)
    assert one == json.dumps(G.generate(1), sort_keys=True)
    code = ("import json,sys; sys.path.insert(0, sys.argv[1]); from golden import generate as G; "
            "sys.stdout.write(json.dumps(G.generate(1), sort_keys=True))")
    for hs in ("0", "123"):
        out = subprocess.run([sys.executable, "-c", code, str(EVALS)], capture_output=True, text=True, check=True,
                             env={**os.environ, "PYTHONHASHSEED": hs}).stdout
        assert hashlib.sha256(out.encode()).hexdigest() == hashlib.sha256(one.encode()).hexdigest()
    two = G.generate(2)
    assert two["vtts"] != G.generate(1)["vtts"]
    assert all(v.endswith("\n") and "\r" not in v for v in two["vtts"].values())

    def shape(g):
        return sorted((s["archetype"], s["family"] or "", s["expected"], s["kind"] or "", s["commitment"] or "",
                       s["category"] or "") for s in g["slots"])
    assert shape(G.generate(1)) == shape(two) == shape(G.generate(3))


def test_every_slot_is_located_in_the_synced_evidence_with_an_exact_span(built):
    project, tasks, problems = built
    assert problems == []
    ev = S.evidence(C.load(project).store)
    n_checks = 0
    for t in tasks:
        item = ev[t["evidence"]]
        assert t["transcript_sha"] == B.transcript_sha(item)
        text = norm(S.full_text(item))
        for _, c in _checks(t):
            n_checks += 1
            s, e = c["span"]
            assert text[s:e] == norm(c["quote"]), (t["task"], c["id"])
            assert text.find(norm(c["quote"]), s + 1) < 0, f"{c['id']}: the quote is not unique"
            assert c["status"] == "draft" and c["difficulty"] is None
    assert n_checks == sum(len(G.generate(s)["slots"]) for s in SEEDS)
    B.write_bank(project / "bank", tasks, "v1", "2026-11-03", "golden generator test")


def test_the_one_block_readout_is_one_turn_of_at_least_60000_chars(built):
    g = G.generate(1)
    files = {s["file"] for s in g["slots"] if s["archetype"] == "one_block_readout"}
    assert len(files) == 1
    turns = TR.stitch(TR._cues_vtt_srt(g["vtts"][files.pop()]))
    assert len(turns) == 1 and len(turns[0]["text"]) >= 60000
    _, tasks, _ = built
    one = [t for t in tasks if t["class"]["structure"] == "one block"]
    assert one and all(t["class"]["size"] == "XL" for t in one)
    assert all("one_block" in c["caps"] for t in one for _, c in _checks(t))
    conv = [t for t in tasks if t["archetype"] == "xl_conversation_readout"]
    assert conv and all(t["class"] == dict(t["class"], size="XL", structure="conversation") for t in conv)


def test_every_class_is_filled_or_named_empty(built):
    _, tasks, problems = built
    assert G.empty_classes(tasks) == {}
    matrix = G.class_matrix(tasks)
    assert all(n >= G.CLASS_MIN for vals in matrix.values() for n in vals.values())
    assert set(matrix) == set(B.CLASSES) and all(set(matrix[d]) == set(B.CLASSES[d]) for d in B.CLASSES)
    small = [t for t in tasks if t["class"]["size"] == "S"]
    gaps = G.empty_classes(small)
    assert set(gaps["size"]) == {"M", "L", "XL"} and "one block" in gaps["structure"]
    page = G.review_page(small, problems + ["<script>alert(1)</script>"])
    assert "size = XL" in page and "structure = one block" in page
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    assert "http://" not in page and "https://" not in page


def test_rule_slots_follow_rulings_and_pending_ones_are_marked(built):
    assert {k: v["question"] for k, v in G.CATEGORIES.items()} == QUESTIONS
    for seed in SEEDS:
        invented = [s for s in G.generate(seed)["slots"] if s["category"] and s["archetype"] != "planted"]
        for cat in QUESTIONS:
            assert sum(s["category"] == cat for s in invented) >= 2, (seed, cat)
    _, tasks, _ = built
    seen = set()
    for t in tasks:
        for typ, c in _checks(t):
            cat = c.get("category")
            if typ == "no_duplicate":
                continue
            if not cat:
                assert c["pending_ruling"] is False
                continue
            seen.add(cat)
            if RULINGS.get(cat) == "work":
                assert typ == "must_create" and c["pending_ruling"] is False
            elif RULINGS.get(cat) == "not_work":
                assert typ == "must_not_create" and c["pending_ruling"] is False and c["why"] == G.CATEGORIES[cat]["why"]
            else:
                assert typ == "must_not_create" and c["pending_ruling"] is True
    assert seen == set(QUESTIONS)
    page = G.review_page(tasks, [])
    assert "pending ruling" in page and QUESTIONS["RISK_NOT_RISK"].replace("'", "&#x27;") in page


def test_the_not_work_guard_sentences_are_present_verbatim():
    g = G.generate(1)
    every = "\n".join(g["vtts"].values())
    for sentence in GUARD_WORK + GUARD_NOT:
        assert sentence in every, sentence
        slots = [s for s in g["slots"] if s["utterance"] == sentence]
        assert len(slots) == 1, sentence
        if sentence in GUARD_WORK:
            assert slots[0]["expected"] == "must_create" and not slots[0]["pending_ruling"] and not slots[0]["category"]
        else:   # handling: the WORKSHOP_HANDLING rule question, scored once ruled
            assert slots[0]["expected"] == "must_not_create" and slots[0]["pending_ruling"]
            assert slots[0]["category"] == "WORKSHOP_HANDLING"


def test_every_guard_sentence_has_an_exact_span_on_the_synced_text(built):
    project, tasks, _ = built
    ev = S.evidence(C.load(project).store)
    for sentence in GUARD_WORK + GUARD_NOT:
        hits = [(t, c) for t in tasks if t["seed"] == 1 for _, c in _checks(t) if c["quote"] == sentence]
        assert len(hits) == 1, sentence
        t, c = hits[0]
        assert norm(S.full_text(ev[t["evidence"]]))[c["span"][0]:c["span"][1]] == norm(sentence)


def test_real_miss_patterns_and_continuity_are_templated(built):
    g = G.generate(1)
    fams = {s["family"] for s in g["slots"] if s["expected"] == "must_create"}
    assert MISS_PATTERNS <= fams
    asr = [s for s in g["slots"] if s["archetype"] == "asr_call"]
    assert asr and all(s["speaker"] == "?" for s in asr)
    _, tasks, _ = built
    by_id = {t["task"]: t for t in tasks}
    follow = [t for t in tasks if t.get("after")]
    assert len(follow) == len(SEEDS)
    for t in follow:
        first = by_id[t["after"][0]]
        assert first["archetype"] == "continuity_1" and t["archetype"] == "continuity_2"
        assert any("continuity" in c["caps"] for c in t["no_duplicate"])


def test_only_invented_names():
    cast = set(G.cast_names())
    assert {"Nora Quill", "Ravi Tamsin", "Opal Brennick"} <= cast
    teams = {t["name"] for t in G.TEMPLATES["teams"]}
    for seed in SEEDS:
        g = G.generate(seed)
        speakers = {m for v in g["vtts"].values() for m in re.findall(r"<v ([^>]+)>", v)}
        assert speakers <= cast | {"?"}, speakers - cast
        owners = {s["owner"] for s in g["slots"] if s["owner"]}
        assert owners <= cast | teams, owners - cast - teams


def test_planted_open_question_is_must_not_create_and_plan_shift_is_a_delay():
    g = G.generate(1)
    oq = [s for s in g["slots"] if s["family"] == "planted:open_question"]
    assert oq and all(s["expected"] == "must_not_create" and s["why"] == "not_work" for s in oq)
    shift = [s for s in g["slots"] if s["family"] == "plan_shift"]
    assert len(shift) == 1 and shift[0]["work"]["effect"] == "delayed" and shift[0]["work"]["kind"] != "decision"
    assert sum(s["category"] == "STATUS_REPORT" for s in g["slots"]) == 4


def test_continuity_uses_no_duplicate_and_the_done_report_waits_for_a_ruling(built):
    _, tasks, _ = built
    by_id = {t["task"]: t for t in tasks}
    for t in (t for t in tasks if t["archetype"] == "continuity_2"):
        first = by_id[t["after"][0]]
        assert t["must_not_create"] == []
        dup = {c["family"]: c for c in t["no_duplicate"]}
        assert set(dup) == {"restated", "done_report"}
        assert all(c["why"] == "duplicate" and "not_work" not in c["caps"] and "continuity" in c["caps"] for c in dup.values())
        assert dup["restated"]["pending_ruling"] is False and dup["done_report"]["pending_ruling"] is True
        assert dup["done_report"]["expect_status"] == "done"
        first_ids = {c["id"]: c for c in first["must_create"]}
        assert "reefer alarm report" in first_ids[dup["restated"]["of"]]["quote"]
        assert "carrier onboarding letter" in first_ids[dup["done_report"]["of"]]["quote"]


def test_a_work_ruling_on_people_matters_is_refused(tmp_path):
    with pytest.raises(ValueError, match="PEOPLE_MATTER"):
        G.bank_tasks([1], tmp_path / "p", {"PEOPLE_MATTER": "work"})
    assert not (tmp_path / "p").exists()


def test_multi_turn_spans_cover_every_turn(built):
    _, tasks, _ = built
    fams = {}
    for t in tasks:
        for _, c in _checks(t):
            fams.setdefault(c["family"], []).append(norm(c["quote"]))
    assert all("could you write up the gate check procedure" in q and "that write up is mine" in q
               and "new lighting" in q for q in fams["split_turns"])
    assert all("dock schedule export broke" in q and "i'll sort out the export" in q for q in fams["paraphrase"])
    assert all("you are still on the cold store alarm reporting" in q and "still mine to deliver" in q
               for q in fams["restated"])


def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def test_every_capitalised_word_in_the_templates_is_allowed():
    t = G.TEMPLATES
    allow = {w for n in G.cast_names() for w in n.split()} | set(t["invented"]) | {"I", "I'll", "Mm-hm"}
    allow |= {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "March", "May", "June"}
    allow |= {d + "s" for d in allow if d.endswith("day")}
    bad = set()
    for s in _strings({k: v for k, v in t.items() if k not in ("categories",)}):
        for sentence in re.split(r"(?:^|[.?!:;—]\s+)", s):
            words = re.findall(r"[A-Za-z][\w'#-]*", re.sub(r"\{[^}]*\}", "", sentence))
            for w in words[1:]:
                if w[0].isupper() and w not in allow and not re.fullmatch(r"[A-Z_]+", w):
                    bad.add(w)
    assert not bad, sorted(bad)
    assert "DockFlow" not in json.dumps(t) and "Pier 9" not in json.dumps(t)


def test_the_xl_conversation_middle_third_holds_only_its_two_quiet_commitments(built):
    project, tasks, _ = built
    ev = S.evidence(C.load(project).store)
    for t in (t for t in tasks if t["archetype"] == "xl_conversation_readout"):
        n = len(norm(S.full_text(ev[t["evidence"]])))
        middle = sorted(c["family"] for _, c in _checks(t) if 1 / 3 <= c["span"][0] / n < 2 / 3)
        assert middle == ["buried", "hedge"], (t["seed"], middle)


COMMITS = re.compile(r"\b(?:i|we)(?:'ll|\s+will|\s+shall|\s+am going to|\s+are going to)\b|\bwill do\b|\bfollow up\b", re.I)


def test_filler_is_neutral_and_the_one_block_readout_varies():
    import random
    lines = G.filler_lines() + [G.frame_sentence(random.Random(i)) for i in range(500)]
    names = G.cast_names()
    from taska_lib import sensitive
    assert not [x for x in lines if sensitive.people_matter(x, names=names) or COMMITS.search(x)]
    g = G.generate(1)
    vtt = next(v for f, v in g["vtts"].items() if "findings readout" in f)
    text = TR.stitch(TR._cues_vtt_srt(vtt))[0]["text"]
    sentences = [x for x in re.split(r"(?<=[.?!])\s+", text) if x]
    assert len(set(sentences)) / len(sentences) >= 0.6


SIZES = {"one_block_readout": 60000, "xl_conversation_readout": 60000, "l_working_session": 30000,
         "status_standup": 10000}


def test_sized_meetings_clear_their_class_boundary_on_the_normalised_text(built):
    project, tasks, _ = built
    ev = S.evidence(C.load(project).store)
    for t in tasks:
        n = len(norm(S.full_text(ev[t["evidence"]])))
        if t["archetype"] in SIZES:
            assert n >= SIZES[t["archetype"]], (t["archetype"], n)
            assert len(S.full_text(ev[t["evidence"]])) >= SIZES[t["archetype"]] * 1.02
        elif t["class"]["size"] == "S":
            assert len(S.full_text(ev[t["evidence"]])) < 10000 * 0.97


def _rulings(tmp_path, body):
    f = tmp_path / "rulings.json"
    f.write_text(json.dumps(body))
    return str(f)


def test_review_writes_the_page_and_prints_counts_but_no_bank(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(G, "_HERE", tmp_path)
    rc = G.main(["review", "--seeds", "1", "--project", str(tmp_path / "p"), "--out", str(tmp_path / "rv"),
                 "--rulings", _rulings(tmp_path, {"BRAINSTORM": "work", "PEOPLE_MATTER": "not_work"})])
    assert rc == 0
    assert "Golden bank review" in (tmp_path / "rv" / "golden-review.html").read_text()
    printed = json.loads(capsys.readouterr().out)
    assert printed["tasks"] == 11
    assert set(printed) >= {"per_capability", "per_class", "per_status", "empty_classes", "pending_ruling", "problems"}
    assert printed["per_class"]["size"] and printed["per_capability"] and printed["per_status"]["draft"] > 0
    assert printed["pending_ruling"] > 0
    assert not list((tmp_path / "rv").glob("bank.*")) and not list(tmp_path.glob("bank.*"))


def _check(i, caps, pending=False, status="draft"):
    return {"id": f"t-x{i}", "caps": caps, "status": status, **({"pending_ruling": True} if pending else {})}


def test_describe_keeps_pending_checks_in_each_capability():
    tasks = [{"task": "t1", "class": {"size": "S"}, "must_create": [_check(1, ["owners"], True), _check(2, ["owners"], True)],
              "must_not_create": [_check(3, ["decisions"]), _check(4, ["decisions"], True, "reviewed")]},
             {"task": "t2", "class": {"size": "M"}, "must_create": [], "must_not_create": []}]
    d = B.describe(tasks)
    assert d["per_capability"] == {"owners": {"n": 2, "pending": 2}, "decisions": {"n": 2, "pending": 1}}
    assert d["pending_ruling"] == 3 and d["per_status"] == {"draft": 3, "reviewed": 1}
    assert d["per_class"]["size"] == {"S": 1, "M": 1}
    assert "L" in d["empty_classes"]["size"]


def test_review_counts_agree_with_the_pages_own_matrix(built):
    _, tasks, _ = built
    c = G.counts(tasks)
    for dim, vals in G.class_matrix(tasks).items():
        assert c["per_class"][dim] == {v: n for v, n in vals.items() if n}
    assert c["empty_classes"] == G.empty_classes(tasks)
    assert sum(c["per_status"].values()) == sum(len(t.get(typ, [])) for t in tasks for typ in
                                                ("must_create", "must_not_create", "no_duplicate"))


def test_write_writes_the_version_and_refuses_an_existing_one(tmp_path, capsys):
    args = ["write", "--seeds", "1", "--project", str(tmp_path / "p"), "--out", str(tmp_path / "bank"),
            "--version", "v1", "--date", "2026-11-03"]
    assert G.main(args) == 0
    head, tasks = B.load_bank(tmp_path / "bank" / "bank.v1.jsonl")
    assert head["n"] == len(tasks) == 11
    before = (tmp_path / "bank" / "bank.v1.jsonl").read_text()
    assert G.main(args[:4] + [str(tmp_path / "p2")] + args[5:]) == 2
    assert "never overwritten" in capsys.readouterr().err
    assert (tmp_path / "bank" / "bank.v1.jsonl").read_text() == before


def test_write_defaults_to_the_golden_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "_HERE", tmp_path)
    assert G.main(["write", "--seeds", "1", "--project", str(tmp_path / "p"), "--version", "v1",
                   "--date", "2026-11-03"]) == 0
    assert (tmp_path / "bank.v1.jsonl").exists()


@pytest.mark.parametrize("body", [{"NOPE": "work"}, {"BRAINSTORM": "maybe"}, {"BRAINSTORM": "pending"},
                                  {"PEOPLE_MATTER": "work"}, ["x"]])
def test_bad_rulings_are_refused_with_a_message(tmp_path, capsys, body):
    rc = G.main(["review", "--seeds", "1", "--project", str(tmp_path / "p"), "--out", str(tmp_path / "rv"),
                 "--rulings", _rulings(tmp_path, body)])
    assert rc == 2 and capsys.readouterr().err.startswith("error: ")
    assert not (tmp_path / "rv").exists()


def test_problems_exit_1_and_write_no_bank(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "bank_tasks", lambda seeds, project, rulings: ([], ["a slot is not located"]))
    assert G.main(["review", "--seeds", "1", "--out", str(tmp_path / "rv")]) == 1
    assert G.main(["write", "--seeds", "1", "--out", str(tmp_path / "bank"), "--version", "v1"]) == 1
    assert not list(tmp_path.rglob("bank.*"))


def test_write_bank_failures_are_errors_not_tracebacks(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise ValueError("task t1: bad")
    monkeypatch.setattr(B, "write_bank", boom)
    rc = G.main(["write", "--seeds", "1", "--project", str(tmp_path / "p"), "--out", str(tmp_path / "bank"),
                 "--version", "v1"])
    assert rc == 2 and capsys.readouterr().err.startswith("error: ")


def test_rulings_values_are_one_constant():
    assert G.VALUES == ("work", "not_work")


def test_main_without_rulings_leaves_every_category_pending(tmp_path, capsys):
    assert G.main(["review", "--seeds", "1", "--project", str(tmp_path / "p"), "--out", str(tmp_path / "rv")]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["pending_ruling"] > 0
    page = (tmp_path / "rv" / "golden-review.html").read_text()
    assert f"{printed['pending_ruling']} pending ruling" in page


def test_one_validator_refuses_the_same_rulings_from_a_file_and_from_code(tmp_path):
    with pytest.raises(ValueError, match="PEOPLE_MATTER cannot be ruled work"):
        G.read_rulings(_rulings(tmp_path, {"PEOPLE_MATTER": "work"}))
    with pytest.raises(ValueError, match="BRAINSTORM"):
        G.bank_tasks([1], tmp_path / "p", {"BRAINSTORM": "pending"})
    assert not (tmp_path / "p").exists()


def test_without_project_the_scratch_folder_is_removed(tmp_path, monkeypatch):
    import tempfile
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    assert G.main(["review", "--seeds", "1", "--out", str(tmp_path / "rv")]) == 0
    assert list(scratch.iterdir()) == []
