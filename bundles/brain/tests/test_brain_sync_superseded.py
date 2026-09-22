from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "skills" / "knowledge-pipeline"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


R = _load("source_registry"); S = _load("brain_sync")


class SupersededTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.root = Path(self.t.name)
        self.db = self.root / "k.sqlite"; sqlite3.connect(self.db).close()
        (self.root / "docs").mkdir()
        con = R.connect(str(self.db)); R.ensure_schema(con); con.close()
        self.parsed = self.root / "parsed"; self.parsed.mkdir()
        old = self.parsed / "standup.vtt.md"; old.write_text("# SOURCE: standup.vtt\n\n## 00:01 cue")
        src = self.root / "docs" / "standup.vtt"; src.write_text("WEBVTT")
        with sqlite3.connect(self.db) as con:
            S.ensure_documents(con)
            with R.connect(str(self.db)) as reg:
                row = R.register(reg, "docs", "standup.vtt", src)
            m = S.scan(str(self.parsed))["standup.vtt.md"]
            con.execute("INSERT INTO synced_files VALUES(?,?,?,?,?,?)",
                        ("standup.vtt.md", m["sha"], m["bytes"], m["mtime"], "now", row["source_id"]))
        old.unlink()  # the transcript is now consumed by the video lane

    def tearDown(self):
        self.t.cleanup()

    def _manifest(self, entries):
        (self.parsed / "manifest.json").write_text(json.dumps(entries))

    CONSUMED = {"source": "standup.vtt", "skipped": True, "method": "consumed-by-video", "consumed_by": "standup.mp4"}
    VIDEO = {"source": "standup.mp4", "md": "standup.mp4.md", "method": "video-lane"}

    def test_superseded_when_consumed_and_video_doc_present(self):
        self._manifest([self.CONSUMED, self.VIDEO])
        (self.parsed / "standup.mp4.md").write_text("# SOURCE: standup.mp4\n")
        with sqlite3.connect(self.db) as con:
            _, d = S.delta(con, str(self.parsed))
        self.assertEqual(d["superseded_by_video"], ["standup.vtt.md"])
        self.assertIn("standup.vtt.md", d["deleted"])
        self.assertEqual(d["blocked_missing_parsed"], [])

    def test_still_blocked_when_video_doc_absent(self):
        self._manifest([self.CONSUMED, self.VIDEO])
        with sqlite3.connect(self.db) as con:
            _, d = S.delta(con, str(self.parsed))
        self.assertEqual(d["blocked_missing_parsed"], ["standup.vtt.md"])
        self.assertEqual(d["superseded_by_video"], [])

    def test_still_blocked_without_consumed_entry(self):
        self._manifest([self.VIDEO])
        (self.parsed / "standup.mp4.md").write_text("# SOURCE: standup.mp4\n")
        with sqlite3.connect(self.db) as con:
            _, d = S.delta(con, str(self.parsed))
        self.assertEqual(d["blocked_missing_parsed"], ["standup.vtt.md"])
