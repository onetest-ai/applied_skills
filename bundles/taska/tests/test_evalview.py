"""evals/evalview.py: one contract (eval-live/1) and one page for any bundle's live eval; nothing in it knows taska.
Invented data only."""
import ast
import json
import sys

from conftest import TASKA_ROOT

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import evalview as V  # noqa: E402


def test_it_imports_nothing_from_a_bundle():
    tree = ast.parse((EVALS / "evalview.py").read_text())
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert names <= {"json", "math", "pathlib", "__future__"}, names


def test_the_page_carries_no_bundle_words_and_polls_without_reloading():
    page = V.PAGE.lower()
    for word in ("meeting", "taska", "key item", "decision"):
        assert word not in page, word
    assert "fetch('live.json'" in V.PAGE and "15000" in V.PAGE and "http-equiv" not in page
    for words in ("is it finding the work?", "where is it stuck?", "is the run healthy?", "what changed since the baseline"):
        assert words in page


def test_write_replaces_an_older_page_and_writes_live_json_atomically(tmp_path):
    (tmp_path / "index.html").write_text("old")
    V.write(tmp_path, {"schema": V.SCHEMA, "status": "running"})
    assert (tmp_path / "index.html").read_text() == V.PAGE
    assert json.loads((tmp_path / "live.json").read_text())["schema"] == "eval-live/1"
    assert not (tmp_path / "live.json.tmp").exists()


def test_compare_items_names_lost_and_gained_with_a_paired_p():
    items = {i: {"id": i, "title": f"t{i}", "quote": "q", "where": "u1", "stage": "read, never proposed"} for i in "abcd"}
    now = {"a": True, "b": False, "c": True, "d": False}
    base = {"a": True, "b": True, "c": False, "d": False}
    c = V.compare_items("B7", "Committed work found", now, base, items)
    assert [x["id"] for x in c["lost"]] == ["b"] and [x["id"] for x in c["gained"]] == ["c"]
    assert c["p"] == 1.0 and c["code"] == "B7"


def test_mcnemar_reference():
    assert abs(V.mcnemar_exact(2, 9) - 0.0654) < 0.0005 and V.mcnemar_exact(0, 0) == 1.0


def test_the_verdict_is_relative_and_says_when_the_baseline_is_a_best_of_n():
    worse = {"code": "B7", "name": "n", "lost": [{}] * 9, "gained": [], "p": V.mcnemar_exact(9, 0)}
    same = {"code": "B13", "name": "n", "lost": [{}], "gained": [{}], "p": 1.0}
    v = V.verdict([worse, same], "r1", 3)
    assert v["by_metric"] == {"B7": "worse beyond noise", "B13": "within noise"}
    assert "no pass bar is adopted" in v["note"] and "best of 3 runs" in v["note"]
    assert "best of 1 run " in V.verdict([same], "r1", 1)["note"]
    assert "no baseline" in V.verdict([], None, None)["note"]


def test_the_quality_page_is_generic_and_shares_the_live_pages_style(tmp_path):
    page = V.QUALITY_PAGE
    for word in ("meeting", "taska", "echo", "key item"):
        assert word not in page.lower(), word
    assert V.STYLE in page and V.STYLE in V.PAGE   # one style for both pages
    assert "fetch('quality.json'" in page
    assert "independent_coverage" in page and "fields_scope" in page   # coverage beside agreement; scope of the field totals
    assert "x.flag" in page and "judge_tiers" in page and "judge_agreement_overall.n" in page   # "not validated", both tiers, per source
    V.write_quality(tmp_path, {"schema": "eval-quality/1"})
    assert (tmp_path / "index.html").read_text() == page
    assert json.loads((tmp_path / "quality.json").read_text())["schema"] == "eval-quality/1"


def test_the_live_page_shows_caveats_with_their_links_under_the_tiles():
    assert "id=caveats" in V.PAGE and "d.caveats" in V.PAGE and "caveats [{text, href}]" in V.__doc__


def test_the_quality_page_labels_every_card_and_saves_a_labels_file():
    page = V.QUALITY_PAGE
    for words in ("'Keep'", "'Drop'", "'Modify'", "'Add'", "'Not work'", "'Duplicate of'", ">Agree<", ">Disagree<",
                  "Show the judge's answer", ">Save<", "answered before"):
        assert words in page, words
    for reason in ("vague", "client_own", "status", "brainstorm", "people", "other"):
        assert f"'{reason}'" in page, reason
    assert "'eval-labels/1'" in page and "d.stamp" in page and "d.key_sha" in page and "seen_judge" in page
    assert "// LABELS-START" in page and "// LABELS-END" in page        # the pure Save code a test runs
    assert "localStorage" in page and page.count("try {") >= 2       # the draft is a convenience: the page works without it
    assert "yours" in page and "yours_unmatched" in page


def test_the_quality_page_hides_the_judges_totals_behind_one_control():
    page = V.QUALITY_PAGE
    assert "Show the judge's totals" in page and "totalsHidden" in page and "id=totals" in page


def test_the_quality_page_shows_judged_precision_with_its_burden_or_its_flag():
    page = V.QUALITY_PAGE
    assert "s.precision" in page and "lower bound" in page and "to delete per" in page
    assert "'Precision (judged)'" in page   # behind the judge's totals control like the other judged rows


def test_the_quality_page_labels_the_story_that_holds_found_work():
    assert "u.containers" in V.QUALITY_PAGE and "Holds found work" in V.QUALITY_PAGE


def test_the_quality_page_reads_the_burden_from_the_doc_when_it_carries_it():
    assert "/^per_.+_to_delete$/" in V.QUALITY_PAGE
