from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "skills" / "brain-maintenance"
SPEC = importlib.util.spec_from_file_location("maintenance", HERE / "maintenance.py")
M = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(M)


class MaintenancePlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "docs").mkdir()
        (self.project / "parsed").mkdir()
        (self.project / "schema").mkdir()
        (self.project / ".runs").mkdir()
        (self.project / "docs" / "a.pdf").write_bytes(b"source")
        (self.project / "parsed" / "a.pdf.md").write_text("# A\nbody", encoding="utf-8")
        (self.project / "parsed" / "manifest.json").write_text(
            json.dumps([{"source": "a.pdf", "md": "a.pdf.md"}]), encoding="utf-8"
        )
        (self.project / "schema" / "taxonomy.json").write_text("{}\n", encoding="utf-8")
        (self.project / "brain.toml").write_text(
            'version = 1\n[sources.roots.docs]\npath = "docs"\nmode = "import"\ninclude = ["**/*.pdf"]\n',
            encoding="utf-8",
        )
        self.db = self.project / "schema" / "knowledge.sqlite"
        with sqlite3.connect(self.db) as con:
            con.executescript("""
            CREATE TABLE sources(source_id TEXT PRIMARY KEY,root_key TEXT,relative_path TEXT,source_sha256 TEXT,
              byte_size INTEGER,media_type TEXT,source_kind TEXT,display_name TEXT,description TEXT,
              provenance_json TEXT,state TEXT,created_at TEXT,updated_at TEXT,last_seen_at TEXT,removed_at TEXT,
              UNIQUE(root_key,relative_path));
            CREATE TABLE synced_files(doc_id TEXT PRIMARY KEY,sha TEXT,bytes INT,mtime REAL,updated_at TEXT,source_id TEXT);
            CREATE TABLE chunks(id INTEGER PRIMARY KEY,source TEXT);
            CREATE TABLE chunk_topics(chunk_id INT);
            CREATE TABLE facts(value REAL);
            CREATE TABLE related(chunk_id INT,related_id INT,score REAL);
            """)
            source_sha = M.sha_file(self.project / "docs" / "a.pdf")
            parsed_sha = M.sha_file(self.project / "parsed" / "a.pdf.md")
            con.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("s1","docs","a.pdf",source_sha,6,"application/pdf","narrative","a.pdf",None,"{}","active","now","now","now",None))
            st = (self.project / "parsed" / "a.pdf.md").stat()
            con.execute("INSERT INTO synced_files VALUES(?,?,?,?,?,?)",("a.pdf.md",parsed_sha,st.st_size,st.st_mtime,"now","s1"))
        self.profile = self.project / "brain-maintenance.toml"
        self.profile.write_text('''version = 1
[project]
root = "."
[paths]
brain_config = "brain.toml"
db = "schema/knowledge.sqlite"
parsed = "parsed"
manifest = "parsed/manifest.json"
assets = "assets"
taxonomy = "schema/taxonomy.json"
runs = ".runs"
[update]
root_key = "docs"
[safety]
require_strict_sources = true
require_snapshot = true
allow_legacy_unlinked_delete = false
max_deleted_docs = 0
[verification]
required_lanes = []
expected_tools = ["list_metrics", "get_metric", "search_knowledge", "get_taxonomy", "find_related_content", "get_evidence", "health"]
[deployment]
enabled = false
''', encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_clean_plan_is_read_only(self):
        before = {p.relative_to(self.project).as_posix(): M.sha_file(p) for p in self.project.rglob("*") if p.is_file()}
        profile = M.load_profile(self.profile)
        report = M.build_status(profile)
        after = {p.relative_to(self.project).as_posix(): M.sha_file(p) for p in self.project.rglob("*") if p.is_file()}
        self.assertTrue(report["ready"])
        self.assertEqual(report["source_action_counts"], {})
        self.assertEqual(report["parsed_delta"]["unchanged"], ["a.pdf.md"])
        self.assertEqual(before, after)

    def test_status_reports_taxonomy_review_state(self):
        tdir = self.project / "schema"
        (tdir / "reviews").mkdir()
        (tdir / "reviews" / "review_r-1.json").write_text('{"review_id": "r-1"}', encoding="utf-8")
        (tdir / "decisions.jsonl").write_text('{"review_id":"r-1","action":"submit"}\n', encoding="utf-8")
        (tdir / "work").mkdir()
        (tdir / "work" / "reclassify.json").write_text('{"chunk_ids":[1,2,3],"reasons":{}}', encoding="utf-8")
        report = M.build_status(M.load_profile(self.profile))
        self.assertEqual(report["taxonomy_review"], {"current_json": False, "latest_review": "r-1",
                                                     "submitted_unapplied": ["r-1"], "pending_reclassify": 3})

    def test_required_empty_lane_blocks(self):
        self.profile.write_text(self.profile.read_text().replace("required_lanes = []", "required_lanes = [\"narrative\"]"))
        report = M.build_status(M.load_profile(self.profile))
        self.assertFalse(report["ready_to_stage"])
        self.assertIn("required_lane_empty", report["blockers"])
        self.assertEqual(report["empty_required_lanes"], ["narrative"])

    def test_import_missing_is_reported_but_not_a_delete(self):
        (self.project / "docs" / "a.pdf").unlink()
        report = M.build_status(M.load_profile(self.profile))
        self.assertTrue(report["ready"])
        self.assertEqual(report["source_action_counts"], {"missing": 1})
        self.assertEqual(report["parsed_delta"]["deleted"], [])

    def test_manifest_traversal_blocks(self):
        (self.project / "parsed" / "manifest.json").write_text(
            json.dumps([{"source": "../a.pdf", "md": "a.pdf.md"}]), encoding="utf-8"
        )
        report = M.build_status(M.load_profile(self.profile))
        self.assertFalse(report["ready"])
        self.assertIn("manifest_invalid", report["blockers"])

    def test_tombstoned_source_is_reported_not_crashed(self):
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE sources SET state='removed' WHERE source_id='s1'")
        report = M.build_status(M.load_profile(self.profile))
        self.assertIn("a.pdf.md", report["parsed_delta"]["deleted"])
        self.assertFalse(report["ready_to_apply"])

    def test_enabled_deployment_requires_executable_adapter_and_profile(self):
        text = self.profile.read_text().replace("enabled = false", "enabled = true\nadapter = \"ops/deploy\"\nprofile = \"ops/deploy.toml\"\nsecret_env = [\"BRAIN_API_KEY\"]\nrequire_immutable_version = true")
        self.profile.write_text(text)
        with self.assertRaisesRegex(ValueError, "executable"):
            M.load_profile(self.profile)
        ops = self.project / "ops"; ops.mkdir()
        adapter = ops / "deploy"; adapter.write_text("#!/bin/sh\n"); adapter.chmod(0o755)
        (ops / "deploy.toml").write_text("version=1\n")
        loaded = M.load_profile(self.profile)
        self.assertEqual(loaded["deployment_paths"]["adapter"], adapter.resolve())

    def test_profile_path_escape_rejected(self):
        text = self.profile.read_text().replace('db = "schema/knowledge.sqlite"', 'db = "../outside.sqlite"')
        self.profile.write_text(text)
        with self.assertRaisesRegex(ValueError, "inside project"):
            M.load_profile(self.profile)

    def test_credential_field_rejected(self):
        self.profile.write_text(self.profile.read_text() + 'api_key = "secret"\n')
        with self.assertRaisesRegex(ValueError, "credential"):
            M.load_profile(self.profile)

    def test_documented_status_cli_and_output_confinement(self):
        out = self.project / ".runs" / "status.json"
        rc = M.main(["status", "--profile", str(self.profile), "--out", str(out)])
        self.assertEqual(rc, 0)
        self.assertTrue(out.is_file())
        report = json.loads(out.read_text())
        self.assertTrue(report["ready_to_stage"])
        self.assertFalse(report["ready_to_apply"])
        self.assertFalse(report["ready_to_deploy"])
        outside = self.project.parent / "outside.json"
        rc = M.main(["status", "--profile", str(self.profile), "--out", str(outside)])
        self.assertEqual(rc, 2)
        self.assertFalse(outside.exists())

    def test_extra_parsed_document_becomes_structured_blocker(self):
        (self.project / "parsed" / "extra.md").write_text("# extra")
        report = M.build_status(M.load_profile(self.profile))
        self.assertFalse(report["ready_to_stage"])
        self.assertIn("strict_source_validation_failed", report["blockers"])
        self.assertIn("missing from manifest", report["strict_source_error"])

    def test_unknown_nested_profile_field_rejected(self):
        self.profile.write_text(self.profile.read_text().replace("[safety]", "[classification]\nunknown = true\n[safety]"))
        with self.assertRaisesRegex(ValueError, r"unknown \[classification\]"):
            M.load_profile(self.profile)

    def test_ambiguous_same_hash_move_blocks(self):
        original = self.project / "docs" / "a.pdf"
        payload = original.read_bytes()
        original.unlink()
        (self.project / "docs" / "b.pdf").write_bytes(payload)
        (self.project / "docs" / "c.pdf").write_bytes(payload)
        report = M.build_status(M.load_profile(self.profile))
        self.assertFalse(report["ready_to_stage"])
        self.assertIn("ambiguous_source_move", report["blockers"])
        self.assertTrue(report["ambiguous_move_hashes"])

    def test_content_change_requires_semantic_work_before_apply(self):
        (self.project / "docs" / "a.pdf").write_bytes(b"changed")
        report = M.build_status(M.load_profile(self.profile))
        self.assertTrue(report["ready_to_stage"])
        self.assertFalse(report["ready_to_apply"])
        self.assertTrue(report["narrative_work"])
        self.assertTrue(report["classification"]["required_after_apply"])
        self.assertEqual(report["next_gate"], "process narrative work in staging")


if __name__ == "__main__":
    unittest.main()
