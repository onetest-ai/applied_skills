from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent.parent / "skills" / "visual-parse"
SPEC = importlib.util.spec_from_file_location("render_pages", HERE / "render_pages.py")
R = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(R)


class DocSlugTests(unittest.TestCase):
    def test_distinct_parent_dirs_produce_distinct_slugs(self):
        self.assertNotEqual(R.doc_slug("a/report.pdf"), R.doc_slug("b/report.pdf"))

    def test_bare_basename_matches_prior_basename_only_behavior(self):
        self.assertEqual(R.doc_slug("report.pdf"), R.kebab("report"))

    def test_deep_relative_paths_preserved_and_distinct(self):
        s1 = R.doc_slug("corpus/2024/q1/report.pdf")
        s2 = R.doc_slug("corpus/2024/q2/report.pdf")
        self.assertNotEqual(s1, s2)
        self.assertTrue(s1.endswith("__report"))

    def test_dash_in_component_does_not_collide_with_dir_boundary(self):
        # "a-b/report" and "a/b-report" would collide under a naive path->kebab
        # collapse; the `__` join between components keeps them distinct.
        self.assertNotEqual(R.doc_slug("a-b/report.pdf"), R.doc_slug("a/b-report.pdf"))


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


try:
    import pymupdf  # noqa: F401
    HAVE_PYMUPDF = True
except ImportError:
    HAVE_PYMUPDF = False


@unittest.skipUnless(HAVE_PYMUPDF, "needs PyMuPDF")
class RenderFunctionMatchesCliTests(unittest.TestCase):
    """render() is the library entry point deck-fact-check vendors; the CLI must stay a thin wrapper over it."""

    def _pdf(self, path: Path) -> None:
        import pymupdf
        doc = pymupdf.open()
        for n in range(1, 4):
            page = doc.new_page()
            page.insert_text((72, 72), f"Page {n}: Uptime held at 99.{n}% in FY2025 across the Northwind region.")
        doc.save(str(path))

    def test_render_and_cli_write_identical_pages_json(self):
        import json
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            self._pdf(td / "deck.pdf")
            subprocess.run([sys.executable, str(HERE / "render_pages.py"), "--doc", "deck.pdf", "--out", "cli"],
                           cwd=td, check=True, capture_output=True)
            import os
            cwd = os.getcwd()
            os.chdir(td)
            try:
                got = R.render("deck.pdf", "lib")
            finally:
                os.chdir(cwd)
            cli = json.loads((td / "cli" / "deck" / "pages.json").read_text())
            lib = json.loads((td / "lib" / "deck" / "pages.json").read_text())
            self.assertEqual(cli, lib)
            self.assertEqual(got, lib)
            self.assertEqual([p["page"] for p in lib["pages"]], [1, 2, 3])
            self.assertEqual(lib["pages"][0]["image"], "deck/p01.png")
