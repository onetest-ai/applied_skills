import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import graph_migrate as GM
import taxo_ops as O
from taxo_fixtures import tag_rows, tagged_store, taxonomy, write_json

BUILD = Path(__file__).resolve().parent.parent / "skills" / "corpus-taxonomy-extraction" / "build_graph.py"

CHANGES = [{"type": "merge", "from": "Billing & Payments Admin", "into": "Billing & Payments"},
           {"type": "rename", "node": "Refunds", "new_name": "Refund Requests"},
           {"type": "remove", "node": "Transform", "disposition": "demote", "reason": "phase, not intent"},
           {"type": "move", "node": "Proof of Delivery", "new_parent": "Billing & Payments"}]


def v1_from(v0, ops):
    t, migs, applied = O.apply_ops(v0, ops)
    t["version"] = 1
    t.setdefault("history", []).append({"version": 1, "ops": applied, "migrations": migs})
    return t


def build(tax_path, db, *extra):
    return subprocess.run([sys.executable, str(BUILD), "--taxonomy", tax_path, "--db", db, *extra],
                          text=True, capture_output=True)


class RunTests(unittest.TestCase):
    def test_run_merge_dedups_and_counts(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "k.sqlite")
            tagged_store(db, taxonomy())
            _, migs, _ = O.apply_ops(taxonomy(), [CHANGES[0]])
            c = sqlite3.connect(db)
            reclass, explained, stats = GM.run(c, migs)
            c.commit()
            self.assertEqual(stats, {"tags_repointed": 1, "tags_deleted": 0, "tags_deduplicated": 1})
            self.assertEqual(reclass, {})
            self.assertEqual(explained, {"billing_payments_admin"})
            ids = {cat for _, cat in tag_rows(db)}
            self.assertNotIn("billing_payments_admin", ids)
            about = c.execute("SELECT COUNT(*) FROM graph_edges WHERE rel='about' AND target='billing_payments_admin'")
            self.assertEqual(about.fetchone()[0], 0)

    def test_reclassify_collected_before_delete(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "k.sqlite")
            tagged_store(db, taxonomy())
            _, migs, _ = O.apply_ops(taxonomy(), [CHANGES[2]])
            c = sqlite3.connect(db)
            reclass, _, stats = GM.run(c, migs)
            self.assertEqual(list(reclass), [7])
            self.assertEqual(stats["tags_deleted"], 1)


class BuildGraphMigrationTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tax_dir = os.path.join(self.td.name, "taxonomy")
        self.db = os.path.join(self.td.name, "k.sqlite")
        v0 = taxonomy(version=0)
        tagged_store(self.db, v0)
        c = sqlite3.connect(self.db)
        GM.write_version(c, 0, "sha-v0")
        c.commit()
        c.close()
        self.v1 = v1_from(v0, CHANGES)
        write_json(os.path.join(self.tax_dir, "taxonomy_v1.json"), self.v1)
        write_json(os.path.join(self.tax_dir, "current.json"), self.v1)
        self.cur = os.path.join(self.tax_dir, "current.json")

    def tearDown(self):
        self.td.cleanup()

    def test_migrates_tags_writes_reclassify_aliases_meta(self):
        r = build(self.cur, self.db)
        self.assertEqual(r.returncode, 0, r.stderr)
        ids = {cat for _, cat in tag_rows(self.db)}
        self.assertNotIn("billing_payments_admin", ids)
        self.assertNotIn("transform", ids)
        self.assertEqual(sorted(cid for cid, cat in tag_rows(self.db) if cat == "refund_requests"), [2, 4])
        rc = json.load(open(os.path.join(self.tax_dir, "work", "reclassify.json")))
        self.assertEqual(rc["chunk_ids"], [6, 7])
        c = sqlite3.connect(self.db)
        self.assertEqual(GM.read_version(c), 1)
        aliases = dict(c.execute("SELECT alias_id, node_id FROM graph_aliases"))
        self.assertEqual(aliases, {"billing_payments_admin": "billing_payments", "refunds": "refund_requests"})

    def test_rerun_is_a_noop(self):
        build(self.cur, self.db)
        before = tag_rows(self.db)
        r = build(self.cur, self.db)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tag_rows(self.db), before)

    def test_legacy_store_with_op_history_refuses(self):
        c = sqlite3.connect(self.db)
        c.execute("DELETE FROM meta")
        c.commit()
        c.close()
        r = build(self.cur, self.db)
        self.assertEqual(r.returncode, 2)
        self.assertIn("adopt", r.stderr)
        self.assertIn("--meta-only", r.stderr)

    def test_malformed_reclassify_file_aborts_and_changes_nothing(self):
        before = tag_rows(self.db)
        rc_path = os.path.join(self.tax_dir, "work", "reclassify.json")
        os.makedirs(os.path.dirname(rc_path), exist_ok=True)
        with open(rc_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        r = build(self.cur, self.db)
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tag_rows(self.db), before)
        c = sqlite3.connect(self.db)
        self.assertEqual(GM.read_version(c), 0)


