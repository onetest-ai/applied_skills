"""Markdown/plain-text sources must reach the parsed store.

`.md` is the pipeline's own intermediate format, so a pre-processed Markdown
corpus needs no conversion — only a passthrough that stamps the standard
`# SOURCE:` provenance header. Before this path existed `parse_one` fell
through to `(None, "skipped")`, which left an active registered `.md` source
with no parsed output and made `brain_sync apply` abort on
`blocked_missing_parsed`.
"""
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


class MarkdownPassthroughTests(unittest.TestCase):
    def _parse(self, name, body):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / name
            src.write_text(body, encoding="utf-8")
            return P.parse_one(str(src), 20.0, 8)

    def test_markdown_is_returned_verbatim(self):
        md, method = self._parse("notes.md", "# Notes\n\nFive9 FCR was 72% in Q3.\n")
        self.assertEqual(method, "passthrough")
        self.assertIn("Five9 FCR was 72% in Q3.", md)
        self.assertIn("# Notes", md)

    def test_markdown_long_extension_and_plain_text(self):
        for name in ("notes.markdown", "notes.txt"):
            with self.subTest(name=name):
                md, method = self._parse(name, "body text that survives\n")
                self.assertEqual(method, "passthrough")
                self.assertIn("body text that survives", md)

    def test_undecodable_bytes_do_not_raise(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "mixed.md"
            src.write_bytes(b"caf\xe9 latte notes")
            md, method = P.parse_one(str(src), 20.0, 8)
        self.assertEqual(method, "passthrough")
        self.assertIn("latte notes", md)

    def test_empty_markdown_is_skipped(self):
        md, method = self._parse("empty.md", "   \n\n")
        self.assertFalse(md)


class MarkdownEndToEndTests(unittest.TestCase):
    def test_default_formats_parse_markdown_with_source_header(self):
        with tempfile.TemporaryDirectory() as td:
            corpus, out = Path(td) / "corpus", Path(td) / "parsed"
            (corpus / "sub").mkdir(parents=True)
            (corpus / "sub" / "notes.md").write_text("# Notes\n\nFCR was 72%.\n", encoding="utf-8")
            P.main(["--corpus", str(corpus), "--out", str(out)])

            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual(len(manifest), 1)
            entry = manifest[0]
            self.assertEqual(entry["source"], "sub/notes.md")
            self.assertEqual(entry["method"], "passthrough")
            self.assertNotIn("skipped", entry)

            parsed = (out / entry["md"]).read_text()
            self.assertTrue(parsed.startswith("# SOURCE: sub/notes.md\n# method: passthrough\n"))
            self.assertIn("FCR was 72%.", parsed)


if __name__ == "__main__":
    unittest.main()
