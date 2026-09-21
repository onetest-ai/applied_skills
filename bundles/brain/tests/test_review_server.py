import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone

import decisions as D
import review_server as S
import taxonomy_review as R
from taxo_fixtures import tagged_store, taxonomy, write_json

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        d = os.path.join(self.td.name, "taxonomy")
        cur = os.path.join(d, "current.json")
        write_json(cur, taxonomy(version=1))
        self.db = os.path.join(self.td.name, "k.sqlite")
        tagged_store(self.db, taxonomy(version=1))
        self.path, _ = R.build_plan("browse", cur, now=NOW)
        self.dec = os.path.join(d, "decisions.jsonl")
        self.app = S.ReviewApp(self.path, db_path=self.db, reviewer="Pat")
        self.codes = []
        self.t = threading.Thread(target=lambda: self.codes.append(
            S.serve(self.app, open_browser=False, timeout=10, out=open(os.devnull, "w"), err=open(os.devnull, "w"))))
        self.t.start()
        self.assertTrue(self.app.ready.wait(5))
        self.base = self.app.url.split("/?")[0]

    def tearDown(self):
        if self.t.is_alive():
            self.req("POST", "/api/cancel", {})
        self.t.join(5)
        self.td.cleanup()

    def req(self, method, path, body=None, token=True):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method)
        if token:
            r.add_header("X-Review-Token", self.app.token)
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_token_required(self):
        self.assertEqual(self.req("GET", "/api/state", token=False)[0], 403)

    def test_state_counts_and_node(self):
        code, st = self.req("GET", "/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(st["counts"]["refunds"], 2)
        self.assertEqual(st["totals"], {"chunks": 8, "tagged": 7, "untagged": 1})
        code, node = self.req("GET", "/api/node/Refunds")
        self.assertEqual((node["level"], node["parent"], node["tags"]), ("L2", "Billing & Payments", 2))
        self.assertEqual({s["label"]: s["overlap"] for s in node["siblings"]}, {"Duplicate Charge": 0})

    def test_impact_does_not_record(self):
        op = {"type": "merge", "from": "Billing & Payments Admin", "into": "Billing & Payments"}
        code, out = self.req("POST", "/api/impact", {"op": op})
        self.assertEqual((code, out["impact"]["tags_repointed"]), (200, 1))
        self.assertFalse(os.path.exists(self.dec))

    def test_decide_propose_structural_and_submit_exits_zero(self):
        op = {"type": "rename", "node": "Refunds", "new_name": "Refund Requests"}
        code, out = self.req("POST", "/api/decision", {"action": "propose", "op": op})
        self.assertEqual(code, 200, out)
        rec = D.read(self.dec)[0]
        self.assertEqual((rec["surface"], rec["reviewer"], rec["impact"]["tags_repointed"]), ("browser", "Pat", 2))
        code, out = self.req("POST", "/api/decision",
                             {"action": "propose", "op": {"type": "remove", "node": "Refunds", "disposition": "demote"}})
        self.assertEqual(code, 400)
        code, out = self.req("POST", "/api/submit", {})
        self.assertEqual((code, out["outcome"]["status"]), (200, "submitted"))
        self.t.join(5)
        self.assertEqual(self.codes, [0])

    def test_db_is_read_only(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.app.db.execute("DELETE FROM chunk_topics")

    def test_ui_served_without_token(self):
        with urllib.request.urlopen(self.base + "/", timeout=5) as resp:
            self.assertIn(b"X-Review-Token", resp.read())

    def test_ui_uses_only_the_documented_api(self):
        html = open(S.UI, encoding="utf-8").read()
        for route in ("/api/state", "/api/node/", "/api/chunk/", "/api/impact", "/api/decision", "/api/submit",
                      "/api/cancel"):
            self.assertIn(route, html)
        self.assertNotIn("http://", html.replace("http://127.0.0.1", ""))
        self.assertNotIn("https://", html)
        self.assertNotIn("confirm(", html)


class TimeoutTests(unittest.TestCase):
    def test_timeout_exits_3(self):
        with tempfile.TemporaryDirectory() as td:
            cur = os.path.join(td, "taxonomy", "current.json")
            write_json(cur, taxonomy(version=1))
            path, _ = R.build_plan("browse", cur, now=NOW)
            app = S.ReviewApp(path)
            code = S.serve(app, open_browser=False, timeout=0.3, out=open(os.devnull, "w"), err=open(os.devnull, "w"))
            self.assertEqual(code, 3)
