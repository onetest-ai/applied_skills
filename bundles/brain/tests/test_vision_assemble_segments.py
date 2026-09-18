"""Tiles are a rendering detail; a citation resolves to the logical segment."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "skills" / "visual-parse" / "vision_assemble.py"


def _render_dir(rows, texts, results=None):
    d = Path(tempfile.mkdtemp())
    (d / "pages.json").write_text(json.dumps(
        {"doc": "deck.html", "slug": "deck", "dpi": 96, "pages": rows}))
    for n, t in texts.items():
        (d / f"p{n:02d}.txt").write_text(t)
    rd = None
    if results:
        rd = Path(tempfile.mkdtemp())
        (rd / "result_1.json").write_text(json.dumps(results))
    return d, rd


def _run(render_dir, results_dir=None):
    out = Path(tempfile.mkdtemp()) / "deck.md"
    cmd = [sys.executable, str(SCRIPT), "--render-dir", str(render_dir), "--out", str(out)]
    if results_dir:
        cmd += ["--results", str(results_dir)]
    r = subprocess.run(cmd, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return out.read_text()


class SegmentMergingTests(unittest.TestCase):
    def test_tiles_of_one_segment_become_one_section(self):
        rows = [
            {"page": 1, "image": "p01.png", "img_sha": "a" * 64, "text_len": 9,
             "n_drawings": 0, "n_tables": 0, "img_cover": 1.0, "flagged": True,
             "why": "html-segment", "segment": 1, "tile": 1, "of": 2},
            {"page": 2, "image": "p02.png", "img_sha": "b" * 64, "text_len": 9,
             "n_drawings": 0, "n_tables": 0, "img_cover": 1.0, "flagged": True,
             "why": "html-segment", "segment": 1, "tile": 2, "of": 2},
        ]
        d, rd = _render_dir(rows, {1: "top half", 2: "bottom half"},
                            results={"a" * 64: "# Risks\nStaffing is short.",
                                     "b" * 64: "Attrition is rising."})
        md = _run(d, rd)
        self.assertEqual(md.count("## p01"), 1)
        self.assertNotIn("## p02", md, "a tile must not become its own section")
        self.assertIn("Staffing is short.", md)
        self.assertIn("Attrition is rising.", md)

    def test_tile_order_is_preserved(self):
        rows = [
            {"page": 1, "image": "p01.png", "img_sha": "a" * 64, "text_len": 1,
             "n_drawings": 0, "n_tables": 0, "img_cover": 1.0, "flagged": True,
             "why": "html-segment", "segment": 1, "tile": 1, "of": 2},
            {"page": 2, "image": "p02.png", "img_sha": "b" * 64, "text_len": 1,
             "n_drawings": 0, "n_tables": 0, "img_cover": 1.0, "flagged": True,
             "why": "html-segment", "segment": 1, "tile": 2, "of": 2},
        ]
        d, rd = _render_dir(rows, {1: "x", 2: "y"},
                            results={"a" * 64: "FIRST", "b" * 64: "SECOND"})
        md = _run(d, rd)
        self.assertLess(md.index("FIRST"), md.index("SECOND"))

    def test_tiles_are_sorted_even_when_pages_json_lists_them_out_of_order(self):
        """The sort is load-bearing: pages.json order is not guaranteed to be tile order."""
        rows = [
            {"page": 2, "image": "p02.png", "img_sha": "b" * 64, "text_len": 1,
             "n_drawings": 0, "n_tables": 0, "img_cover": 1.0, "flagged": True,
             "why": "html-segment", "segment": 1, "tile": 2, "of": 2},
            {"page": 1, "image": "p01.png", "img_sha": "a" * 64, "text_len": 1,
             "n_drawings": 0, "n_tables": 0, "img_cover": 1.0, "flagged": True,
             "why": "html-segment", "segment": 1, "tile": 1, "of": 2},
        ]
        d, rd = _render_dir(rows, {1: "x", 2: "y"},
                            results={"a" * 64: "FIRST", "b" * 64: "SECOND"})
        md = _run(d, rd)
        self.assertLess(md.index("FIRST"), md.index("SECOND"))
        self.assertEqual(md.count("## p"), 1)

    def test_a_later_tile_supplies_the_title_when_the_first_has_none(self):
        rows = [
            {"page": 1, "image": "p01.png", "img_sha": "a" * 64, "text_len": 0,
             "n_drawings": 0, "n_tables": 0, "img_cover": 1.0, "flagged": True,
             "why": "html-segment", "segment": 1, "tile": 1, "of": 2},
            {"page": 2, "image": "p02.png", "img_sha": "b" * 64, "text_len": 9,
             "n_drawings": 0, "n_tables": 0, "img_cover": 1.0, "flagged": True,
             "why": "html-segment", "segment": 1, "tile": 2, "of": 2},
        ]
        d, rd = _render_dir(rows, {1: "", 2: "Risks"},
                            results={"b" * 64: "# Risks\nStaffing is short."})
        md = _run(d, rd)
        self.assertIn("· Risks", md)
        self.assertNotIn("· Page 1", md)

    def test_rows_without_a_segment_assemble_exactly_as_before(self):
        """The regression guard: every pages.json render_pages.py ever wrote."""
        rows = [
            {"page": 1, "image": "p01.png", "img_sha": "a" * 64, "text_len": 20,
             "n_drawings": 0, "n_tables": 0, "img_cover": 0.0, "flagged": False, "why": ""},
            {"page": 2, "image": "p02.png", "img_sha": "b" * 64, "text_len": 20,
             "n_drawings": 0, "n_tables": 0, "img_cover": 0.0, "flagged": False, "why": ""},
        ]
        d, _ = _render_dir(rows, {1: "page one text", 2: "page two text"})
        md = _run(d)
        self.assertIn("## p01", md)
        self.assertIn("## p02", md)
        self.assertIn("page one text", md)
        self.assertIn("page two text", md)


if __name__ == "__main__":
    unittest.main()
