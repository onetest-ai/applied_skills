"""Status-report ingestion — the document-date layer: a report lands on its own (header) date, so it
slots into the right week instead of the copy day. Invented data; public repo."""
import json
import textwrap

from taska_lib.sources.files_folder import report_date
from conftest import run_cli


def test_report_date_reads_unambiguous_formats():
    assert report_date("2026-06-03 Weekly status", today="2026-10-15") == "2026-06-03"
    assert report_date("Jun 3, 2026", today="2026-10-15") == "2026-06-03"
    assert report_date("3 June 2026 — status", today="2026-10-15") == "2026-06-03"


def test_report_date_numeric_requires_declared_format():
    assert report_date("03/06/26", today="2026-10-15") is None                              # ambiguous -> not guessed
    assert report_date("03/06/26", date_format="dmy", today="2026-10-15") == "2026-06-03"
    assert report_date("03/06/26", date_format="mdy", today="2026-10-15") == "2026-03-06"


def test_report_date_rejects_future_and_junk():
    assert report_date("2099-01-01", today="2026-10-15") is None                            # sync-clock error
    assert report_date("no date in this header", today="2026-10-15") is None


def test_file_evidence_uses_the_document_date(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "Weekly status.md").write_text("2026-06-03 Weekly status\n\nDone:\n- migrated the stock feed\n")
    (tmp_path / "taska.toml").write_text(textwrap.dedent(f"""
        [project]
        name = "Harbor Logistics"
        [plan]
        epics = ["E"]
        [[people]]
        name = "A"
        side = "internal"
        [sources.docs]
        adapter = "files_folder"
        path = "{docs}"
        include = ["*.md"]
    """))
    run_cli(tmp_path, "setup")
    run_cli(tmp_path, "sync")
    item = next(json.loads(p.read_text()) for p in (tmp_path / ".taska" / "evidence").glob("*.json")
                if json.loads(p.read_text()).get("kind") == "file")
    assert item["date"] == "2026-06-03" and item["date_source"] == "file_content", item
