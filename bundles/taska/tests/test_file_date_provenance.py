"""File/deliverable evidence must record how its date was found, the way meetings do. A file carries
no embedded date, so taska dates it by the file's mtime — the sync/copy day. That is a guess (the
artifact's real date may be older), so it must be marked `date_source="file_mtime"`, not silently
trusted. Surfaced on a real document-deliverables corpus (PDFs dated by copy-day). Invented data; public repo.
"""
import json
import textwrap

from conftest import run_cli


def test_file_evidence_records_date_source(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "Weekly status report.md").write_text("# Weekly status\n\n- Done: migrated the stock feed\n- Todo: dashboard\n")
    (tmp_path / "taska.toml").write_text(textwrap.dedent(f"""
        [project]
        name = "Harbor Logistics"
        [plan]
        epics = ["Onboarding"]
        [[people]]
        name = "Nora Quill"
        side = "internal"
        [sources.docs]
        adapter = "files_folder"
        path = "{docs}"
        include = ["*.md"]
    """))
    run_cli(tmp_path, "setup")
    run_cli(tmp_path, "sync")

    files = [json.loads(p.read_text()) for p in (tmp_path / ".taska" / "evidence").glob("*.json")]
    files = [f for f in files if f.get("kind") == "file"]
    assert files, "no file evidence was produced"
    # A file's date is the sync/copy day (mtime) — it must be marked as such, like a meeting's +flag.
    assert all(f.get("date_source") == "file_mtime" for f in files), [f.get("date_source") for f in files]
