"""evals/bank.py init: bank v1 from an answer key and adjudications, and the review page. Invented data only."""
import json
import subprocess
import sys

from conftest import TASKA_ROOT
from test_bank_grade import MEET, NORM, S1, S2, S3, S4, project, span

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import bank as B  # noqa: E402

TASK = MEET.rsplit("_", 1)[1]


def krow(cid, quote, **kw):
    return {"cid": cid, "evidence": MEET, "quote": quote, "ts": "00:01:00", "speaker": "Nora Quill", "offset": 0,
            "title": "t", "kind": "task", "commitment": "committed", "owner": "Ravi Tamsin", "due": None,
            "status": "open", "client_owed": False, "covered_by": None} | kw


def case(n, quote, ev=MEET):
    return {"case": f"harbor|abc|T{n}", "project_copy": "x", "evidence_id": ev,
            "node": {"kind": "task", "title": "t", "quote": quote}}


def verdict(n, v, why=None):
    return {"case": f"harbor|abc|T{n}", "verdict": v, "why": why, "dup_of": None, "note": ""}


def ids(tasks, field="must_create"):
    return [e["id"] for t in tasks for e in t[field]]


def test_init_makes_one_task_per_key_meeting_with_spans_from_quotes(tmp_path):
    p = project(tmp_path)
    key = [krow("k-001", S1), krow("k-002", S3, kind="decision", commitment=None, owner=None, client_owed=True),
           krow("k-003", S4, commitment="proposed", owner="Opal Brennick")]
    tasks, problems = B.init(p, key, [], key_version="v1.2")
    assert problems == [] and len(tasks) == 1
    t = tasks[0]
    assert t["task"] == TASK and t["evidence"] == MEET and set(t["outcome"]) == set(B.OUTCOMES)
    assert t["budget"] == {"cost_usd": None, "minutes": None}
    from taska_lib import config as C, store as S
    assert t["transcript_sha"] == B.transcript_sha(S.evidence(C.load(p).store)[MEET])
    m = {e["id"]: e for e in t["must_create"]}
    assert m["k-001"]["span"] == span(S1) and m["k-001"]["quote"] == S1 and m["k-001"]["source"] == "key v1.2"
    assert m["k-001"]["caps"] == ["committed", "owners"] and m["k-001"]["owner"] == "Ravi Tamsin"
    assert m["k-002"]["caps"] == ["decisions", "client_owed"]
    assert m["k-003"]["caps"] == ["proposed", "owners"]


def test_a_key_quote_that_cannot_be_found_is_a_problem_not_a_guess(tmp_path):
    p = project(tmp_path)
    tasks, problems = B.init(p, [krow("k-001", S1), krow("k-009", "Nobody ever said this sentence at all")], [])
    assert any("k-009" in x for x in problems) and "k-009" not in ids(tasks) and "k-001" in ids(tasks)


def test_not_work_verdicts_become_must_not_spans_and_duplicates_become_nothing(tmp_path):
    p = project(tmp_path)
    cases = [case(1, S2), case(2, S4), case(3, S3), case(4, S3, ev="2026-03-03_meetings_ffff0000")]
    verdicts = [verdict(1, "NOT_WORK", "BRAINSTORM"), verdict(2, "DUPLICATE"), verdict(3, "REAL_WORK"),
                verdict(4, "NOT_WORK", "PEOPLE")]
    tasks, problems = B.init(p, [krow("k-001", S1)], [(cases, verdicts)])
    mn = tasks[0]["must_not_create"]
    assert len(mn) == 1 and mn[0]["id"] == f"{TASK}-n01" and mn[0]["why"] == "brainstorm"
    assert mn[0]["span"] == span(S2) and mn[0]["caps"] == ["not_work"] and mn[0]["source"] == "rca:harbor|abc|T1"
    assert any("not a key meeting" in x and "T4" in x for x in problems)
    B.write_bank(tmp_path / "bank", tasks, "v1", "2026-11-03", "t")


def test_why_mapping(tmp_path):
    p = project(tmp_path)
    cases = [case(1, S2), case(2, S3), case(3, S4)]
    verdicts = [verdict(1, "NOT_WORK", "CLIENT_SIDE"), verdict(2, "NOT_WORK", "PEOPLE"), verdict(3, "NOT_WORK", "OTHER")]
    tasks, _ = B.init(p, [krow("k-001", S1)], [(cases, verdicts)])
    got = {e["span"][0]: (e["why"], e["caps"]) for e in tasks[0]["must_not_create"]}
    assert got[span(S2)[0]] == ("client_own", ["not_work"])
    assert got[span(S3)[0]] == ("people", ["people"])
    assert got[span(S4)[0]] == ("not_work", ["not_work"])


