"""page_render is prompt-versioned per medium: a video prompt change must reach cached frames,
while deck pages transcribed under the unchanged document prompt stay cached."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import video_capture as V
import vision_assemble as VA
import vision_prep as VPm

VP = Path(__file__).resolve().parent.parent / "skills" / "visual-parse" / "vision_prep.py"


def _legacy_db(path: Path, rows):
    """A page_render table as main wrote it: no prompt_v column."""
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE page_render(img_sha TEXT PRIMARY KEY, doc TEXT, page INT, md TEXT)")
    c.executemany("INSERT INTO page_render VALUES(?,?,?,?)", rows)
    c.commit(); c.close()


def _render_dir(root: Path, slug: str, medium: str | None) -> Path:
    d = root / "assets" / slug; d.mkdir(parents=True)
    pages = []
    for n in (1, 2):
        (d / f"p{n:02d}.png").write_bytes(b"png")
        pages.append({"page": n, "image": f"{slug}/p{n:02d}.png", "img_sha": f"{slug}-{n}", "flagged": True})
    doc = {"doc": slug, "slug": slug, "pages": pages}
    if medium:
        doc["medium"] = medium
    (d / "pages.json").write_text(json.dumps(doc))
    return d


def _prep_items(render_dir: Path, db: Path, out: Path):
    subprocess.run([sys.executable, str(VP), "--render-dir", str(render_dir), "--out", str(out), "--db", str(db)],
                   check=True, capture_output=True, text=True)
    return [i for p in sorted(out.glob("batch_*.json")) for i in json.loads(p.read_text())]


class PrepCacheTests(unittest.TestCase):
    def test_video_prompt_is_newer_than_document_prompt(self):
        self.assertGreater(VPm.PROMPT_VERSION["video"], VPm.PROMPT_VERSION["document"])

    def test_legacy_rows_stay_cached_for_documents(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); db = root / "k.sqlite"
            _legacy_db(db, [("deck-1", "deck", 1, "# A"), ("deck-2", "deck", 2, "# B")])
            self.assertEqual(_prep_items(_render_dir(root, "deck", None), db, root / "v"), [])

    def test_legacy_rows_are_stale_for_video(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); db = root / "k.sqlite"
            _legacy_db(db, [("rec-1", "rec", 1, "# A"), ("rec-2", "rec", 2, "# B")])
            items = _prep_items(_render_dir(root, "rec", "video"), db, root / "v")
        self.assertEqual(sorted(i["img_sha"] for i in items), ["rec-1", "rec-2"])

    def test_rows_at_the_current_video_version_are_cached(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); db = root / "k.sqlite"
            _legacy_db(db, [])
            VA.persist_page_render(str(db), {"doc": "rec", "medium": "video",
                                             "pages": [{"page": 1, "img_sha": "rec-1"}, {"page": 2, "img_sha": "rec-2"}]},
                                   {"rec-1": "# A", "rec-2": "# B"})
            self.assertEqual(_prep_items(_render_dir(root, "rec", "video"), db, root / "v"), [])


class PersistTests(unittest.TestCase):
    def test_persist_adds_the_column_and_records_the_medium_version(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "k.sqlite"
            _legacy_db(db, [("old", "deck", 1, "# old")])
            VA.persist_page_render(str(db), {"doc": "rec", "medium": "video", "pages": [{"page": 1, "img_sha": "s"}]},
                                   {"s": "# new"})
            rows = dict(sqlite3.connect(db).execute("SELECT img_sha, prompt_v FROM page_render").fetchall())
        self.assertEqual(rows, {"old": None, "s": VPm.PROMPT_VERSION["video"]})

    def test_results_of_other_documents_are_not_stamped_with_this_documents_version(self):
        # a shared run dir holds deck results too; stamping them video-v2 would make the deck
        # pages look stale (re-transcribed), and old video results would look current
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "k.sqlite"
            _legacy_db(db, [("deck-1", "deck", 1, "# deck")])
            VA.persist_page_render(str(db), {"doc": "rec", "medium": "video", "pages": [{"page": 1, "img_sha": "v"}]},
                                   {"v": "# video", "deck-1": "# deck again", "stray": "# stray"})
            rows = dict(sqlite3.connect(db).execute("SELECT img_sha, prompt_v FROM page_render").fetchall())
        self.assertEqual(rows, {"deck-1": None, "v": VPm.PROMPT_VERSION["video"]})

    def test_load_cache_filters_by_medium_version(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "k.sqlite"
            _legacy_db(db, [("legacy", "x", 1, "# legacy")])
            VA.persist_page_render(str(db), {"doc": "rec", "medium": "video", "pages": [{"page": 1, "img_sha": "v"}]},
                                   {"v": "# video"})
            self.assertEqual(VA.load_cache(str(db), "video"), {"v": "# video"})
            self.assertEqual(VA.load_cache(str(db), "document"), {"legacy": "# legacy"})
            self.assertEqual(set(VA.load_cache(str(db))), {"legacy", "v"})


class AssembleCacheTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); r = Path(self.t.name)
        self.slug = "rec__standup--mp4"
        self.rd = r / "assets" / self.slug; self.rd.mkdir(parents=True)
        pages = []
        for n in (1, 2):
            (self.rd / f"p{n:02d}.png").write_bytes(b"png")
            pages.append({"page": n, "image": f"{self.slug}/p{n:02d}.png", "img_sha": f"sha{n}", "flagged": True,
                          "t_start": n * 10, "t_end": n * 10 + 5, "shown_at": [[n * 10, n * 10 + 5]]})
        (self.rd / "pages.json").write_text(json.dumps({"doc": "standup.mp4", "slug": self.slug, "medium": "video",
                                                        "duration": 60.0, "pages": pages}))
        self.probe = r / "probe.json"
        self.probe.write_text(json.dumps({"source": "rec/standup.mp4", "transcript": "none", "sidecar": None,
                                          "sidecar_source": None, "warnings": []}))
        self.parsed = r / "parsed"; self.parsed.mkdir()
        self.db = r / "k.sqlite"

    def tearDown(self):
        self.t.cleanup()

    def _assemble(self):
        return V.main(["assemble", "--probe", str(self.probe), "--render-dir", str(self.rd),
                       "--parsed", str(self.parsed), "--db", str(self.db)])

    def test_stale_video_rows_are_not_assembled(self):
        _legacy_db(self.db, [("sha1", "standup.mp4", 1, "# Old A"), ("sha2", "standup.mp4", 2, "# Old B")])
        self.assertEqual(self._assemble(), 1)  # refuses: no current-version result
        self.assertFalse((self.parsed / "rec__standup.mp4.md").exists())

    def test_current_video_rows_are_assembled(self):
        _legacy_db(self.db, [])
        VA.persist_page_render(str(self.db), json.loads((self.rd / "pages.json").read_text()),
                               {"sha1": "# New A", "sha2": "# New B"})
        self.assertEqual(self._assemble(), 0)
        self.assertIn("New A", (self.parsed / "rec__standup.mp4.md").read_text())


if __name__ == "__main__":
    unittest.main()