class WriteReclassifyTests(unittest.TestCase):
    def test_merges_into_existing_file_and_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "work", "reclassify.json")
            GM.write_reclassify(path, 1, {5: "moved"})
            GM.write_reclassify(path, 2, {6: "removed", 5: "ignored, already queued"})
            data = json.load(open(path))
            self.assertEqual(data["chunk_ids"], [5, 6])
            self.assertEqual(data["reasons"]["5"], "moved")
            self.assertEqual(data["reasons"]["6"], "removed")
            self.assertEqual(data["version"], 2)
            leftovers = [f for f in os.listdir(os.path.dirname(path)) if f != "reclassify.json"]
            self.assertEqual(leftovers, [])

    def test_malformed_existing_file_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "reclassify.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("not json")
            with self.assertRaises(ValueError) as ctx:
                GM.write_reclassify(path, 1, {5: "moved"})
            self.assertIn(path, str(ctx.exception))


class DriftGuardTests(unittest.TestCase):
    """Legacy shape: v1 = v0 + L2s (legacy add-only history, no migrations), store built from v1."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tax_dir = os.path.join(self.td.name, "taxonomy")
        self.db = os.path.join(self.td.name, "k.sqlite")
        v0 = taxonomy(version=0)
        v1 = taxonomy(version=1)
        v1["intent_taxonomy"]["tree"]["Billing & Payments"].append("Payment Plans")
        v1["intent_taxonomy"]["tree"]["Delivery & Pickup"].append("POD Disputes")
        v1["history"] = [{"added_l1": [], "added_l2": [["Payment Plans", "Billing & Payments"],
                                                        ["POD Disputes", "Delivery & Pickup"]]}]
        tags = {1: ["Payment Plans"], 2: ["Refunds"]}
        tagged_store(self.db, v1, tags)
        write_json(os.path.join(self.tax_dir, "taxonomy_v0.json"), v0)
        write_json(os.path.join(self.tax_dir, "taxonomy_v1.json"), v1)
        write_json(os.path.join(self.tax_dir, "current.json"), v1)

    def tearDown(self):
        self.td.cleanup()

    def test_stale_version_refuses_and_changes_nothing(self):
        before = tag_rows(self.db)
        r = build(os.path.join(self.tax_dir, "taxonomy_v0.json"), self.db)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("2 node(s)", r.stderr)
        self.assertIn("1 tag(s)", r.stderr)
        self.assertEqual(tag_rows(self.db), before)

    def test_stale_file_outside_taxonomy_dir_refused_by_store_version(self):
        c = sqlite3.connect(self.db)
        GM.write_version(c, 1, "sha-v1")
        c.commit()
        c.close()
        stale = os.path.join(self.td.name, "elsewhere", "old.json")   # no sibling current.json
        write_json(stale, taxonomy(version=0))
        before = tag_rows(self.db)
        r = build(stale, self.db)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("store is already at taxonomy version 1", r.stderr)
        self.assertEqual(tag_rows(self.db), before)
        c = sqlite3.connect(self.db)
        self.assertEqual(GM.read_version(c), 1)
        self.assertIn("payment_plans", {row[0] for row in c.execute("SELECT id FROM graph_nodes")})
        c.close()
        r = build(stale, self.db, "--yes-prune")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_yes_prune_overrides(self):
        r = build(os.path.join(self.tax_dir, "taxonomy_v0.json"), self.db, "--yes-prune")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("payment_plans", {cat for _, cat in tag_rows(self.db)})

    def test_hand_edited_current_warns_but_builds(self):
        cur = json.load(open(os.path.join(self.tax_dir, "current.json")))
        cur["intent_taxonomy"]["tree"]["Delivery & Pickup"].remove("POD Disputes")
        write_json(os.path.join(self.tax_dir, "current.json"), cur)
        r = build(os.path.join(self.tax_dir, "current.json"), self.db)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("vanished without a review op", r.stderr)
