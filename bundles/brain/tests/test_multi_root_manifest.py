"""Two source roots feeding one Brain (the v6 finding): parsing a second root must not
drop the first root's manifest entries, and brain_sync must be able to link and strictly
seed a multi-root corpus — either one shared parsed/ dir or one subdir per root."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

import parse_corpus as P

HERE = Path(__file__).resolve().parent.parent / "skills" / "knowledge-pipeline"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


R, S = load("source_registry"), load("brain_sync")


def two_roots(tmp_path: Path):
    docs, notes, db = tmp_path / "docs", tmp_path / "notes", tmp_path / "k.sqlite"
    docs.mkdir(); notes.mkdir()
    (docs / "plan.md").write_text("# Plan\nalpha")
    (notes / "memo.md").write_text("# Memo\nbeta")
    con = R.connect(str(db))
    R.ensure_schema(con)
    R.register(con, "docs", "plan.md", docs / "plan.md")
    R.register(con, "notes", "memo.md", notes / "memo.md")
    con.close()
    return docs, notes, db


def parse(corpus, out, db, key):
    P.main(["--corpus", str(corpus), "--out", str(out), "--formats", "md",
            "--registry-db", str(db), "--root-key", key])


def manifest(out: Path):
    return {e["source"]: e for e in json.loads((out / "manifest.json").read_text())}


def test_second_root_keeps_first_roots_entries(tmp_path):
    docs, notes, db = two_roots(tmp_path)
    out = tmp_path / "parsed"
    parse(docs, out, db, "docs")
    parse(notes, out, db, "notes")
    man = manifest(out)
    assert sorted(man) == ["memo.md", "plan.md"]
    assert man["plan.md"]["root_key"] == "docs" and man["memo.md"]["root_key"] == "notes"


def test_rerun_of_one_root_still_rederives_only_its_own_entries(tmp_path):
    docs, notes, db = two_roots(tmp_path)
    out = tmp_path / "parsed"
    parse(docs, out, db, "docs")
    parse(notes, out, db, "notes")
    (notes / "memo.md").unlink()
    parse(notes, out, db, "notes")
    assert sorted(manifest(out)) == ["plan.md"]


def test_legacy_unstamped_entries_are_rederived_by_a_keyed_run(tmp_path):
    # An existing project's manifest predates the root_key stamp; a keyed re-run of the
    # same root must replace those entries, not keep a duplicate beside the fresh one.
    docs, _notes, db = two_roots(tmp_path)
    out = tmp_path / "parsed"
    P.main(["--corpus", str(docs), "--out", str(out), "--formats", "md"])
    parse(docs, out, db, "docs")
    entries = json.loads((out / "manifest.json").read_text())
    assert [e["source"] for e in entries] == ["plan.md"] and entries[0]["root_key"] == "docs"


def test_unkeyed_run_writes_no_root_key(tmp_path):
    docs, _notes, _db = two_roots(tmp_path)
    out = tmp_path / "parsed"
    P.main(["--corpus", str(docs), "--out", str(out), "--formats", "md"])
    assert "root_key" not in manifest(out)["plan.md"]


def test_shared_dir_strict_seed_links_both_roots(tmp_path):
    docs, notes, db = two_roots(tmp_path)
    out = tmp_path / "parsed"
    parse(docs, out, db, "docs")
    parse(notes, out, db, "notes")
    con = sqlite3.connect(db)
    # One --root-key: each entry's own stamp wins, so both roots resolve.
    links, unmanaged = S.source_ids(con, str(out), None, "docs", strict=True)
    assert sorted(links) == ["memo.md.md", "plan.md.md"] and unmanaged == []
    ids = dict(con.execute("SELECT relative_path, source_id FROM sources"))
    assert links["plan.md.md"] == ids["plan.md"] and links["memo.md.md"] == ids["memo.md"]


def test_subdir_per_root_strict_seed_with_paired_manifests(tmp_path):
    docs, notes, db = two_roots(tmp_path)
    out = tmp_path / "parsed"
    parse(docs, out / "docs", db, "docs")
    parse(notes, out / "notes", db, "notes")
    con = sqlite3.connect(db)
    links, unmanaged = S.source_ids(
        con, str(out), [str(out / "docs" / "manifest.json"), str(out / "notes" / "manifest.json")],
        ["docs", "notes"], strict=True)
    assert sorted(links) == ["docs/plan.md.md", "notes/memo.md.md"] and unmanaged == []


def test_subdir_manifest_without_stamp_uses_its_paired_key(tmp_path):
    # Manifests written before the stamp: the positional --root-key pairing supplies the key.
    docs, notes, db = two_roots(tmp_path)
    out = tmp_path / "parsed"
    P.main(["--corpus", str(docs), "--out", str(out / "docs"), "--formats", "md"])
    P.main(["--corpus", str(notes), "--out", str(out / "notes"), "--formats", "md"])
    con = sqlite3.connect(db)
    links, _ = S.source_ids(
        con, str(out), [str(out / "docs" / "manifest.json"), str(out / "notes" / "manifest.json")],
        ["docs", "notes"], strict=True)
    assert sorted(links) == ["docs/plan.md.md", "notes/memo.md.md"]


def test_mismatched_pairing_is_refused(tmp_path):
    docs, notes, db = two_roots(tmp_path)
    out = tmp_path / "parsed"
    parse(docs, out / "docs", db, "docs")
    parse(notes, out / "notes", db, "notes")
    with pytest.raises(ValueError, match="pair"):
        S.source_ids(sqlite3.connect(db), str(out),
                     [str(out / "docs" / "manifest.json"), str(out / "notes" / "manifest.json")],
                     ["docs", "notes", "extra"], strict=True)


def test_single_manifest_outside_parsed_keeps_legacy_md_resolution(tmp_path):
    # A manifest copy stored elsewhere (v6's seed/manifest_*.json) keeps md relative to --parsed.
    docs, _notes, db = two_roots(tmp_path)
    out = tmp_path / "parsed"
    parse(docs, out, db, "docs")
    copy = tmp_path / "seed" / "manifest_docs.json"
    copy.parent.mkdir()
    copy.write_text((out / "manifest.json").read_text())
    links, _ = S.source_ids(sqlite3.connect(db), str(out), str(copy), "docs", strict=True)
    assert list(links) == ["plan.md.md"]


def test_seed_cli_accepts_repeated_manifest_and_root_key(tmp_path, monkeypatch):
    docs, notes, db = two_roots(tmp_path)
    out = tmp_path / "parsed"
    parse(docs, out / "docs", db, "docs")
    parse(notes, out / "notes", db, "notes")
    monkeypatch.setattr(sys, "argv", [
        "brain_sync.py", "seed", "--db", str(db), "--parsed", str(out),
        "--manifest", str(out / "docs" / "manifest.json"), "--root-key", "docs",
        "--manifest", str(out / "notes" / "manifest.json"), "--root-key", "notes",
        "--strict-sources"])
    S.main()
    rows = dict(sqlite3.connect(db).execute("SELECT doc_id, source_id FROM synced_files"))
    assert set(rows) == {"docs/plan.md.md", "notes/memo.md.md"} and all(rows.values())
