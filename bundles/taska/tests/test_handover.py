"""A project is handed over, never rebuilt (the manager, Oct 2026: keep all existing work, never remove data). taska helps:
`doctor` warns about paths tied to one person's home folder and about sources that would read again, and `handover`
packs taska.toml + the store into one zip with a note, changing nothing. Invented data only (public repo)."""
import hashlib
import os
import zipfile
from pathlib import Path

from conftest import run_cli
from taska_lib import store as S


def _digest(d):
    h = hashlib.sha256()
    for p in sorted(Path(d).rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(d)).encode()); h.update(p.read_bytes())
    return h.hexdigest()


def test_doctor_warns_about_a_path_in_one_persons_home(project):
    home = os.path.expanduser("~")
    toml = project / "taska.toml"
    toml.write_text(toml.read_text() + f'\n[sources.deliverables]\nadapter = "files_folder"\npath = "{home}/Documents/x"\n')
    r = run_cli(project, "doctor")
    assert any(home in w and "~/" in w for w in r["portability"])


def test_doctor_flags_a_source_whose_items_have_no_cursor(project):
    run_cli(project, "sync")
    st = project / ".taska"
    c = S.cursors(st); c.pop("daily", None); S.save_cursors(st, c)
    r = run_cli(project, "doctor")
    assert r["handover_check"]["daily"]["would_reread"] is True


def test_handover_packs_the_project_with_a_note_and_changes_nothing(project, tmp_path_factory):
    run_cli(project, "setup")                                                # a real project has a ledger
    run_cli(project, "sync")
    before = _digest(project)
    out = tmp_path_factory.mktemp("handover")                              # outside the project folder
    r = run_cli(project, "handover", "--out", str(out))
    assert _digest(project) == before                                       # read-only: nothing moved or removed
    z = zipfile.ZipFile(r["zip"])
    names = z.namelist()
    assert "taska.toml" in names and any(n.startswith(".taska/events.jsonl") for n in names)
    assert any(n.startswith(".taska/evidence/") for n in names)
    note = Path(r["note"]).read_text()
    assert "evidence items" in note and "events" in note and "Harbor Logistics" in note
    assert r["counts"]["evidence"] >= 2
