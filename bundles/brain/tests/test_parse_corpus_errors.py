"""A file parse_corpus cannot read is reported in one line, never a traceback.

A corpus routinely holds a truncated download or a corrupt Office file; the run
must name it, record the reason in the manifest, and carry on — the traceback is
only for someone debugging with --verbose.
"""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import parse_corpus


def _fake_soffice(tmp_path: Path, body: str) -> str:
    exe = tmp_path / "fake-soffice"
    exe.write_text(f"#!{sys.executable}\nimport sys\n{body}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def _run(tmp_path, *extra):
    corpus = tmp_path / "c"; out = tmp_path / "parsed"
    parse_corpus.main(["--corpus", str(corpus), "--out", str(out), *extra])
    return {e["source"]: e for e in json.loads((out / "manifest.json").read_text())}


def test_unreadable_file_is_one_line_and_recorded(tmp_path, capsys):
    (tmp_path / "c").mkdir(); (tmp_path / "c" / "a.pdf").write_bytes(b"x")
    with patch.object(parse_corpus, "parse_one", side_effect=ValueError("boom")):
        man = _run(tmp_path, "--formats", "pdf")
    err = capsys.readouterr().err
    assert "[ERR] a.pdf: boom" in err
    assert "Traceback" not in err
    assert man["a.pdf"] == {"source": "a.pdf", "error": "boom"}


def test_verbose_restores_the_traceback(tmp_path, capsys):
    (tmp_path / "c").mkdir(); (tmp_path / "c" / "a.pdf").write_bytes(b"x")
    with patch.object(parse_corpus, "parse_one", side_effect=ValueError("boom")):
        _run(tmp_path, "--formats", "pdf", "--verbose")
    assert "Traceback" in capsys.readouterr().err


def test_failed_soffice_conversion_names_the_file_not_the_command(tmp_path):
    doc = tmp_path / "notes.docx"; doc.write_bytes(b"PK\x03\x04truncated")
    so = _fake_soffice(tmp_path, 'sys.stderr.write("Fontconfig warning: adding cachedir\\nError: source file could not be loaded\\n"); sys.exit(1)')
    with patch.object(parse_corpus, "_soffice", return_value=so):
        try:
            parse_corpus.parse_office_pymupdf(str(doc))
        except RuntimeError as e:
            msg = str(e)
        else:
            raise AssertionError("expected RuntimeError")
    assert "not a readable Office file (truncated download?)" in msg
    assert "source file could not be loaded" in msg
    assert "Fontconfig" not in msg
    assert "-env:UserInstallation" not in msg


def test_soffice_exiting_zero_without_a_pdf_is_the_same_clean_error(tmp_path):
    doc = tmp_path / "notes.docx"; doc.write_bytes(b"PK\x03\x04truncated")
    so = _fake_soffice(tmp_path, "sys.exit(0)")
    with patch.object(parse_corpus, "_soffice", return_value=so):
        try:
            parse_corpus.parse_office_pymupdf(str(doc))
        except RuntimeError as e:
            assert "not a readable Office file (truncated download?)" in str(e)
        else:
            raise AssertionError("expected RuntimeError")
