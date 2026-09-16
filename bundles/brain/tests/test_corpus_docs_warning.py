"""corpus_docs must warn on stderr when binary source files are present."""
import sys
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import knowledge_index as K


def test_corpus_docs_warns_on_pdf_files(tmp_path, capsys):
    # Create one indexed and one non-indexed file
    (tmp_path / "doc.md").write_text("# Hello\n\nWorld.")
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4 fake")

    result = K.corpus_docs(str(tmp_path))

    captured = capsys.readouterr()
    assert any(str(tmp_path / "doc.md") in r or "doc.md" in r for r in result), \
        "corpus_docs must include .md files"
    assert "pdf" in captured.err.lower() or "report.pdf" in captured.err, \
        "corpus_docs must warn about skipped .pdf files on stderr"


def test_corpus_docs_warns_on_xlsx_and_pptx(tmp_path, capsys):
    (tmp_path / "data.xlsx").write_bytes(b"PK fake xlsx")
    (tmp_path / "slides.pptx").write_bytes(b"PK fake pptx")

    K.corpus_docs(str(tmp_path))

    captured = capsys.readouterr()
    assert "xlsx" in captured.err.lower() or "pptx" in captured.err.lower(), \
        "corpus_docs must warn about skipped .xlsx and .pptx files on stderr"


def test_corpus_docs_no_warning_when_clean(tmp_path, capsys):
    (tmp_path / "doc.md").write_text("# Title\n\nContent.")
    K.corpus_docs(str(tmp_path))
    captured = capsys.readouterr()
    assert captured.err == "", "corpus_docs must not warn when only indexed types are present"
