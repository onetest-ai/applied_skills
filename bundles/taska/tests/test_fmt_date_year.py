"""A date from another year must say so: "nothing since Oct 6 (1105 days)" hides that the last word is from three
years earlier. The year appears only when it differs from the project's today. Invented data; public repo."""
from taska_lib.text import fmt_date
from taska_lib import staleness as ST


def test_fmt_date_adds_year_only_when_it_differs_from_today():
    assert fmt_date("2026-10-06", today="2026-10-15") == "Oct 6"
    assert fmt_date("2023-10-06", today="2026-10-15") == "Oct 6, 2023"
    assert fmt_date("2026-10-06") == "Oct 6"                                  # no today: unchanged


def test_gone_quiet_ask_shows_the_year_of_an_old_last_word():
    node = {"id": "T004", "kind": "task", "title": "Keep pages non-indexable", "blocks": [], "parent": "S001"}
    r = {"node": node, "flags": [], "since": "2023-10-06", "days": 1105,
         "anchor": {"speaker": "Dana Example", "date": "2023-10-06", "quote": "we need to check it"}}
    q = ST.question(r, {"T004": node})
    assert "nothing since Oct 6, 2023 (1105 days)" in q and "Dana Example, Oct 6, 2023" in q, q