def test_a_must_not_span_overlapping_a_must_create_span_is_a_conflict_written_as_neither(tmp_path):
    p = project(tmp_path)
    half = S1[:40]
    tasks, problems = B.init(p, [krow("k-001", S1), krow("k-002", S3)],
                             [([case(1, half)], [verdict(1, "NOT_WORK", "BRAINSTORM")])])
    assert "k-001" not in ids(tasks) and "k-002" in ids(tasks) and ids(tasks, "must_not_create") == []
    assert any("k-001" in x and "T1" in x for x in problems)


def test_the_review_page_lists_every_span_with_its_quote_and_every_problem(tmp_path):
    p = project(tmp_path)
    tasks, problems = B.init(p, [krow("k-001", S1), krow("k-009", "Missing <b>words</b> here")],
                             [([case(1, S2)], [verdict(1, "NOT_WORK", "BRAINSTORM")])])
    html = B.review_page(tasks, problems)
    for needle in ("k-001", S1, f"{TASK}-n01", S2, "brainstorm", "committed", "Ravi Tamsin", "k-009"):
        assert needle in html
    assert "<b>words</b>" not in html and "&lt;b&gt;words&lt;/b&gt;" in html
    assert "http://" not in html and "https://" not in html
    sp = tasks[0]["must_create"][0]["span"]
    assert f"{sp[0]}-{sp[1]}" in html
    import evalview
    assert evalview.STYLE in html


