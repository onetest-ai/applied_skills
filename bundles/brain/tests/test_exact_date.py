import pytest
import parse_corpus as P


@pytest.mark.parametrize("name,ext,expected", [
    ("Deck 08.18.26.pdf", ".pdf", ("2026-08-18", "filename")),
    ("Review_4.23.24.pptx", ".pptx", ("2024-04-23", "filename")),
    ("Session-20260914_162508UTC-Meeting Recording.vtt", ".vtt", ("2026-09-14", "filename")),
    ("GMT20240717-190217_Recording.vtt", ".vtt", ("2024-07-17", "filename")),
    ("260406 - SteerCo.pptx", ".pptx", ("2026-04-06", "filename")),
    ("Proposal.v2.08.24.2026.pdf", ".pdf", ("2026-08-24", "filename")),
    ("Sep 24 Daily sync.vtt", ".vtt", (None, "none")),          # no year → not exact
    ("Report_June'24_Release.pptx", ".pptx", (None, "none")),   # month+year only
    ("Plan Aug 2026.docx", ".docx", (None, "none")),
    ("Deck 13.45.26.pdf", ".pdf", (None, "none")),              # invalid month
])
def test_filename_dates_are_exact_or_none(name, ext, expected):
    assert P.exact_date("", name, ext) == expected


def test_text_date_is_first_by_position_not_earliest():
    text = "Kickoff\nFebruary 27, 2025\n...as of December 31, 2023 (source)"
    assert P.exact_date(text, "deck.pdf", ".pdf") == ("2025-02-27", "text")


def test_text_date_wins_over_filename():
    assert P.exact_date("Title 10/24/2023", "deck 08.18.26.pdf", ".pdf") == ("2023-10-24", "text")


def test_transcript_speech_dates_are_ignored():
    text = "## 00:01\n\nWe met on October 24, 2023 for the kickoff."
    assert P.exact_date(text, "Sep 24 sync.vtt", ".vtt") == (None, "none")


def test_spreadsheet_cell_dates_are_ignored():
    assert P.exact_date("| 2023-01-01 | 120 |", "Staffing.xlsx", ".xlsx") == (None, "none")


def test_text_date_on_parse_day_is_ignored():
    text = "CONFIDENTIAL | September 28, 2026\nSolution design"
    assert P.exact_date(text, "Design_v3.docx", ".docx", today="2026-09-28") == (None, "none")


def test_first_text_date_on_or_after_parse_day_makes_text_undated():
    text = "Printed December 1, 2026\nIssued 3/14/2024"
    assert P.exact_date(text, "Design.docx", ".docx", today="2026-09-28") == (None, "none")


def test_as_of_citation_is_not_used_when_first_date_is_today():
    text = "CONFIDENTIAL | September 28, 2026\nSolution design, as of December 31, 2023 (source)"
    assert P.exact_date(text, "Design_v3.docx", ".docx", today="2026-09-28") == (None, "none")


def test_auto_date_falls_back_to_filename():
    text = "CONFIDENTIAL | September 28, 2026"
    assert P.exact_date(text, "Plan 4.23.24.docx", ".docx", today="2026-09-28") == ("2024-04-23", "filename")


def test_date_before_parse_day_is_kept():
    assert P.exact_date("Kickoff 10/24/2023", "k.pdf", ".pdf", today="2026-09-28") == ("2023-10-24", "text")


@pytest.mark.parametrize("name,ext", [
    ("Release Notes v3.10.25.pdf", ".pdf"),
    ("App 2.1.22 guide.docx", ".docx"),
    ("Spec 1.2.2024.pdf", ".pdf"),
])
def test_version_numbers_are_not_read_as_filename_dates(name, ext):
    assert P.exact_date("", name, ext) == (None, "none")


def test_version_word_in_content_is_not_read_as_a_date():
    text = "Version 1.10.2024 of the API"
    assert P.exact_date(text, "notes.pdf", ".pdf") == (None, "none")


def test_dotted_dates_still_match_when_not_version_like():
    assert P.exact_date("", "Review_4.23.24.pptx", ".pptx") == ("2024-04-23", "filename")
    assert P.exact_date("", "Deck 08.18.26.pdf", ".pdf") == ("2026-08-18", "filename")
    # preceded by "v2." not the bare version word "v" -> not rejected merely for a
    # preceding digit-dot token
    assert P.exact_date("", "Proposal.v2.08.24.2026.pdf", ".pdf") == ("2026-08-24", "filename")
    assert P.exact_date("", "Team_Review_7.17.2024.pptx", ".pptx") == ("2024-07-17", "filename")


def test_impossible_calendar_date_in_filename_is_rejected():
    assert P.exact_date("", "Report 2024-02-31.pdf", ".pdf") == (None, "none")


def test_manifest_records_date_and_parsed_bytes_unchanged(tmp_path):
    c = tmp_path / "c"; c.mkdir()
    (c / "Notes 08.18.26.md").write_text("# Notes\n\nFebruary 27, 2025 update.\n")
    P.main(["--corpus", str(c), "--out", str(tmp_path / "p"), "--formats", "md"])
    import json
    man = json.loads((tmp_path / "p" / "manifest.json").read_text())
    assert man[0]["event_date"] == "2025-02-27" and man[0]["date_source"] == "text"
    assert "event_date" not in (tmp_path / "p" / "Notes 08.18.26.md.md").read_text()
