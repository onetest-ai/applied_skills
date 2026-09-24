"""vision_prep batches pages in contiguous slices. Round-robin was tried and reverted: it
removed "same as previous frame" lines but split near-identical frames across agents, whose
different wording defeated the text dedup, and one agent filed two similar screens under each
other's hash (video-evals after-v3, 2026-09-24)."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

VP = Path(__file__).resolve().parent.parent / "skills" / "visual-parse" / "vision_prep.py"


def _render_dir(root: Path, n_pages: int) -> Path:
    d = root / "assets" / "rec--mp4"; d.mkdir(parents=True)
    pages = []
    for n in range(1, n_pages + 1):
        (d / f"p{n:02d}.png").write_bytes(b"png")
        pages.append({"page": n, "image": f"rec--mp4/p{n:02d}.png", "img_sha": f"s{n}", "flagged": True})
    (d / "pages.json").write_text(json.dumps({"doc": "rec.mp4", "slug": "rec--mp4", "medium": "video", "pages": pages}))
    return d


def _prep(rd: Path, out: Path, *extra):
    r = subprocess.run([sys.executable, str(VP), "--render-dir", str(rd), "--out", str(out), *extra],
                       check=True, capture_output=True, text=True)
    return r.stdout, [[i["page"] for i in json.loads(p.read_text())] for p in sorted(out.glob("batch_*.json"))]


class BatchingTests(unittest.TestCase):
    def test_consecutive_pages_stay_together(self):
        # one agent sees neighbouring frames, so it words a repeated screen the same way
        # and the text dedup in assemble can recognise it
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, batches = _prep(_render_dir(root, 10), root / "v", "--batches", "4")
        self.assertEqual(batches, [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10]])

    def test_fewer_pages_than_batches_writes_one_page_per_batch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, batches = _prep(_render_dir(root, 2), root / "v", "--batches", "4")
        self.assertEqual(batches, [[1], [2]])

    def test_summary_counts_pages_skipped_not_cache_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); db = root / "k.sqlite"
            c = sqlite3.connect(db)
            c.execute("CREATE TABLE page_render(img_sha TEXT PRIMARY KEY, doc TEXT, page INT, md TEXT, prompt_v INT)")
            c.executemany("INSERT INTO page_render VALUES(?,?,?,?,?)",
                          [("s1", "rec", 1, "# a", 2), ("other-doc", "x", 1, "# b", 1), ("s2", "rec", 2, "# old", None)])
            c.commit(); c.close()
            out, batches = _prep(_render_dir(root, 3), root / "v", "--db", str(db))
        self.assertIn("(1 already cached, skipped)", out)
        self.assertEqual(sorted(p for b in batches for p in b), [2, 3])


if __name__ == "__main__":
    unittest.main()
