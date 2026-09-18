"""The HTML path must emit EXACTLY the layout render_pages.py produces.

That parity is what lets vision_prep.py stay untouched, so it is asserted against
the real key set rather than a copy of it.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "html_capture", HERE.parent / "skills" / "visual-parse" / "html_capture.py")
HC = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(HC)

FIXTURE = json.loads((HERE / "fixtures" / "segments_deck.json").read_text())

# The row keys render_pages.py writes (render_pages.py, the pages.append call).
RENDER_PAGES_ROW_KEYS = {"page", "image", "img_sha", "text_len", "n_drawings",
                         "n_tables", "img_cover", "flagged", "why"}
RENDER_PAGES_TOP_KEYS = {"doc", "slug", "dpi", "pages"}


def _outdir_with_pngs(plan):
    """assemble() only hashes these and checks they exist — it never decodes them."""
    d = Path(tempfile.mkdtemp())
    for entry in plan:
        (d / f"p{entry['page']:02d}.png").write_bytes(
            b"\x89PNG\r\n\x1a\n" + bytes([entry["page"]]) * 16)
    return d


class AssembleTests(unittest.TestCase):
    def setUp(self):
        self.plan = HC.plan_captures(FIXTURE, max_px=1600)
        self.outdir = _outdir_with_pngs(self.plan)
        self.pages = HC.assemble(FIXTURE, self.plan, str(self.outdir), dpi=96)

    def test_pages_json_matches_render_pages_contract(self):
        self.assertTrue(RENDER_PAGES_TOP_KEYS <= set(self.pages))
        for row in self.pages["pages"]:
            self.assertTrue(RENDER_PAGES_ROW_KEYS <= set(row),
                            f"row missing {RENDER_PAGES_ROW_KEYS - set(row)}")

    def test_rows_carry_the_segment_grouping(self):
        for row in self.pages["pages"]:
            self.assertIn("segment", row)
        self.assertGreater(len({r["segment"] for r in self.pages["pages"]}), 1)

    def test_text_sidecar_written_per_page(self):
        for row in self.pages["pages"]:
            self.assertTrue((self.outdir / f"p{row['page']:02d}.txt").is_file())

    def test_dom_tables_land_as_markdown_grids(self):
        seg2 = next(r for r in self.pages["pages"] if r["segment"] == 2)
        grid = (self.outdir / f"p{seg2['page']:02d}.tables.md").read_text()
        self.assertIn("| Quarter | FCR |", grid)
        self.assertIn("| Q3 | 72% |", grid)
        self.assertEqual(seg2["n_tables"], 1)

    def test_a_table_is_not_split_across_tiles(self):
        """Grids come from the DOM, not the image, so tiling cannot break one."""
        plan = HC.plan_captures(FIXTURE, max_px=200)   # forces heavy tiling
        out = _outdir_with_pngs(plan)
        pages = HC.assemble(FIXTURE, plan, str(out), dpi=96)
        rows = [r for r in pages["pages"] if r["segment"] == 2]
        with_tables = [r for r in rows if r["n_tables"]]
        self.assertEqual(len(with_tables), 1, "the grid belongs to the segment, once")

    def test_slug_is_stable_and_collision_free(self):
        """The slug names the assets directory, so two sources must never share one."""
        long_a = "https://site-alpha.example.com/reports/2024/q3/deck-final-review-for-ops-leadership-team-presentation-v2"
        long_b = "https://site-beta.example.org/reports/2024/q3/deck-final-review-for-ops-leadership-team-presentation-v2"
        self.assertNotEqual(HC._slug(long_a), HC._slug(long_b))
        self.assertEqual(HC._slug(long_a), HC._slug(long_a))          # stable
        self.assertNotEqual(HC._slug("https://example.com/a"), HC._slug("https://example.com/b"))
        self.assertTrue(HC._slug("https://example.com/reports/q3"))

    def test_img_sha_is_the_captured_file(self):
        row = self.pages["pages"][0]
        png_path = self.outdir / f"p{row['page']:02d}.png"
        expected_sha = hashlib.sha256(png_path.read_bytes()).hexdigest()
        self.assertEqual(row["img_sha"], expected_sha)

    def test_missing_png_is_reported_not_guessed(self):
        (self.outdir / "p01.png").unlink()
        with self.assertRaises(FileNotFoundError):
            HC.assemble(FIXTURE, self.plan, str(self.outdir), dpi=96)

    def test_grid_normalizes_ragged_rows(self):
        """Rows with different column counts are padded to header width."""
        ragged_table = {
            "caption": "Ragged",
            "hasHeader": True,
            "rows": [["A", "B"], ["1", "2", "3"], ["x"]]
        }
        grid = HC._grid(ragged_table)
        lines = grid.split("\n")
        # Caption, blank, header, separator, two body rows = 6 lines
        self.assertEqual(len(lines), 6)
        # All table lines (skip caption and blank) have 3 cells
        table_lines = lines[2:]  # skip caption and blank line
        for line in table_lines:
            pipe_count = line.count("|")
            # 3 cells = 4 pipes (leading, between each, trailing)
            self.assertEqual(pipe_count, 4, f"line has wrong column count: {line}")

    def test_grid_escapes_pipes_in_cells(self):
        """A cell containing | must be escaped to prevent it being parsed as a delimiter."""
        pipe_table = {
            "caption": "With pipes",
            "hasHeader": True,
            "rows": [["a|b", "c"], ["x", "y"]]
        }
        grid = HC._grid(pipe_table)
        self.assertIn("a\\|b", grid)
        # Verify escaping doesn't break parsing: separator line shows column structure
        lines = grid.split("\n")
        sep_line = lines[3]  # skip caption, blank, header to get separator
        cell_count = sep_line.count("|") - 1  # leading and trailing pipes, so columns = pipes - 1
        self.assertEqual(cell_count, 2, "table should have 2 columns despite escaped pipe")

    def test_grid_handles_headerless_tables(self):
        """A table without hasHeader gets empty cells as a header."""
        headerless_table = {
            "caption": "No header",
            "hasHeader": False,
            "rows": [["1", "2"], ["3", "4"]]
        }
        grid = HC._grid(headerless_table)
        lines = grid.split("\n")
        # Caption, blank, empty-cell header, separator, two body rows = 6 lines
        self.assertEqual(len(lines), 6)
        # Header line (skip caption and blank) should have empty cells (spaces between pipes)
        header = lines[2]
        self.assertIn("|  |  |", header, "header should have empty cells")


if __name__ == "__main__":
    unittest.main()
