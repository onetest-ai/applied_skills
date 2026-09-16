import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "skills" / "knowledge-index"))
import fact_schema as F  # noqa: E402


def test_normalize_folds_case_ws_stopwords():
    assert F.normalize("  The  Go-Live   DATE ") == "go-live date"


def test_canon_key_applies_alias():
    aliases = {"deployment|date": "go-live|date"}
    assert F.canon_key("Deployment", "date", aliases) == ("go-live", "date")


def test_canon_key_without_alias_is_normalized():
    assert F.canon_key("Go-Live", "Date", {}) == ("go-live", "date")


def test_assertion_id_is_stable_and_value_sensitive():
    a = F.assertion_id("go-live", "date", "Q2", "docs/a.pdf", "chunk:1")
    b = F.assertion_id("go-live", "date", "Q2", "docs/a.pdf", "chunk:1")
    c = F.assertion_id("go-live", "date", "Q3", "docs/a.pdf", "chunk:1")
    assert a == b and a != c and len(a) == 16


def test_build_ledger_attaches_links():
    assertions = [{"assertion_id": "n1"}, {"assertion_id": "o1"}]
    led = F.build_ledger(assertions, [("n1", "supersedes", "o1")])
    assert led["schema_version"] == "1.0"
    n1 = [a for a in led["assertions"] if a["assertion_id"] == "n1"][0]
    assert n1["supersedes"] == ["o1"]
