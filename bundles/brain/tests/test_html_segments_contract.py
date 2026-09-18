"""The segments.json contract, shared by the JS producer and every Python consumer.

There is no JS test runner here and segmentation needs a live DOM, so the JS stays
small and this pins the shape it must emit.
"""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / "skills" / "visual-parse"
SPEC = importlib.util.spec_from_file_location("html_capture", SKILL / "html_capture.py")
HC = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(HC)

FIXTURE = HERE / "fixtures" / "segments_deck.json"


class SegmentsContractTests(unittest.TestCase):
    def test_fixture_is_valid(self):
        self.assertEqual(HC.validate_segments(json.loads(FIXTURE.read_text())), [])

    def test_missing_source_url_is_reported(self):
        obj = json.loads(FIXTURE.read_text())
        del obj["source"]
        self.assertIn("source", " ".join(HC.validate_segments(obj)))

    def test_segment_missing_bbox_is_reported(self):
        obj = json.loads(FIXTURE.read_text())
        del obj["segments"][0]["bbox"]
        problems = HC.validate_segments(obj)
        self.assertTrue(problems, "a segment without a bbox must be reported")
        self.assertIn("bbox", " ".join(problems))

    def test_negative_height_is_reported(self):
        obj = json.loads(FIXTURE.read_text())
        obj["segments"][0]["height"] = -1
        self.assertIn("height", " ".join(HC.validate_segments(obj)))

    def test_empty_segment_list_is_reported(self):
        obj = json.loads(FIXTURE.read_text())
        obj["segments"] = []
        self.assertTrue(HC.validate_segments(obj))

    def test_segment_missing_text_is_reported(self):
        obj = json.loads(FIXTURE.read_text())
        del obj["segments"][0]["text"]
        self.assertIn("text", " ".join(HC.validate_segments(obj)))

    def test_zero_bbox_height_is_reported(self):
        """The planner tiles from bbox.height, so that is the field that must be valid."""
        obj = json.loads(FIXTURE.read_text())
        obj["segments"][0]["bbox"]["height"] = 0
        self.assertIn("bbox.height", " ".join(HC.validate_segments(obj)))

    def test_missing_index_is_reported(self):
        """plan_captures/assemble key everything on seg['index']; a payload missing it
        must be reported here, not surface as a bare KeyError downstream."""
        obj = json.loads(FIXTURE.read_text())
        del obj["segments"][0]["index"]
        self.assertIn("index", " ".join(HC.validate_segments(obj)))

    def test_non_integral_index_is_reported(self):
        """The message says 'integer-valued'; int(seg['index']) would otherwise
        silently truncate a value like 1.9, so the check must match the message."""
        obj = json.loads(FIXTURE.read_text())
        obj["segments"][0]["index"] = 1.9
        self.assertIn("integer", " ".join(HC.validate_segments(obj)))

    def test_nan_bbox_height_is_reported(self):
        """A fully hidden segment's bboxUnion() starts from Infinity seeds and can
        yield NaN — 'bh <= 0' alone does not catch it."""
        obj = json.loads(FIXTURE.read_text())
        obj["segments"][0]["bbox"]["height"] = float("nan")
        self.assertIn("bbox.height", " ".join(HC.validate_segments(obj)))

    def test_nan_height_is_reported(self):
        obj = json.loads(FIXTURE.read_text())
        obj["segments"][0]["height"] = float("nan")
        self.assertIn("height", " ".join(HC.validate_segments(obj)))


if __name__ == "__main__":
    unittest.main()
