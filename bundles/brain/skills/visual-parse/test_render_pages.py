from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("render_pages", HERE / "render_pages.py")
R = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(R)


class LibreOfficeRenderIsolationTests(unittest.TestCase):
    def test_to_pdf_uses_unique_profile_and_returns_temp_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "deck.pptx"; source.write_bytes(b"pptx")
            calls = []
            def fake_run(argv, **kwargs):
                calls.append(argv)
                outdir = Path(argv[argv.index("--outdir") + 1])
                (outdir / "deck.pdf").write_bytes(b"pdf")
            with patch.object(R, "soffice_bin", return_value="soffice"), \
                 patch.object(R.subprocess, "run", side_effect=fake_run):
                pdf, tmp = R.to_pdf(str(source))
                self.assertTrue(Path(pdf).is_file())
                profile_arg = next(x for x in calls[0] if x.startswith("-env:UserInstallation="))
                profile = Path(profile_arg.split("=", 1)[1].replace("file://", ""))
                self.assertFalse(profile.exists())
                R.shutil.rmtree(tmp)

    def test_failed_conversion_cleans_all_temporary_directories(self):
        made = []
        real_mkdtemp = tempfile.mkdtemp
        def tracked(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs); made.append(Path(path)); return path
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "deck.pptx"; source.write_bytes(b"pptx")
            with patch.object(R, "soffice_bin", return_value="soffice"), \
                 patch.object(R.tempfile, "mkdtemp", side_effect=tracked), \
                 patch.object(R.subprocess, "run", side_effect=RuntimeError("busy")):
                with self.assertRaisesRegex(RuntimeError, "busy"):
                    R.to_pdf(str(source))
        self.assertTrue(made)
        self.assertTrue(all(not path.exists() for path in made))


if __name__ == "__main__":
    unittest.main()
