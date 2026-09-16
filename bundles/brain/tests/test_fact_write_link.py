import sqlite3, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "skills" / "knowledge-index"))
import fact_write as W  # noqa: E402
import temporal_memory as T  # noqa: E402


def _con_with_prior(value="Q2", at="2026-01-01T00:00:00Z"):
    con = sqlite3.connect(":memory:")
    T.load_ledger(con, {"schema_version": "1.0", "assertions": [{
        "assertion_id": "old", "entity": "go-live", "predicate": "date", "value": value,
        "asserted_at": at, "ingested_at": at, "source": "docs/a.pdf", "segment_id": "chunk:2"}]})
    return con


def test_find_priors_returns_matching():
    con = _con_with_prior()
    priors = W.find_priors(con, "go-live", "date")
    assert priors[0]["assertion_id"] == "old" and priors[0]["value"] == "Q2"


def test_later_different_value_auto_supersedes():
    con = _con_with_prior()
    priors = W.find_priors(con, "go-live", "date")
    auto, dis = W.plan_links("new", "Q3", "2026-09-14T00:00:00Z", priors)
    assert auto == [("new", "supersedes", "old")] and dis == []


def test_same_value_no_link():
    con = _con_with_prior(value="Q2")
    priors = W.find_priors(con, "go-live", "date")
    auto, dis = W.plan_links("new", "Q2", "2026-09-14T00:00:00Z", priors)
    assert auto == [] and dis == []


def test_same_time_conflict_is_disagreement():
    con = _con_with_prior(at="2026-09-14T00:00:00Z")
    priors = W.find_priors(con, "go-live", "date")
    auto, dis = W.plan_links("new", "Q3", "2026-09-14T00:00:00Z", priors)
    assert auto == [] and dis and dis[0]["prior_id"] == "old"


def test_older_than_prior_no_link():
    con = _con_with_prior(at="2026-09-14T00:00:00Z")
    priors = W.find_priors(con, "go-live", "date")
    auto, dis = W.plan_links("new", "Q1", "2026-01-01T00:00:00Z", priors)
    assert auto == [] and dis == []
