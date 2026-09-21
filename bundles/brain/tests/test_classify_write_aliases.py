import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxo_fixtures import tagged_store, taxonomy

CW = Path(__file__).resolve().parent.parent / "skills" / "corpus-taxonomy-extraction" / "classify_write.py"


class AliasTests(unittest.TestCase):
    def test_alias_label_resolves_and_reclassify_done_shrinks(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "k.sqlite")
            tax = taxonomy()
            tax["intent_taxonomy"]["tree"]["Billing & Payments"][1] = "Refund Requests"
            tagged_store(db, tax, {1: [], 2: []})
            c = sqlite3.connect(db)
            c.execute("CREATE TABLE graph_aliases(alias_id TEXT PRIMARY KEY, node_id TEXT)")
            c.execute("INSERT INTO graph_aliases VALUES('refunds','refund_requests')")
            c.commit()
            c.close()
            res = os.path.join(td, "cls")
            os.makedirs(res)
            json.dump({"1": ["Refunds"]}, open(os.path.join(res, "result_0.json"), "w"))
            rc_path = os.path.join(td, "reclassify.json")
            json.dump({"version": 1, "chunk_ids": [1, 2], "reasons": {"1": "x", "2": "y"}}, open(rc_path, "w"))
            r = subprocess.run([sys.executable, str(CW), "--db", db, "--results", res,
                                "--reclassify-done", rc_path], text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = sqlite3.connect(db).execute("SELECT category_id FROM chunk_topics WHERE chunk_id=1").fetchall()
            self.assertEqual(sorted(x[0] for x in rows), ["billing_payments", "refund_requests"])
            self.assertEqual(json.load(open(rc_path))["chunk_ids"], [2])
            json.dump({"2": []}, open(os.path.join(res, "result_0.json"), "w"))
            subprocess.run([sys.executable, str(CW), "--db", db, "--results", res, "--reclassify-done", rc_path],
                           check=True, capture_output=True)
            self.assertFalse(os.path.exists(rc_path))


class MergeModeTests(unittest.TestCase):
    def test_merge_adds_without_deleting(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "k.sqlite")
            tagged_store(db, taxonomy())            # chunk 2 has Refunds + Billing & Payments
            res = os.path.join(td, "cls"); os.makedirs(res)
            json.dump({"2": ["Track Delivery"]}, open(os.path.join(res, "result_0.json"), "w"))
            r = subprocess.run([sys.executable, str(CW), "--db", db, "--results", res, "--merge"],
                               text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            got = sorted(x[0] for x in sqlite3.connect(db).execute(
                "SELECT category_id FROM chunk_topics WHERE chunk_id=2"))
            self.assertEqual(got, ["billing_payments", "delivery_pickup", "refunds", "track_delivery"])
