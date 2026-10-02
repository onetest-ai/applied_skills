import scope_rules as sr

# Policy supplied as data (as a consuming project would), never hardcoded in the module.
CFG = {"scope": {
    "provenance_cutoff": "2026-06",
    "exclude_patterns": [r"prior .*engagement", r"legacy ?suite", r"prj-001", r"team_demo",
                         r"vendor platform"],
    "keep_patterns": [r"monthly", r"call volume", r"survey", r"scorecard", r"region"],
    "junk_patterns": [r"tobedeleted", r"^book\.xlsx$", r"~\$", r"\bcopy\b"],
}}


def test_prior_engagement_folder_fails():
    r = sr.classify("Prior engagement 2021 - 2023__Team_Demo_03.14.2022.pptx.md",
                    "Prior engagement 2021 - 2023", "Team_Demo_03.14.2022.pptx", "2022-03-14", CFG)
    assert r["scope"] == "fail"


def test_prior_engagement_nested_below_the_root_fails():
    r = sr.classify("Client Docs__Prior engagement 2021 - 2023__Archive__Old plan.pdf.md",
                    "Client Docs/Prior engagement 2021 - 2023/Archive", "Old plan.pdf", "", CFG)
    assert r["scope"] == "fail"


def test_junk_fails():
    assert sr.classify("someone_tobedeleted.xlsx.md", "", "someone_tobedeleted.xlsx", "2026-08-01", CFG)["scope"] == "fail"
    assert sr.classify("Book.xlsx.md", "", "Book.xlsx", "2026-08-01", CFG)["scope"] == "fail"


def test_pre_cutoff_year_fails_even_without_folder():
    r = sr.classify("Kickoff Deck PRJ-001 Vendor Platform(1).pdf.md",
                    "", "Kickoff Deck PRJ-001 Vendor Platform(1).pdf", "2024-05-01", CFG)
    assert r["scope"] == "fail"


def test_operational_data_keeps_even_before_cutoff():
    r = sr.classify("Ops Reporting__Monthly Ops January 2026.xlsm.md", "Ops Reporting",
                    "Monthly Ops January 2026.xlsm", "2026-02-07", CFG)
    assert r["scope"] == "keep"


def test_unknown_recent_is_review():
    assert sr.classify("Glossary.loop.md", "", "Glossary.loop", "2026-08-01", CFG)["scope"] == "review"


def test_empty_policy_is_inert():
    # No config policy -> nothing fails; a generic brain with no policy is not brain-specific.
    empty = {"scope": {"exclude_patterns": [], "keep_patterns": [], "junk_patterns": [], "provenance_cutoff": ""}}
    assert sr.classify("Prior engagement__x.pptx.md", "Prior engagement", "x.pptx", "2024-01-01", empty)["scope"] == "review"
