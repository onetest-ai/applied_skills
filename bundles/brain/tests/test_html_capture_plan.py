"""Tall segments are where the PPTX analogy breaks.

A PPTX page is bounded; an HTML segment is not. Vision models downscale large
images, so a tall segment captured whole has illegible body text — the exact
input that yields a confident, wrong transcription.
"""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "html_capture", HERE.parent / "skills" / "visual-parse" / "html_capture.py")
HC = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(HC)

FIXTURE = json.loads((HERE / "fixtures" / "segments_deck.json").read_text())


class PlanCapturesTests(unittest.TestCase):
    def test_short_segments_are_one_tile_each(self):
        plan = HC.plan_captures(FIXTURE, max_px=1600)
        first = [p for p in plan if p["segment"] == 1]
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["of"], 1)
        self.assertEqual(first[0]["clip"]["height"], 720)

    def test_tall_segment_is_tiled(self):
        plan = HC.plan_captures(FIXTURE, max_px=1600)
        tiles = [p for p in plan if p["segment"] == 3]
        self.assertGreater(len(tiles), 1, "a 5200px segment must not be captured whole")
        self.assertTrue(all(t["clip"]["height"] <= 1600 for t in tiles))
        self.assertEqual({t["of"] for t in tiles}, {len(tiles)})

    def test_tiles_overlap_so_nothing_is_lost_at_a_seam(self):
        plan = HC.plan_captures(FIXTURE, max_px=1600, overlap=0.1)
        tiles = sorted((p for p in plan if p["segment"] == 3), key=lambda t: t["tile"])
        for a, b in zip(tiles, tiles[1:]):
            a_end = a["clip"]["y"] + a["clip"]["height"]
            self.assertLess(b["clip"]["y"], a_end, "consecutive tiles must overlap")

    def test_tiles_cover_the_whole_segment(self):
        plan = HC.plan_captures(FIXTURE, max_px=1600)
        seg = next(s for s in FIXTURE["segments"] if s["index"] == 3)
        tiles = sorted((p for p in plan if p["segment"] == 3), key=lambda t: t["tile"])
        self.assertEqual(tiles[0]["clip"]["y"], seg["bbox"]["y"])
        last = tiles[-1]["clip"]
        self.assertGreaterEqual(last["y"] + last["height"],
                                seg["bbox"]["y"] + seg["bbox"]["height"])

    def test_pages_are_numbered_consecutively_across_segments(self):
        plan = HC.plan_captures(FIXTURE, max_px=1600)
        self.assertEqual([p["page"] for p in plan], list(range(1, len(plan) + 1)))

    def test_invalid_segments_raise_with_the_problems_named(self):
        bad = {"source": "", "segments": []}
        with self.assertRaises(ValueError) as cm:
            HC.plan_captures(bad)
        self.assertIn("source", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