def test_cli_dry_run_writes_only_the_review_page(tmp_path):
    p = project(tmp_path / "proj")
    key = tmp_path / "items.v1.jsonl"
    key.write_text("\n".join(json.dumps(r) for r in [krow("k-001", S1)]) + "\n")
    cases, verds = tmp_path / "c.json", tmp_path / "v.json"
    cases.write_text(json.dumps([case(1, S2)]))
    verds.write_text(json.dumps([verdict(1, "NOT_WORK", "BRAINSTORM")]))
    r = subprocess.run([sys.executable, str(EVALS / "bank.py"), "init", "--project", str(p), "--key", str(key),
                        "--adjudications", str(cases), str(verds), "--dry-run"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["tasks"] == 1 and out["must_create"] == 1 and out["must_not_create"] == 1 and out["problems"] == []
    assert (p / "reports" / "evals" / "bank" / "review-v1" / "index.html").exists()
    assert not (p / "evals" / "bank").exists()


def test_conflict_drops_only_the_clashing_spans(tmp_path):
    p = project(tmp_path)
    tasks, problems = B.init(p, [krow("k-001", S1), krow("k-002", S3)],
                             [([case(1, S1[:40]), case(2, S2)], [verdict(1, "NOT_WORK"), verdict(2, "NOT_WORK")])])
    assert ids(tasks) == ["k-002"] and [e["span"] for e in tasks[0]["must_not_create"]] == [span(S2)]
    assert tasks[0]["must_not_create"][0]["id"] == f"{TASK}-n01"
    assert any("T1" in x and "k-001" in x for x in problems)


def test_two_must_nots_on_one_must_create_are_both_conflicts_in_either_order(tmp_path):
    p = project(tmp_path)
    res = []
    for order in (1, -1):
        cases = [case(1, S1[:40]), case(2, S1[30:80])][::order]
        verds = [verdict(1, "NOT_WORK"), verdict(2, "NOT_WORK")]
        tasks, problems = B.init(p, [krow("k-001", S1), krow("k-002", S3)], [(cases, verds)])
        res.append((ids(tasks), ids(tasks, "must_not_create"), sorted(problems)))
    assert res[0] == res[1] and res[0][0] == ["k-002"] and res[0][1] == []
    assert sum("T1" in x for x in res[0][2]) == 1 and sum("T2" in x for x in res[0][2]) == 1


def test_identical_must_not_span_is_silent_only_when_why_matches(tmp_path):
    p = project(tmp_path)
    same, _ = B.init(p, [krow("k-001", S1)], [([case(1, S2), case(2, S2)],
                                               [verdict(1, "NOT_WORK", "BRAINSTORM"), verdict(2, "NOT_WORK", "BRAINSTORM")])])
    assert len(same[0]["must_not_create"]) == 1 and _ == []
    diff, problems = B.init(p, [krow("k-001", S1)], [([case(1, S2), case(2, S2)],
                                                      [verdict(1, "NOT_WORK", "BRAINSTORM"), verdict(2, "NOT_WORK", "PEOPLE")])])
    assert diff[0]["must_not_create"][0]["why"] == "brainstorm"
    assert any("brainstorm" in x and "people" in x for x in problems)


def test_task_with_no_locatable_key_quote_is_listed_but_still_written(tmp_path):
    p = project(tmp_path)
    tasks, problems = B.init(p, [krow("k-009", "never said anywhere")], [([case(1, S2)], [verdict(1, "NOT_WORK")])])
    assert len(tasks) == 1 and len(tasks[0]["must_not_create"]) == 1
    assert f"task {TASK}: no locatable key quote" in problems


def test_dry_run_prints_per_task_ids(tmp_path):
    p = project(tmp_path / "proj")
    key = tmp_path / "k.jsonl"
    key.write_text(json.dumps(krow("k-001", S1)) + "\n")
    c, v = tmp_path / "c.json", tmp_path / "v.json"
    c.write_text(json.dumps([case(1, S2)]))
    v.write_text(json.dumps([verdict(1, "NOT_WORK")]))
    r = subprocess.run([sys.executable, str(EVALS / "bank.py"), "init", "--project", str(p), "--key", str(key),
                        "--adjudications", str(c), str(v), "--dry-run"], capture_output=True, text=True)
    assert json.loads(r.stdout)["per_task"] == [{"task": TASK, "must_create": ["k-001"], "must_not_create": [f"{TASK}-n01"]}]


def test_conflicts_do_not_depend_on_case_order_and_every_not_work_span_is_accounted_for(tmp_path):
    p = project(tmp_path)
    a, b = S1[:30], S1[-30:]
    cases = [case(1, a), case(2, b), case(3, S2)]
    verdicts = [verdict(n, "NOT_WORK", "BRAINSTORM") for n in (1, 2, 3)]
    outs = []
    for order in (slice(None), slice(None, None, -1)):
        tasks, problems = B.init(p, [krow("k-001", S1), krow("k-002", S3)], [(cases[order], verdicts)])
        assert "k-001" not in ids(tasks) and "k-002" in ids(tasks)
        assert [m["quote"] for m in tasks[0]["must_not_create"]] == [S2]
        conf = [x for x in problems if "k-001" in x]
        assert len(conf) == 2 and any("T1" in x for x in conf) and any("T2" in x for x in conf)
        for x in conf:
            assert S1 in x and str(span(S1)[0]) in x
        outs.append(sorted(conf))
    assert len(outs) == 2


def test_the_review_page_has_a_conflicts_section_with_both_quotes_and_spans(tmp_path):
    p = project(tmp_path)
    half = S1[:40]
    tasks, problems = B.init(p, [krow("k-001", S1)], [([case(1, half)], [verdict(1, "NOT_WORK", "BRAINSTORM")])])
    page = B.review_page(tasks, problems)
    assert "Conflicts (1)" in page and S1 in page and half in page
    assert f"{span(S1)[0]}-{span(S1)[1]}" in page and f"{span(half)[0]}-{span(half)[1]}" in page


def test_the_same_not_work_span_in_two_runs_is_written_once(tmp_path):
    p = project(tmp_path)
    c2 = case(2, S2) | {"case": "harbor|def|T9"}
    v2 = verdict(2, "NOT_WORK", "BRAINSTORM") | {"case": "harbor|def|T9"}
    tasks, problems = B.init(p, [krow("k-001", S1)],
                             [([case(1, S2)], [verdict(1, "NOT_WORK", "BRAINSTORM")]), ([c2], [v2])])
    assert len(tasks[0]["must_not_create"]) == 1 and problems == []


def test_a_not_work_key_row_is_a_problem_and_never_a_must_create(tmp_path):
    p = project(tmp_path)
    tasks, problems = B.init(p, [krow("k-001", S1), krow("k-002", S3, commitment="not_work")], [])
    assert ids(tasks) == ["k-001"]
    assert "k-002: commitment not_work, not a must_create" in problems
    tasks, _ = B.init(p, [krow("k-001", S1, commitment="proposed"), krow("k-002", S3, commitment="declined")], [])
    assert {m["id"]: m["commitment"] for m in tasks[0]["must_create"]} == {"k-001": "proposed", "k-002": "declined"}
