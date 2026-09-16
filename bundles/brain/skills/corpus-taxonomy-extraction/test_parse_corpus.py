from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("parse_corpus", HERE / "parse_corpus.py")
P = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(P)


class LibreOfficeIsolationTests(unittest.TestCase):
    def test_office_conversion_uses_unique_profile_and_cleans_it(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "deck.pptx"; source.write_bytes(b"pptx")
            calls = []
            def fake_run(argv, **kwargs):
                calls.append(argv)
                outdir = Path(argv[argv.index("--outdir") + 1])
                (outdir / "deck.pdf").write_bytes(b"pdf")
            with patch.object(P, "_soffice", return_value="soffice"), \
                 patch.object(P.subprocess, "run", side_effect=fake_run), \
                 patch.object(P, "parse_pdf_pymupdf", return_value="parsed"):
                self.assertEqual(P.parse_office_pymupdf(str(source)), "parsed")
            profile_arg = next(x for x in calls[0] if x.startswith("-env:UserInstallation="))
            profile = Path(profile_arg.split("=", 1)[1].replace("file://", ""))
            self.assertFalse(profile.exists())

    def test_failed_conversion_cleans_profile_and_output_temp(self):
        made = []
        real_mkdtemp = tempfile.mkdtemp
        def tracked(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs); made.append(Path(path)); return path
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "deck.pptx"; source.write_bytes(b"pptx")
            with patch.object(P, "_soffice", return_value="soffice"), \
                 patch.object(P.tempfile, "mkdtemp", side_effect=tracked), \
                 patch.object(P.subprocess, "run", side_effect=RuntimeError("busy")):
                with self.assertRaisesRegex(RuntimeError, "busy"):
                    P.parse_office_pymupdf(str(source))
        self.assertTrue(made)
        self.assertTrue(all(not path.exists() for path in made))


class AiDialJsonParseTests(unittest.TestCase):
    """Tests for _parse_ai_dial_json — string, structured-block, and non-DIAL formats."""

    def _make_json(self, messages, tmp_path):
        import json, tempfile, os
        payload = {"history": [{"name": "conv", "messages": messages}]}
        p = os.path.join(str(tmp_path), "conv.json")
        open(p, "w").write(json.dumps(payload))
        return p

    def test_string_content_is_returned(self):
        """Plain string assistant messages with ≥50 chars are included."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = self._make_json([
                {"role": "assistant", "content": "A" * 60},
                {"role": "user", "content": "question"},
            ], td)
            result = P._parse_ai_dial_json(path)
            self.assertIsNotNone(result)
            self.assertIn("A" * 60, result)

    def test_list_content_string_items_are_joined(self):
        """list-valued content with plain strings is joined and included."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = self._make_json([
                {"role": "assistant", "content": ["First part of message.", " Second part adds more text here."]},
            ], td)
            result = P._parse_ai_dial_json(path)
            self.assertIsNotNone(result)
            self.assertIn("First part", result)

    def test_list_content_dict_blocks_are_extracted(self):
        """Anthropic API structured content blocks {type, text} are extracted."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = self._make_json([
                {"role": "assistant", "content": [
                    {"type": "text", "text": "This is a structured block with enough length to pass the 50-char threshold."},
                ]},
            ], td)
            result = P._parse_ai_dial_json(path)
            self.assertIsNotNone(result)
            self.assertIn("structured block", result)

    def test_non_dial_json_returns_none(self):
        """JSON without 'history' key is not AI DIAL format — returns None."""
        import json, tempfile, os
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "other.json")
            open(p, "w").write(json.dumps({"messages": [{"role": "user", "content": "hello"}]}))
            result = P._parse_ai_dial_json(p)
            self.assertIsNone(result)

    def test_short_messages_below_threshold_skipped(self):
        """Assistant messages shorter than 50 chars are filtered out."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = self._make_json([
                {"role": "assistant", "content": "Too short."},
            ], td)
            result = P._parse_ai_dial_json(path)
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
