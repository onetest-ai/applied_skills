"""+flag follow-up to finding D-1: when taska can only *guess* a meeting's date from the file's
mtime — no embedded creation_time, no date in the file name, no Teams .docx header — the evidence
item must say so (date_source="file_mtime"), so staleness/prep can treat that date as uncertain
instead of trusting it silently. A date read from the file name is marked "filename".

These assert the evidence item carries that provenance; they FAIL on code that doesn't stamp it.
All names are invented (the Harbor Logistics world from conftest.py) — never client-identifying,
this is a public repo.
"""
import json
import textwrap

from conftest import VTT_DAY1, run_cli


def _toml(calls):
    return textwrap.dedent(f"""
        [project]
        name = "Harbor Logistics"
        [plan]
        epics = ["Onboarding"]
        [[people]]
        name = "Nora Quill"
        side = "internal"
        [sources.daily]
        adapter = "meetings_folder"
        path = "{calls}"
    """)


def _only_item(project):
    evs = sorted((project / ".taska" / "evidence").glob("*.json"))
    assert len(evs) == 1, [p.name for p in evs]
    return json.loads(evs[0].read_text())


def test_mtime_guessed_date_is_flagged_as_file_mtime(tmp_path):
    calls = tmp_path / "calls"
    calls.mkdir()
    (calls / "Logistics weekly catchup.vtt").write_text(VTT_DAY1)  # no date anywhere in the name
    (tmp_path / "taska.toml").write_text(_toml(calls))
    run_cli(tmp_path, "setup")
    run_cli(tmp_path, "sync")

    item = _only_item(tmp_path)
    assert item.get("date_source") == "file_mtime", \
        f"a date guessed from the file mtime was not flagged uncertain: date_source={item.get('date_source')!r}"


def test_filename_dated_meeting_is_flagged_from_filename(tmp_path):
    calls = tmp_path / "calls"
    calls.mkdir()
    (calls / "12 May Logistics weekly catchup.vtt").write_text(VTT_DAY1)  # date lives in the name
    (tmp_path / "taska.toml").write_text(_toml(calls))
    run_cli(tmp_path, "setup")
    run_cli(tmp_path, "sync")

    item = _only_item(tmp_path)
    assert item.get("date_source") == "filename", \
        f"a date read from the file name was not recorded as such: date_source={item.get('date_source')!r}"
