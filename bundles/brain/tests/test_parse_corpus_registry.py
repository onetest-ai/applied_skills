import json
import sqlite3
from pathlib import Path

import pytest

import parse_corpus as P

VTT = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\nHello there.\n"


def registry(db: Path, rels: list[str]) -> None:
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE sources(root_key TEXT, relative_path TEXT, state TEXT)")
    con.executemany("INSERT INTO sources VALUES('docs',?,'active')", [(r,) for r in rels])
    con.commit()
    con.close()


def corpus(tmp_path: Path) -> Path:
    c = tmp_path / "c"
    c.mkdir()
    (c / "a b.vtt").write_text(VTT)
    (c / "a_b.vtt").write_text(VTT)
    return c


def run(c: Path, out: Path, *extra: str) -> None:
    P.main(["--corpus", str(c), "--out", str(out), "--formats", "vtt", *extra])


def test_only_registered_files_are_parsed(tmp_path: Path):
    c, out, db = corpus(tmp_path), tmp_path / "p", tmp_path / "k.sqlite"
    registry(db, ["a b.vtt"])
    run(c, out, "--registry-db", str(db), "--root-key", "docs")
    assert sorted(p.name for p in out.glob("*.md")) == ["a b.vtt.md"]
    man = {m["source"]: m for m in json.loads((out / "manifest.json").read_text())}
    assert man["a_b.vtt"]["method"] == "unregistered" and man["a_b.vtt"]["skipped"] is True


def test_stale_output_of_an_unregistered_file_is_removed(tmp_path: Path):
    c, out, db = corpus(tmp_path), tmp_path / "p", tmp_path / "k.sqlite"
    run(c, out)
    assert (out / "a_b.vtt.md").exists()
    registry(db, ["a b.vtt"])
    run(c, out, "--registry-db", str(db), "--root-key", "docs")
    assert not (out / "a_b.vtt.md").exists()


def test_without_flags_everything_is_parsed(tmp_path: Path):
    c, out = corpus(tmp_path), tmp_path / "p"
    run(c, out)
    assert sorted(p.name for p in out.glob("*.md")) == ["a b.vtt.md", "a_b.vtt.md"]


def test_flags_must_come_together(tmp_path: Path):
    with pytest.raises(SystemExit):
        run(corpus(tmp_path), tmp_path / "p", "--registry-db", "x")


def test_root_with_no_active_sources_parses_nothing(tmp_path: Path):
    c, out, db = corpus(tmp_path), tmp_path / "p", tmp_path / "k.sqlite"
    registry(db, [])
    run(c, out, "--registry-db", str(db), "--root-key", "docs")
    assert list(out.glob("*.md")) == []
