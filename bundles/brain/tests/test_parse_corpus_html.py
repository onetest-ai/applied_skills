"""Without a browser, HTML still ingests as text — but says so, and refuses to
store a JS-rendered deck it captured nothing from."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "skills" / "corpus-taxonomy-extraction"
SPEC = importlib.util.spec_from_file_location("parse_corpus", HERE / "parse_corpus.py")
P = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(P)

STATIC = """<html><head><style>.s{color:red}</style></head><body>
<section class="slide"><h1>Q3 Contact Centre Review</h1>
<p>Five9 FCR was 72% in Q3, up from 68% in Q2. Handle time fell to 4m12s across the NW2
and SE1 branches, and staffing held at 94% against plan for the quarter.</p>
<p>Abandon rate closed at 3.1%, the lowest since the Engage migration, though the
after-call work backlog in SE1 remains the main risk going into Q4.</p></section>
<script>console.log("noise")</script></body></html>"""

JS_ONLY = """<html><body><div id="root"></div>
<script>document.getElementById('root').innerHTML='<h1>Q3</h1>'</script></body></html>"""


class HtmlDegradedPathTests(unittest.TestCase):
    def _parse(self, body, name="deck.html"):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / name
            p.write_text(body, encoding="utf-8")
            return P.parse_one(str(p), 20.0, 8)

    def test_static_html_yields_text(self):
        md, method = self._parse(STATIC)
        self.assertEqual(method, "pymupdf-html")
        self.assertIn("Five9 FCR was 72%", md)
        self.assertNotIn("console.log", md, "script content must not reach the store")

    def test_degraded_path_does_not_paginate(self):
        """HTML has no pages: PyMuPDF's print pagination is an offset a reader never
        saw, and the full-fidelity path segments by DOM, not by print page. Emitting
        '## [part N]' headings here would promote pagination to chunk boundaries and
        make the same deck cite incompatible targets depending on which path ingested
        it."""
        md, method = self._parse(STATIC)
        self.assertEqual(method, "pymupdf-html")
        self.assertNotIn("[part", md)
        self.assertNotIn("##", md)

    def test_htm_extension_also_handled(self):
        md, method = self._parse(STATIC, name="deck.htm")
        self.assertEqual(method, "pymupdf-html")
        self.assertIn("Q3 Contact Centre Review", md)

    def test_js_rendered_deck_is_skipped_with_a_reason(self):
        md, method = self._parse(JS_ONLY)
        self.assertFalse(md, "a deck we captured nothing from must not be stored")
        self.assertEqual(method, "skipped-js-rendered")

    def test_parsed_output_declares_degraded_fidelity(self):
        with tempfile.TemporaryDirectory() as td:
            corpus, out = Path(td) / "c", Path(td) / "p"
            corpus.mkdir()
            (corpus / "deck.html").write_text(STATIC, encoding="utf-8")
            P.main(["--corpus", str(corpus), "--out", str(out)])
            entry = json.loads((out / "manifest.json").read_text())[0]
            self.assertEqual(entry["method"], "pymupdf-html")
            text = (out / entry["md"]).read_text()
            self.assertIn("# fidelity: degraded", text)

    def test_skipped_deck_is_recorded_in_the_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            corpus, out = Path(td) / "c", Path(td) / "p"
            corpus.mkdir()
            (corpus / "app.html").write_text(JS_ONLY, encoding="utf-8")
            P.main(["--corpus", str(corpus), "--out", str(out)])
            entry = json.loads((out / "manifest.json").read_text())[0]
            self.assertTrue(entry.get("skipped"))
            self.assertEqual(entry["method"], "skipped-js-rendered")

    def test_a_document_just_under_the_threshold_is_skipped(self):
        """The guard is a real boundary, not a formality: content below it is not stored."""
        short = "<html><body><p>" + ("The quick brown fox jumps over the lazy dog. " * 4) + "</p></body></html>"
        md, method = self._parse(short)
        self.assertFalse(md)
        self.assertEqual(method, "skipped-js-rendered")

    def test_a_document_just_over_the_threshold_is_kept(self):
        long = "<html><body><p>" + ("The quick brown fox jumps over the lazy dog. " * 5) + "</p></body></html>"
        md, method = self._parse(long)
        self.assertTrue(md)
        self.assertEqual(method, "pymupdf-html")


if __name__ == "__main__":
    unittest.main()
