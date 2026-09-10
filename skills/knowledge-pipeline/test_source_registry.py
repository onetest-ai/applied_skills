from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("source_registry", HERE / "source_registry.py")
R = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(R)

SYNC_SPEC = importlib.util.spec_from_file_location("brain_sync", HERE / "brain_sync.py")
S = importlib.util.module_from_spec(SYNC_SPEC)
assert SYNC_SPEC.loader
SYNC_SPEC.loader.exec_module(S)


class SourceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "schema" / "knowledge.sqlite"
        self.db.parent.mkdir()
        sqlite3.connect(self.db).close()
        (self.root / ".incoming").mkdir()
        (self.root / "docs").mkdir()
        self.config = self.root / "brain.toml"
        self.config.write_text('''version = 1
[paths]
parsed = "parsed"
[sources.roots.incoming]
path = ".incoming"
mode = "managed"
include = ["**/*"]
[sources.roots.docs]
path = "docs"
mode = "import"
include = ["**/*.pdf"]
''')
        con = R.connect(str(self.db))
        try:
            R.ensure_schema(con)
        finally:
            con.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_relative_roots_survive_project_move_and_registry_has_no_blobs(self):
        cfg = R.load_config(self.config)
        self.assertEqual(cfg["roots"]["docs"]["path"], (self.root / "docs").resolve())
        with sqlite3.connect(self.db) as con:
            cols = {r[1]: r[2].upper() for r in con.execute("PRAGMA table_info(sources)")}
        self.assertNotIn("BLOB", cols.values())
        moved = self.root / "nested"
        moved.mkdir()
        moved_cfg = moved / "brain.toml"
        moved_cfg.write_text(self.config.read_text().replace('path = "docs"', 'path = "../docs"').replace('path = ".incoming"', 'path = "../.incoming"'))
        self.assertEqual(R.load_config(moved_cfg)["roots"]["docs"]["path"], (self.root / "docs").resolve())

    def test_adopt_and_plan_import_missing_never_deletes(self):
        src = self.root / "docs" / "über report.pdf"
        src.write_bytes(b"version one")
        with R.connect(str(self.db)) as con:
            row = R.register(con, "docs", "über report.pdf", src, description="A report")
            clean = R.build_plan(con, R.load_config(self.config))
        self.assertEqual(clean["actions"], [])
        src.unlink()
        with R.connect_readonly(str(self.db)) as con:
            plan = R.build_plan(con, R.load_config(self.config))
        action = next(x for x in plan["actions"] if x["source_id"] == row["source_id"])
        self.assertEqual(action["action"], "missing")

    def test_attachment_import_copies_into_managed_root_without_db_blob(self):
        attachment = self.root / "My upload.pdf"
        payload = b"private attachment bytes 123"
        attachment.write_bytes(payload)
        args = Namespace(db=str(self.db), config=str(self.config), attachment=str(attachment), root="incoming",
                         source_id=None, kind="narrative", name=None, description="chat upload",
                         provenance='{"attachment_id":"a-1"}')
        R.cmd_import(args)
        with sqlite3.connect(self.db) as con:
            row = con.execute("SELECT root_key,relative_path,source_sha256,provenance_json FROM sources").fetchone()
        self.assertEqual(row[0], "incoming")
        self.assertTrue((self.root / ".incoming" / row[1]).is_file())
        self.assertEqual(row[2], R.sha_file(attachment))
        self.assertEqual(json.loads(row[3])["attachment_id"], "a-1")
        self.assertNotIn(payload, self.db.read_bytes())

    def test_plan_is_read_only_and_missing_db_is_not_created(self):
        missing = self.root / "missing.sqlite"
        rc = R.main(["--db", str(missing), "--config", str(self.config), "plan"])
        self.assertEqual(rc, 2)
        self.assertFalse(missing.exists())

    def test_stale_content_plan_is_rejected_without_mutation(self):
        src = self.root / "docs" / "a.pdf"; src.write_bytes(b"one")
        with R.connect(str(self.db)) as con:
            row = R.register(con, "docs", "a.pdf", src)
        src.write_bytes(b"two")
        with R.connect_readonly(str(self.db)) as con:
            plan = R.build_plan(con, R.load_config(self.config))
        # Change registry state after the plan; apply must reject its old precondition.
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE sources SET source_sha256=? WHERE source_id=?", ("0" * 64, row["source_id"]))
            con.commit()
        plan_path = self.root / "plan.json"; plan_path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, "stale content_change"):
            R.cmd_apply(Namespace(db=str(self.db), config=str(self.config), plan=str(plan_path)))
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute("SELECT source_sha256 FROM sources WHERE source_id=?", (row["source_id"],)).fetchone()[0], "0" * 64)

    def test_remove_rejects_changed_managed_file(self):
        attachment = self.root / "upload.pdf"; attachment.write_bytes(b"original")
        args = Namespace(db=str(self.db), config=str(self.config), attachment=str(attachment), root="incoming",
                         source_id=None, kind="narrative", name=None, description=None, provenance=None)
        R.cmd_import(args)
        with sqlite3.connect(self.db) as con:
            row = con.execute("SELECT source_id,relative_path FROM sources").fetchone()
        (self.root / ".incoming" / row[1]).write_bytes(b"replacement")
        with self.assertRaisesRegex(RuntimeError, "changed"):
            R.cmd_remove(Namespace(db=str(self.db), config=str(self.config), source_id=[row[0]], yes=True, delete_managed_file=True))
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute("SELECT state FROM sources").fetchone()[0], "active")

    def test_remove_requires_confirmation_and_tombstones(self):
        src = self.root / "docs" / "a.pdf"; src.write_bytes(b"a")
        with R.connect(str(self.db)) as con:
            row = R.register(con, "docs", "a.pdf", src)
        base = dict(db=str(self.db), config=str(self.config), source_id=[row["source_id"]], delete_managed_file=False)
        with self.assertRaisesRegex(RuntimeError, "confirmation"):
            R.cmd_remove(Namespace(**base, yes=False))
        R.cmd_remove(Namespace(**base, yes=True))
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute("SELECT state FROM sources").fetchone()[0], "removed")
        self.assertTrue(src.exists())

    def test_brain_sync_blocks_missing_parsed_for_active_registered_source(self):
        parsed = self.root / "parsed"; parsed.mkdir()
        md = parsed / "a.pdf.md"; md.write_text("# A\nbody")
        src = self.root / "docs" / "a.pdf"; src.write_bytes(b"a")
        with sqlite3.connect(self.db) as con:
            S.ensure_documents(con)
            with R.connect(str(self.db)) as registry:
                row = R.register(registry, "docs", "a.pdf", src)
            meta = S.scan(str(parsed))["a.pdf.md"]
            con.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", ("a.pdf.md", meta["sha"], meta["bytes"], meta["mtime"], "now", row["source_id"]))
            con.commit()
            md.unlink()
            _, delta = S.delta(con, str(parsed))
            self.assertEqual(delta["blocked_missing_parsed"], ["a.pdf.md"])
            self.assertEqual(delta["deleted"], [])
            con.execute("UPDATE sources SET state='removed' WHERE source_id=?", (row["source_id"],))
            con.commit()
            _, delta = S.delta(con, str(parsed))
            self.assertEqual(delta["deleted"], ["a.pdf.md"])

    def test_brain_sync_plan_is_read_only(self):
        parsed = self.root / "parsed"; parsed.mkdir()
        (parsed / "a.md").write_text("# A\nbody")
        with sqlite3.connect(self.db) as con:
            S.ensure_documents(con)
            m = S.scan(str(parsed))["a.md"]
            con.execute("INSERT INTO documents(doc_id,sha,bytes,mtime,updated_at,source_id) VALUES(?,?,?,?,?,NULL)",
                        ("a.md", m["sha"], m["bytes"], m["mtime"], "now"))
            con.commit()
        before = self.db.read_bytes()
        S.cmd_plan(Namespace(db=str(self.db), parsed=str(parsed), manifest=None, root_key=None, strict_sources=False))
        self.assertEqual(self.db.read_bytes(), before)

    def test_strict_sources_rejects_extra_parsed_document(self):
        parsed = self.root / "parsed"; parsed.mkdir()
        (parsed / "known.md").write_text("known")
        (parsed / "extra.md").write_text("extra")
        manifest = parsed / "manifest.json"
        manifest.write_text(json.dumps([{"source": "known.pdf", "md": "known.md"}]))
        src = self.root / "docs" / "known.pdf"; src.write_bytes(b"known")
        with sqlite3.connect(self.db) as con:
            with R.connect(str(self.db)) as registry:
                R.register(registry, "docs", "known.pdf", src)
            with self.assertRaisesRegex(ValueError, "missing from manifest"):
                S.source_ids(con, str(parsed), str(manifest), "docs", True)

    def test_tombstone_deletes_even_when_stale_parsed_file_remains(self):
        parsed = self.root / "parsed"; parsed.mkdir()
        md = parsed / "a.pdf.md"; md.write_text("# A\nbody")
        src = self.root / "docs" / "a.pdf"; src.write_bytes(b"a")
        with sqlite3.connect(self.db) as con:
            S.ensure_documents(con)
            with R.connect(str(self.db)) as registry:
                row = R.register(registry, "docs", "a.pdf", src)
            meta = S.scan(str(parsed))["a.pdf.md"]
            con.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", ("a.pdf.md", meta["sha"], meta["bytes"], meta["mtime"], "now", row["source_id"]))
            con.execute("UPDATE sources SET state='removed' WHERE source_id=?", (row["source_id"],))
            con.commit()
            now, delta = S.delta(con, str(parsed))
            self.assertNotIn("a.pdf.md", now)
            self.assertEqual(delta["deleted"], ["a.pdf.md"])

    def test_strict_sources_accepts_linked_tombstone_for_deletion(self):
        parsed = self.root / "parsed"; parsed.mkdir()
        md = parsed / "a.pdf.md"; md.write_text("# A\nbody")
        manifest = parsed / "manifest.json"
        manifest.write_text(json.dumps([{"source": "a.pdf", "md": "a.pdf.md"}]))
        src = self.root / "docs" / "a.pdf"; src.write_bytes(b"a")
        with sqlite3.connect(self.db) as con:
            S.ensure_documents(con)
            with R.connect(str(self.db)) as registry:
                row = R.register(registry, "docs", "a.pdf", src)
            meta = S.scan(str(parsed))["a.pdf.md"]
            con.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", ("a.pdf.md", meta["sha"], meta["bytes"], meta["mtime"], "now", row["source_id"]))
            con.execute("UPDATE sources SET state='removed' WHERE source_id=?", (row["source_id"],))
            con.commit()
            links, unmanaged = S.source_ids(con, str(parsed), str(manifest), "docs", True)
            self.assertEqual(links["a.pdf.md"], row["source_id"])
            self.assertEqual(unmanaged, [])

    def test_manifest_migration_links_document_without_changing_doc_id(self):
        src = self.root / "docs" / "a.pdf"; src.write_bytes(b"a")
        parsed = self.root / "parsed"; parsed.mkdir()
        (parsed / "a.pdf.md").write_text("# A")
        manifest = parsed / "manifest.json"
        manifest.write_text(json.dumps([{"source": "a.pdf", "md": "a.pdf.md"}]))
        with sqlite3.connect(self.db) as con:
            S.ensure_documents(con)
            m = S.scan(str(parsed))["a.pdf.md"]
            con.execute("INSERT INTO documents(doc_id,sha,bytes,mtime,updated_at,source_id) VALUES(?,?,?,?,?,NULL)", ("a.pdf.md", m["sha"], m["bytes"], m["mtime"], "now"))
            con.commit()
        R.cmd_migrate(Namespace(db=str(self.db), config=str(self.config), root="docs", manifest=str(manifest)))
        with sqlite3.connect(self.db) as con:
            doc = con.execute("SELECT doc_id,source_id FROM documents").fetchone()
            self.assertEqual(doc[0], "a.pdf.md")
            self.assertIsNotNone(doc[1])


if __name__ == "__main__":
    unittest.main()
