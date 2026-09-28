"""The Scribe loop guard must hold end to end: registration AND parsing.

`source_registry.iter_files` skips excluded / scribe-marked files; `parse_corpus` walks
the whole corpus, so unless it applies the same predicate a marked or excluded file is
parsed into an unmanaged doc and strict `brain_sync` refuses the whole update.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
import zipfile
from argparse import Namespace
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


R = _load("lg_source_registry", PIPE / "source_registry.py")
S = _load("lg_brain_sync", PIPE / "brain_sync.py")
P = _load("lg_parse_corpus", CTE / "parse_corpus.py")


def _marked_docx(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("docProps/custom.xml",
                   '<Properties><property name="scribe-task"><vt:lpwstr>t1</vt:lpwstr></property></Properties>')


class ScribeMarkerCopiesTests(unittest.TestCase):
    def test_the_two_scribe_marker_copies_are_byte_identical(self):
        self.assertEqual((PIPE / "scribe_marker.py").read_bytes(), (CTE / "scribe_marker.py").read_bytes())


class ParseCorpusLoopGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.corpus = self.root / "docs"
        (self.corpus / "_ai-docs").mkdir(parents=True)
        (self.corpus / "moved").mkdir()
        _marked_docx(self.corpus / "moved" / "Copied Profile.docx")
        (self.corpus / "notes.md").write_text("<!-- scribe: task=t1 version=2 -->\n# T\nbody\n")
        (self.corpus / "_ai-docs" / "Profile.md").write_text("# Generated\nbody\n")
        (self.corpus / "real.md").write_text("# Real\nhuman notes\n")
        self.parsed = self.root / "parsed"
        self.db = self.root / "knowledge.sqlite"
        sqlite3.connect(self.db).close()
        self.config = self.root / "brain.toml"
        self.config.write_text('''version = 1
[sources.roots.docs]
path = "docs"
mode = "import"
include = ["**/*.md", "**/*.docx"]
exclude = ["**/_ai-docs/**"]
''')

    def tearDown(self):
        self.temp.cleanup()

    def test_marked_and_excluded_files_are_skipped_with_reasons_and_strict_sync_accepts(self):
        P.main(["--corpus", str(self.corpus), "--out", str(self.parsed), "--exclude", "**/_ai-docs/**"])
        docs = sorted(p.name for p in self.parsed.glob("*.md"))
        self.assertEqual(docs, ["real.md.md"])
        manifest = json.loads((self.parsed / "manifest.json").read_text())
        skipped = {(e["source"], e.get("reason")) for e in manifest if e.get("skipped")}
        self.assertEqual(skipped, {("moved/Copied Profile.docx", "scribe_marker"),
                                   ("notes.md", "scribe_marker"),
                                   ("_ai-docs/Profile.md", "excluded")})

        # Register exactly what the registry would register, then strict sync the parsed dir.
        cfg = R.load_config(self.config)
        with R.connect(str(self.db)) as con:
            R.ensure_schema(con)
            plan = R.build_plan(con, cfg)
            for action in plan["actions"]:
                if action["action"] == "add":
                    R.register(con, "docs", action["relative_path"], self.corpus / action["relative_path"])
        with sqlite3.connect(self.db) as con:
            S.ensure_documents(con)
            links, unmanaged = S.source_ids(con, str(self.parsed), None, "docs", True)
        self.assertEqual(unmanaged, [])
        self.assertEqual(set(links), {"real.md.md"})
        S.cmd_plan(Namespace(db=str(self.db), parsed=str(self.parsed), manifest=None, root_key="docs",
                             strict_sources=True))

    def test_a_stale_parsed_doc_of_a_now_marked_file_is_removed(self):
        self.parsed.mkdir()
        (self.parsed / "notes.md.md").write_text("# SOURCE: notes.md\n# method: passthrough\n\nold")
        P.main(["--corpus", str(self.corpus), "--out", str(self.parsed), "--exclude", "**/_ai-docs/**"])
        self.assertFalse((self.parsed / "notes.md.md").exists())


if __name__ == "__main__":
    unittest.main()
