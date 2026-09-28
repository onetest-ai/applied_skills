import importlib.util
import json
import sqlite3
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
VTT = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\nHello there.\n"


def build(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    corpus, parsed, db = tmp_path / "docs", tmp_path / "parsed", tmp_path / "k.sqlite"
    corpus.mkdir()
    (corpus / "a b.vtt").write_text(VTT)
    (corpus / "a_b.vtt").write_text(VTT)
    con = R.connect(str(db))
    R.ensure_schema(con)
    R.register(con, "docs", "a b.vtt", corpus / "a b.vtt")
    con.close()
    P.main(["--corpus", str(corpus), "--out", str(parsed), "--formats", "vtt", "--registry-db", str(db), "--root-key", "docs"])
    return sqlite3.connect(db), parsed


def test_strict_sources_accept_registry_filtered_parse(tmp_path: Path):
    con, parsed = build(tmp_path)
    links, unmanaged = S.source_ids(con, str(parsed), None, "docs", strict=True)
    assert list(links) == ["a b.vtt.md"] and unmanaged == []


def test_strict_sources_refuse_an_unregistered_parsed_doc(tmp_path: Path):
    con, parsed = build(tmp_path)
    (parsed / "a_b.vtt.md").write_text("# SOURCE: a_b.vtt\n# method: vtt\n\ntext")
    man = json.loads((parsed / "manifest.json").read_text())
    man.append({"source": "a_b.vtt", "md": "a_b.vtt.md", "method": "vtt"})
    (parsed / "manifest.json").write_text(json.dumps(man))
    with pytest.raises(ValueError, match="unmanaged parsed documents"):
        S.source_ids(con, str(parsed), None, "docs", strict=True)
