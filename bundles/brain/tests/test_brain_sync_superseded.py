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

    def test_still_blocked_when_video_source_is_tombstoned(self):
        # The video's own parsed doc is stale (source tombstoned) but still on disk.
        # It must not be able to "supersede" the transcript — the transcript should
        # stay blocked, not be silently lost alongside the tombstoned video doc.
        self._manifest([self.CONSUMED, self.VIDEO])
        (self.parsed / "standup.mp4.md").write_text("# SOURCE: standup.mp4\n")
        video_src = self.root / "docs" / "standup.mp4"; video_src.write_text("video-bytes")
        with sqlite3.connect(self.db) as con:
            with R.connect(str(self.db)) as reg:
                vrow = R.register(reg, "docs", "standup.mp4", video_src)
            m = S.scan(str(self.parsed))["standup.mp4.md"]
            con.execute("INSERT INTO synced_files VALUES(?,?,?,?,?,?)",
                        ("standup.mp4.md", m["sha"], m["bytes"], m["mtime"], "now", vrow["source_id"]))
            con.execute("UPDATE sources SET state='removed' WHERE source_id=?", (vrow["source_id"],))
        with sqlite3.connect(self.db) as con:
            _, d = S.delta(con, str(self.parsed))
        self.assertEqual(d["blocked_missing_parsed"], ["standup.vtt.md"])
        self.assertEqual(d["superseded_by_video"], [])


class SupersededDocxTests(unittest.TestCase):
    """A Teams .docx transcript parsed earlier as a document (soffice) is retired once
    the video lane consumes it — the file name differs from the recording's."""

    DOCX_MD = "rec__Acme_ Sync.docx.md"
    VIDEO_REL = "rec/Sync-20260105-Meeting Recording.mp4"

    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.root = Path(self.t.name)
        self.db = self.root / "k.sqlite"; sqlite3.connect(self.db).close()
        (self.root / "docs" / "rec").mkdir(parents=True)
        con = R.connect(str(self.db)); R.ensure_schema(con); con.close()
        self.parsed = self.root / "parsed"; self.parsed.mkdir()
        old = self.parsed / self.DOCX_MD
        old.write_text("# SOURCE: rec/Acme_ Sync.docx\n# method: soffice+pymupdf\n\nbody")
        src = self.root / "docs" / "rec" / "Acme_ Sync.docx"; src.write_bytes(b"PK")
        with sqlite3.connect(self.db) as con:
            S.ensure_documents(con)
            with R.connect(str(self.db)) as reg:
                row = R.register(reg, "docs", "rec/Acme_ Sync.docx", src)
            m = S.scan(str(self.parsed))[self.DOCX_MD]
            con.execute("INSERT INTO synced_files VALUES(?,?,?,?,?,?)",
                        (self.DOCX_MD, m["sha"], m["bytes"], m["mtime"], "now", row["source_id"]))
        old.unlink()  # video_capture assemble removed it when it consumed the transcript

    def tearDown(self):
        self.t.cleanup()

    def test_docx_transcript_is_superseded_by_the_video_doc(self):
        video_md = self.VIDEO_REL.replace("/", "__") + ".md"
        (self.parsed / video_md).write_text(f"# SOURCE: {self.VIDEO_REL}\n")
        (self.parsed / "manifest.json").write_text(json.dumps([
            {"source": self.VIDEO_REL, "md": video_md, "method": "video-lane",
             "inputs": [self.VIDEO_REL, "rec/Acme_ Sync.docx"]},
            {"source": "rec/Acme_ Sync.docx", "skipped": True, "method": "consumed-by-video",
             "consumed_by": self.VIDEO_REL}]))
        with sqlite3.connect(self.db) as con:
            _, d = S.delta(con, str(self.parsed))
        self.assertEqual(d["superseded_by_video"], [self.DOCX_MD])
        self.assertIn(self.DOCX_MD, d["deleted"])
        self.assertEqual(d["blocked_missing_parsed"], [])
