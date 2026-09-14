from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import openpyxl


SKILL_DIR = Path(__file__).resolve().parents[1]


def _load(name, filename):
    spec = spec_from_file_location(name, SKILL_DIR / filename)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


parse_corpus = _load("parse_corpus_xlsx", "parse_corpus.py")
sys.path.insert(0, str(SKILL_DIR))
chunking = _load("chunking_xlsx", "chunking.py")


def _workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Owners"
    ws.append(["Service", "DevSecOps Manager", "Comment"])
    ws.append(["First service", "Someone Else", "first row"])
    ws.append(["Acme Service", "Sample Person", "Confirmed by reviewer.\nQ3 owner"])
    wb.save(path)


def test_xlsx_rows_are_chunk_content_not_truncated_headings(tmp_path):
    source = tmp_path / "owners.xlsx"
    _workbook(source)

    markdown, method = parse_corpus.parse_one(
        str(source), xlsx_max_mb=20, sample_rows=1
    )
    chunks = chunking.sections(markdown, max_chars=1600)

    assert method == "openpyxl-structure"
    assert "dims=A1:C3" in markdown
    # A small workbook is read completely even when the large-file sample is 1.
    assert "Sample Person" in markdown
    assert "Confirmed by reviewer. Q3 owner" in markdown
    person_chunks = [(title, body) for title, body in chunks if "Sample Person" in body]
    assert person_chunks
    assert "DevSecOps Manager: Sample Person" in person_chunks[0][1]
    assert "Service: Acme Service" in person_chunks[0][1]
    assert all(len(title) <= 70 for title, _ in chunks)
    assert all(body for _, body in chunks)


def test_xlsx_explicit_sampling_keeps_rows_separate(tmp_path):
    source = tmp_path / "owners.xlsx"
    _workbook(source)

    markdown = parse_corpus.parse_xlsx_structure(str(source), sample_rows=2)

    assert "First service" in markdown
    assert "Sample Person" not in markdown
    assert "Columns: Service; DevSecOps Manager; Comment" in markdown
    assert "## sheet: Owners · row 2" in markdown
