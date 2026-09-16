import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "skills" / "knowledge-index"))
import fact_write as W  # noqa: E402


def test_judge_supersedes_becomes_link():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    links, unresolved = W.judge_disagreements(dis, lambda d: {"relation": "supersedes"})
    assert links == [("n", "supersedes", "o")] and unresolved == []


def test_judge_contradicts_becomes_link():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    links, _ = W.judge_disagreements(dis, lambda d: {"relation": "contradicts"})
    assert links == [("n", "contradicts", "o")]


def test_judge_keep_both_no_link():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    links, unresolved = W.judge_disagreements(dis, lambda d: {"relation": "keep_both"})
    assert links == [] and unresolved == []


def test_judge_error_is_unresolved():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    def boom(d): raise RuntimeError("llm down")
    links, unresolved = W.judge_disagreements(dis, boom)
    assert links == [] and unresolved == dis


def test_judge_unknown_relation_is_unresolved():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    links, unresolved = W.judge_disagreements(dis, lambda d: {"relation": "bogus"})
    assert links == [] and unresolved == dis


def test_judge_missing_relation_key_is_unresolved():
    dis = [{"new_id": "n", "prior_id": "o", "new_value": "Q3", "prior_value": "Q2"}]
    links, unresolved = W.judge_disagreements(dis, lambda d: {})
    assert links == [] and unresolved == dis
