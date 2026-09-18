"""Removing the degraded HTML path's `## [part N]` print-pagination headings
(review finding 6) was correct, but it means a degraded document with no
internal headings has NOTHING for section_records() to split on except blank
lines between paragraphs. A document with no blank lines at all — a single
run-on `<p>` — falls straight through the oversize-splitting loop's single
`para`, which is emitted whole regardless of `max_chars`.

This is a known gap flagged by the whole-branch review, not something this
wave decided to fix (changing the chunker's oversize behaviour is a bigger
decision, out of scope here). xfail with strict=True so this stays visible:
if the chunker is ever fixed to subdivide an oversized single paragraph, this
test starts unexpectedly passing and someone has to notice and remove the
xfail.
"""
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

import pytest

import chunking  # noqa: E402  (conftest puts every skill dir on sys.path)

HERE = Path(__file__).resolve().parent.parent / "skills" / "corpus-taxonomy-extraction"
SPEC = importlib.util.spec_from_file_location("parse_corpus", HERE / "parse_corpus.py")
P = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(P)


class DegradedHtmlChunkingTests(unittest.TestCase):
    def _parse(self, body, name="deck.html"):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / name
            p.write_text(body, encoding="utf-8")
            return P.parse_one(str(p), 20.0, 8)

    @pytest.mark.xfail(
        strict=True,
        reason="chunker's oversize-paragraph split does not subdivide a single "
                "run-on paragraph — a degraded HTML body with no blank lines "
                "yields one chunk larger than max_chars (review follow-up, not "
                "fixed in this wave)",
    )
    def test_no_blank_line_degraded_body_stays_within_max_chars(self):
        # A single run-on <p>, long enough to exceed the default max_chars once
        # parsed, short enough to stay on ONE of PyMuPDF's internal HTML print
        # pages — so the degraded parse's page-join ("\n\n") never introduces a
        # paragraph break of its own. This isolates the no-blank-line case from
        # the (separate, already-handled) multi-page-join case.
        text = ("The quick brown fox jumps over the lazy dog and keeps running "
                "past the hedge. " * 30)
        html = f"<html><body><p>{text}</p></body></html>"
        md, method = self._parse(html)
        self.assertEqual(method, "pymupdf-html")
        self.assertNotIn("\n\n", md, "test setup must stay on one HTML print page")
        records = chunking.section_records(md)
        sizes = [len(r["body"]) for r in records]
        self.assertLessEqual(max(sizes), 1600,
                              f"largest of {len(records)} chunk(s) is {max(sizes)} chars")

    def test_with_a_real_paragraph_break_the_same_chunker_does_split(self):
        """Contrast: section_records() DOES split when there is at least one blank
        line to split on (each individual paragraph itself under max_chars) — the
        gap above is specific to a run-on body with none, not a general chunker
        failure."""
        para = ("The quick brown fox jumps over the lazy dog. " * 8).strip()  # ~376 chars
        md = "\n\n".join([para] * 3)
        records = chunking.section_records(md, max_chars=800)
        sizes = [len(r["body"]) for r in records]
        self.assertGreater(len(records), 1)
        self.assertLessEqual(max(sizes), 800)


if __name__ == "__main__":
    unittest.main()
