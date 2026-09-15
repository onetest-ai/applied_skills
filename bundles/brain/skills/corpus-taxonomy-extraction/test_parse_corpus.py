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


if __name__ == "__main__":
    unittest.main()
