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

    def test_cancel_exits_four(self):
        code, out = self.req("POST", "/api/cancel", {})
        self.assertEqual((code, out["outcome"]["status"]), (200, "cancelled"))
        self.t.join(5)
        self.assertEqual(self.codes, [4])

    def test_submit_then_cancel_keeps_submitted_and_exits_zero(self):
        self.assertEqual(self.req("POST", "/api/submit", {})[0], 200)
        code, out = self.req("POST", "/api/cancel", {})   # inside the flush window
        self.assertEqual(code, 409, out)
        self.assertEqual(self.app.outcome["status"], "submitted")
        self.t.join(5)
        self.assertEqual(self.codes, [0])
        self.assertEqual([r["action"] for r in D.read(self.dec)], ["submit"])

    def test_decide_after_submit_is_409_and_not_recorded(self):
        self.assertEqual(self.req("POST", "/api/submit", {})[0], 200)
        op = {"type": "add", "level": "L1", "name": "AI & Automation"}
        code, out = self.req("POST", "/api/decision", {"action": "propose", "op": op})
        self.assertEqual(code, 409, out)
        self.assertEqual(self.req("POST", "/api/submit", {})[0], 409)
        self.assertEqual([r["action"] for r in D.read(self.dec)], ["submit"])

    def test_decision_endpoint_allow_lists_item_actions(self):
        for action in ("submit", "applied", "bogus", None):
            code, out = self.req("POST", "/api/decision", {"action": action})
            self.assertEqual(code, 400, (action, out))
        self.assertFalse(os.path.exists(self.dec))
        self.assertTrue(self.t.is_alive())

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

    def test_ui_escapes_every_interpolated_id(self):
        import re
        html = open(S.UI, encoding="utf-8").read()
        raw = re.findall(r"\$\{(?:it|i|p|s|c|item)\.(?:id|item_id|chunk_id)\}", html)
        self.assertEqual(raw, [])

    def test_ui_offers_revert_to_keep_and_closes_header_after_submit(self):
        html = open(S.UI, encoding="utf-8").read()
        self.assertIn("Revert to keep", html)
        self.assertIn("Revert it in Changes", html)
        self.assertIn("closeHeader()", html)

    def test_revert_to_keep_undoes_a_draft_amend(self):
        # What the "Revert to keep" button posts: approve on an amended first-build item.
        with tempfile.TemporaryDirectory() as td:
            v0 = os.path.join(td, "taxonomy", "taxonomy_v0.json")
            write_json(v0, taxonomy())
            path, rv = R.build_plan("draft", v0, now=NOW)
            app = S.ReviewApp(path)
            item = next(i for i in rv["items"] if i["op"]["node"] == "Refunds")
            op = {"type": "rename", "node": "Refunds", "new_name": "Refund Requests"}
            self.assertEqual(app.decide({"action": "amend", "item_id": item["id"], "op": op})[0], 200)
            self.assertEqual(app.decide({"action": "approve", "item_id": item["id"]})[0], 200)
            self.assertEqual(D.effective_ops(rv, D.read(app.decisions_path)), [])

    def test_state_and_node_carry_descriptions_and_context(self):
        code, st = self.req("GET", "/api/state")
        self.assertIn("descriptions", st["base"])
        self.assertIn("context", st["review"])
        code, node = self.req("GET", "/api/node/Refunds")
        self.assertIn("description", node)

    def test_clear_undoes_a_decision(self):
        op = {"type": "describe", "node": "Refunds", "description": "Money returned."}
        code, out = self.req("POST", "/api/decision", {"action": "propose", "op": op})
        self.assertEqual(code, 200, out)
        code, out = self.req("POST", "/api/decision", {"action": "withdraw", "item_id": out["record"]["item_id"]})
        self.assertEqual(code, 200, out)

    def test_clear_undoes_an_approved_plan_item(self):
        # A real plan item (not a human proposal): approve it via /api/decision, then clear it.
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "taxonomy")
            cur = os.path.join(d, "current.json"); write_json(cur, taxonomy(version=1))
            db = os.path.join(td, "k.sqlite"); tagged_store(db, taxonomy(version=1))
            props = os.path.join(td, "props")
            write_json(os.path.join(props, "result_0.json"), {"descriptions": [
                {"node": "Refunds", "description": "Money returned to a customer."}]})
            path, rv = R.build_plan("describe", cur, proposals_dir=props, db=db, now=NOW)
            item = next(i for i in rv["items"] if i["op"]["node"] == "Refunds")
            self.assertEqual(item["status"], "proposed")
            app = S.ReviewApp(path, db_path=db)
            code, out = app.decide({"action": "approve", "item_id": item["id"]})
            self.assertEqual(code, 200, out)
            code, out = app.decide({"action": "clear", "item_id": item["id"]})
            self.assertEqual(code, 200, out)
            self.assertEqual(app.state()["decisions"][item["id"]]["action"], "clear")


class TimeoutTests(unittest.TestCase):
    def test_timeout_exits_3(self):
        with tempfile.TemporaryDirectory() as td:
            cur = os.path.join(td, "taxonomy", "current.json")
            write_json(cur, taxonomy(version=1))
            path, _ = R.build_plan("browse", cur, now=NOW)
            app = S.ReviewApp(path)
            code = S.serve(app, open_browser=False, timeout=0.3, out=open(os.devnull, "w"), err=open(os.devnull, "w"))
            self.assertEqual(code, 3)

    def test_submit_after_timeout_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            cur = os.path.join(td, "taxonomy", "current.json")
            write_json(cur, taxonomy(version=1))
            path, _ = R.build_plan("browse", cur, now=NOW)
            app = S.ReviewApp(path)
            self.assertEqual(app.expire()["status"], "timeout")
            code, _ = app.submit({})
            self.assertEqual(code, 409)
            self.assertEqual(app.outcome["status"], "timeout")
            self.assertEqual(D.read(D.default_path(os.path.dirname(cur))), [])
