"""`vision_assemble --source` writes parse_corpus's preamble (so downstream agents see the
real source, not a slug); without it the output is byte-identical to before, and the
preamble never reaches a chunk (chunking.strip_preamble removes it exactly)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import chunking

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "visual-parse" / "vision_assemble.py"


def _render_dir():
    d = Path(tempfile.mkdtemp())
    (d / "pages.json").write_text(json.dumps({"doc": "deck.pdf", "slug": "decks__deck", "dpi": 96, "pages": [
        {"page": 1, "image": "decks__deck/p01.png", "flagged": False, "img_sha": "a"}]}))
    (d / "p01.txt").write_text("Quarterly results\nRevenue grew.")
    return d


def _run(rd, *extra):
    out = Path(tempfile.mkdtemp()) / "deck.md"
    r = subprocess.run([sys.executable, str(SCRIPT), "--render-dir", str(rd), "--out", str(out), *extra],
                       text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return out.read_text()


def test_without_source_output_has_no_preamble():
    assert _run(_render_dir()).startswith("## p01 · ")


def test_source_writes_parse_corpus_preamble_and_chunks_are_unchanged():
    rd = _render_dir()
    plain, headed = _run(rd), _run(rd, "--source", "decks/deck.pdf")
    assert headed == "# SOURCE: decks/deck.pdf\n# method: visual-parse\n# fidelity: full\n\n" + plain
    assert chunking.strip_preamble(headed) == plain
    assert chunking.section_records(headed) == chunking.section_records(plain)
