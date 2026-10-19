"""section_io.py is the one format-free writer both fact-check readers use (spec §4.1, seam 1)."""
from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

from test_plugin_structure import KB_ROOT

import sys
sys.path.insert(0, str(KB_ROOT / "skills" / "doc-fact-check"))
import section_io  # noqa: E402

DOC = KB_ROOT / "skills" / "doc-fact-check"
MOVED = ("RISK_PATTERNS", "tag_risk", "split_sentences", "estimate_minutes", "build_batches", "_render", "_units",
         "_dump", "_words")


def _section(n: int, blocks: list) -> dict:
    return {"section_id": f"s{n:02d}", "section": f"Slide {n}: Plan", "heading_path": [f"Slide {n}", "Plan"],
            "level": 1, "heading_p_id": f"s{n}-sh2-p1", "words": 2 + sum(w for *_, w in blocks), "blocks": blocks}


class OneDefinition(unittest.TestCase):
    def test_sections_py_defines_none_of_the_moved_names(self):
        tree = ast.parse((DOC / "sections.py").read_text(encoding="utf-8"))
        defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        defined |= {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
        self.assertFalse(defined & set(MOVED), f"sections.py redefines {defined & set(MOVED)}")

    def test_sections_reexports_the_shared_objects(self):
        import sections
        for name in MOVED:
            self.assertIs(getattr(sections, name), getattr(section_io, name), name)


class WriterContract(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.src = self.d / "deck.pptx"
        self.src.write_bytes(b"not read except for its hash")
        ctx = {"title": "Plan", "header": ["Metric", "Value"], "row_label": "Uptime"}
        pos = {"x": 914400, "y": 914400, "w": 1828800, "h": 457200}
        sent = {"s_id": "s1-sh3-p1s1", "text": "Go-live is set for Q2 2027.", "risk": section_io.tag_risk("Go-live is set for Q2 2027.")}
        self.sections = [_section(1, [
            ("p", {"p_id": "s1-sh3-p1", "text": sent["text"], "sentences": [sent], "ctx": {"title": "Plan"}, "pos": pos}, 6),
            ("r", {"t_id": "s1-sh4", "r_id": "s1-sh4-r2", "cells": ["Uptime", "99.9%"], "s_id": "s1-sh4-r2",
                   "risk": ["num"], "ctx": ctx, "pos": pos}, 2),
        ])]

    def test_ctx_and_pos_pass_through_to_sections_and_batches(self):
        index = section_io.write_outputs(self.src, self.d, self.sections, 1500, extra_stats={"hidden_slides": [2]})
        self.assertEqual([b["file"] for b in index], ["batch_1.json"])
        for name in ("sections.json", "batch_1.json"):
            doc = json.loads((self.d / name).read_text(encoding="utf-8"))
            sec = (doc if isinstance(doc, list) else doc["sections"])[0]
            self.assertEqual(sec["paragraphs"][0]["ctx"], {"title": "Plan"}, name)
            self.assertEqual(sec["tables"][0]["rows"][0]["ctx"]["row_label"], "Uptime", name)
            self.assertEqual(sec["tables"][0]["rows"][0]["pos"]["w"], 1828800, name)
        stats = json.loads((self.d / "stats.json").read_text(encoding="utf-8"))
        self.assertEqual(stats["hidden_slides"], [2])
        self.assertEqual((stats["statements_total"], stats["statements_risk"]), (2, 2))

    def test_rows_without_ctx_keep_the_docx_shape(self):
        self.sections[0]["blocks"][1][1].pop("ctx")
        self.sections[0]["blocks"][1][1].pop("pos")
        section_io.write_outputs(self.src, self.d, self.sections, 1500)
        row = json.loads((self.d / "sections.json").read_text(encoding="utf-8"))[0]["tables"][0]["rows"][0]
        self.assertEqual(set(row), {"r_id", "cells", "s_id", "risk"})

    def test_stale_batch_files_are_removed(self):
        (self.d / "batch_9.json").write_text("{}")
        section_io.write_outputs(self.src, self.d, self.sections, 1500)
        self.assertFalse((self.d / "batch_9.json").exists())


if __name__ == "__main__":
    unittest.main()


class NumberWordIdiomTests(unittest.TestCase):
    """'one' in an idiom ("one-off", "one of", "one-to-one") counts nothing: no `num` tag (spec §10 X6)."""

    def test_idioms_are_not_numbers(self):
        from section_io import tag_risk
        for text in ("ONE-OFF ORDERS", "one of the depots", "one-to-one coaching", "a one-time setup",
                     "one another"):
            self.assertNotIn("num", tag_risk(text), text)

    def test_counting_words_still_are(self):
        from section_io import tag_risk
        for text in ("one bottle per week", "two deliveries in one cycle", "three regions"):
            self.assertIn("num", tag_risk(text), text)
