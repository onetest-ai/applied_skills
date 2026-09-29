"""F1: hidden files/dirs are skipped everywhere a source or raw file is selected.

A path is hidden when any component (dir or file, relative to the scanned root)
starts with "." (dot-files, dot-dirs, macOS AppleDouble ``._x``), or the file name
starts with "~$" (Office lock/owner files), or the OS marks it hidden. `skip_reason`
must return "hidden" BEFORE checking excludes/scribe markers.
"""
from __future__ import annotations

import importlib.util
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / "skills"
PIPE = SKILLS / "knowledge-pipeline"
CTE = SKILLS / "corpus-taxonomy-extraction"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


M = _load("hidden_scribe_marker", PIPE / "scribe_marker.py")


class IsHiddenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _touch(self, rel):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return p

    def test_dot_file_is_hidden(self):
        p = self._touch(".DS_Store")
        self.assertTrue(M.is_hidden(p, ".DS_Store"))

    def test_appledouble_file_is_hidden(self):
        p = self._touch("._report.pdf")
        self.assertTrue(M.is_hidden(p, "._report.pdf"))

    def test_dot_dir_component_is_hidden(self):
        p = self._touch(".hidden/dir/a.pdf")
        self.assertTrue(M.is_hidden(p, ".hidden/dir/a.pdf"))

    def test_office_lock_file_is_hidden(self):
        p = self._touch("~$lock.docx")
        self.assertTrue(M.is_hidden(p, "~$lock.docx"))

    def test_normal_file_is_not_hidden(self):
        p = self._touch("real.pdf")
        self.assertFalse(M.is_hidden(p, "real.pdf"))

    def test_normal_file_next_to_hidden_siblings_is_not_hidden(self):
        self._touch(".DS_Store")
        self._touch("._report.pdf")
        p = self._touch("real.pdf")
        self.assertFalse(M.is_hidden(p, "real.pdf"))

    def test_uf_hidden_flag_is_hidden(self):
        uf_hidden = getattr(stat, "UF_HIDDEN", None)
        if uf_hidden is None or not hasattr(__import__("os"), "chflags"):
            self.skipTest("UF_HIDDEN/os.chflags unavailable on this platform")
        p = self._touch("flagged.pdf")
        try:
            import os
            os.chflags(p, uf_hidden)
        except (AttributeError, OSError):
            self.skipTest("chflags(UF_HIDDEN) unavailable on this platform")
        self.assertTrue(M.is_hidden(p, "flagged.pdf"))

    def test_never_raises_on_missing_file(self):
        missing = self.root / "gone.pdf"
        self.assertFalse(M.is_hidden(missing, "gone.pdf"))


class SkipReasonHiddenPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_hidden_wins_over_exclude_and_marker(self):
        p = self.root / ".hidden.md"
        p.write_text("<!-- scribe: task=t1 version=1 -->\nbody\n")
        self.assertEqual(M.skip_reason(p, ".hidden.md", ["**/*.md"]), "hidden")

    def test_non_hidden_excluded_file_is_excluded(self):
        p = self.root / "gen.md"
        p.write_text("body")
        self.assertEqual(M.skip_reason(p, "gen.md", ["**/*.md"]), "excluded")

    def test_non_hidden_marked_file_is_scribe_marker(self):
        p = self.root / "notes.md"
        p.write_text("<!-- scribe: task=t1 version=1 -->\nbody\n")
        self.assertEqual(M.skip_reason(p, "notes.md", []), "scribe_marker")

    def test_plain_file_is_none(self):
        p = self.root / "real.md"
        p.write_text("body")
        self.assertIsNone(M.skip_reason(p, "real.md", []))


class CopiesStayInSyncTests(unittest.TestCase):
    def test_both_copies_export_is_hidden_and_agree(self):
        M2 = _load("hidden_scribe_marker_cte", CTE / "scribe_marker.py")
        self.assertEqual((PIPE / "scribe_marker.py").read_bytes(), (CTE / "scribe_marker.py").read_bytes())
        p = Path(tempfile.mkstemp()[1])
        try:
            self.assertEqual(M.is_hidden(p, ".x"), M2.is_hidden(p, ".x"))
        finally:
            p.unlink(missing_ok=True)


class SourceRegistryHiddenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _reg(self):
        return _load("hidden_source_registry", PIPE / "source_registry.py")

    def test_iter_files_skips_hidden_file_and_dir_with_reason(self):
        R = self._reg()
        docs = self.root
        (docs / ".hidden" / "dir").mkdir(parents=True)
        (docs / ".hidden" / "dir" / "a.pdf").write_bytes(b"%PDF-1.4")
        (docs / ".DS_Store").write_bytes(b"x")
        (docs / "real.pdf").write_bytes(b"%PDF-1.4")
        found, skipped = R.iter_files(docs, ["**/*.pdf", "**/*"], [])
        self.assertEqual(set(found), {"real.pdf"})
        skip_map = {s["relative_path"]: s["reason"] for s in skipped}
        self.assertEqual(skip_map.get(".hidden/dir/a.pdf"), "hidden")
        self.assertEqual(skip_map.get(".DS_Store"), "hidden")


class ParseCorpusHiddenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.corpus = self.root / "docs"
        self.corpus.mkdir()
        self.parsed = self.root / "parsed"

    def tearDown(self):
        self.temp.cleanup()

    def _parse(self):
        return _load("hidden_parse_corpus", CTE / "parse_corpus.py")

    def test_hidden_dir_and_files_are_skipped_with_reason_and_stale_removed(self):
        P = self._parse()
        (self.corpus / ".hidden" / "dir").mkdir(parents=True)
        (self.corpus / ".hidden" / "dir" / "a.pdf").write_bytes(b"x")
        (self.corpus / ".DS_Store").write_bytes(b"x")
        (self.corpus / "~$lock.docx").write_bytes(b"x")
        (self.corpus / "real.md").write_text("# Real\nbody\n")
        self.parsed.mkdir()
        (self.parsed / ".hidden__dir__a.pdf.md").write_text("# SOURCE: .hidden/dir/a.pdf\n# method: x\n\nold")
        P.main(["--corpus", str(self.corpus), "--out", str(self.parsed), "--formats", "md,pdf,docx"])
        manifest = json.loads((self.parsed / "manifest.json").read_text())
        skip_map = {e["source"]: e.get("reason") for e in manifest if e.get("skipped")}
        self.assertEqual(skip_map.get(".hidden/dir/a.pdf"), "hidden")
        self.assertEqual(skip_map.get(".DS_Store"), "hidden")
        self.assertEqual(skip_map.get("~$lock.docx"), "hidden")
        self.assertFalse((self.parsed / ".hidden__dir__a.pdf.md").exists())
        docs = sorted(p.name for p in self.parsed.glob("*.md") if p.name != "manifest.json")
        self.assertEqual(docs, ["real.md.md"])


if __name__ == "__main__":
    unittest.main()
