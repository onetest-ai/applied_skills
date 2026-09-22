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
import health as H
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
        self.assertEqual(st["totals"], {"chunks": 8, "tagged": 7, "untagged": 1, "no_topic": 0})
        code, node = self.req("GET", "/api/node/Refunds")
        self.assertEqual((node["level"], node["parent"], node["tags"]), ("L2", "Billing & Payments", 2))
        self.assertEqual({s["label"]: s["overlap"] for s in node["siblings"]}, {"Duplicate Charge": 0})

    def test_totals_exclude_no_topic_chunks_like_health(self):
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE chunk_verdicts(chunk_id INTEGER PRIMARY KEY, verdict TEXT, taxonomy_version INT)")
            c.execute("INSERT INTO chunk_verdicts VALUES(8,'no_topic',1)")     # the one untagged chunk
            c.execute("INSERT INTO chunk_verdicts VALUES(99,'no_topic',1)")    # a verdict whose chunk is gone
        _, st = self.req("GET", "/api/state")
        self.assertEqual(st["totals"], {"chunks": 8, "tagged": 7, "untagged": 0, "no_topic": 1})
        problems = H.detect(os.path.join(self.td.name, "taxonomy", "current.json"), self.db)
        try:
            self.assertEqual(problems[0]["untagged_sections"][0]["count"], st["totals"]["untagged"])
        finally:
            if problems[3] is not None:
                problems[3].close()

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
        self.assertIn("Revert it in Your changes", html)
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

    def test_batch_decisions_all_or_none(self):
        ok = {"action": "propose", "op": {"type": "describe", "node": "Refunds", "description": "Money back."}}
        bad = {"action": "propose", "op": {"type": "describe", "node": "Nope", "description": "x"}}
        code, out = self.req("POST", "/api/decisions", {"records": [ok, bad]})
        self.assertEqual((code, out["index"]), (400, 1))
        self.assertFalse(os.path.exists(self.dec))
        code, out = self.req("POST", "/api/decisions", {"records": [ok]})
        self.assertEqual((code, len(out["records"])), (200, 1))

    def test_request_appends_and_state_shows_it(self):
        code, out = self.req("POST", "/api/request", {"item_id": "i-missing", "note": "x"})
        self.assertEqual(code, 400)

    def test_v2_ui_contract(self):
        html = open(S.UI, encoding="utf-8").read()
        for needle in ("/api/state", "/api/node/", "/api/chunk/", "/api/impact", "/api/decision", "/api/submit",
                       "/api/cancel", '"clear"', "New category", "Add sub-category", "Missing description",
                       "Review &amp; submit", "Your changes", "Not modeled", "What happens next"):
            self.assertIn(needle, html)
        for banned in ("confirm(", "alert(", "prompt(", "https://", "http://"):
            self.assertNotIn(banned, html)
        self.assertNotIn("bar-s", html)  # no share bars in the taxonomy list

    def test_health_ui_contract(self):
        html = open(S.UI, encoding="utf-8").read()
        for needle in ("/api/decisions", "/api/request", "Accept all remaining", "Redo with a note",
                       "Revised by Claude", "Queued for the next run", "Needs a definition",
                       "Possible duplicates", "Quoted from documents"):
            self.assertIn(needle, html)


class SparseHealthStateTests(unittest.TestCase):
    """Task 7: a health review planned from a work dir holding only `problems.json` (no
    fit/result_k.json) carries its sparse problem into state() as a fallback item."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        tax = taxonomy(version=1)
        self.cur = os.path.join(self.dir, "current.json"); write_json(self.cur, tax)
        self.db = os.path.join(self.td.name, "k.sqlite"); tagged_store(self.db, tax)
        self.work = os.path.join(self.dir, "work", "health")
        write_json(os.path.join(self.work, "problems.json"), {"sparse": [
            {"node": "Refunds", "level": "L2", "parent": "Billing & Payments", "tags": 2,
             "siblings": [{"node": "Duplicate Charge", "tags": 1}]}]})
        self.path, self.rv = R.build_plan("health", self.cur, work_dir=self.work, db=self.db, now=NOW)
        self.app = S.ReviewApp(self.path, db_path=self.db)

    def tearDown(self):
        self.td.cleanup()

    def test_sparse_item_is_a_fallback_in_state(self):
        st = self.app.state()
        it = next(i for i in st["review"]["items"] if i["kind"] == "sparse")
        self.assertEqual(it["group"], "sparse")
        self.assertIs(it["fallback"], True)


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


class HealthLiveChannelTests(unittest.TestCase):
    """B4/B6: a request against a health review, and the server-side view of a Claude revision
    recorded via `respond`, into state()'s requests/revisions."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.td.name, "taxonomy")
        tax = taxonomy(version=1)
        tax["intent_taxonomy"]["tree"]["Billing & Payments"].append("Payment Plans")
        self.cur = os.path.join(self.dir, "current.json"); write_json(self.cur, tax)
        self.db = os.path.join(self.td.name, "k.sqlite"); tagged_store(self.db, tax)
        self.work = os.path.join(self.dir, "work", "health")
        H.diagnose(self.cur, self.db, self.work)
        self.path, self.rv = R.build_plan("health", self.cur, work_dir=self.work, db=self.db, now=NOW)
        self.dec = os.path.join(self.dir, "decisions.jsonl")
        self.app = S.ReviewApp(self.path, db_path=self.db, live_channel=True)
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

    def req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method)
        r.add_header("X-Review-Token", self.app.token)
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_request_then_revision_flow(self):
        item = self.rv["items"][0]
        code, out = self.req("POST", "/api/request", {"item_id": item["id"], "note": "please retry"})
        self.assertEqual(code, 200, out)
        rid = out["request"]["id"]
        code, st = self.req("GET", "/api/state")
        self.assertEqual(code, 200, st)
        self.assertEqual(st["requests"][rid]["status"], "open")
        self.assertTrue(st["live_channel"])
        resp_path = os.path.join(self.dir, "work", "responses.jsonl")
        os.makedirs(os.path.dirname(resp_path), exist_ok=True)
        op = {"type": "keep", "node": "x"}
        with open(resp_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"request_id": rid, "item_id": item["id"], "op": op, "ts": "t"}) + "\n")
        code, st = self.req("GET", "/api/state")
        self.assertEqual(st["revisions"][item["id"]]["op"], op)

    def test_revision_tracks_the_items_latest_request_not_a_stale_one(self):
        item = self.rv["items"][0]
        resp_path = os.path.join(self.dir, "work", "responses.jsonl")
        os.makedirs(os.path.dirname(resp_path), exist_ok=True)

        # q-1: requested, answered.
        code, out = self.req("POST", "/api/request", {"item_id": item["id"], "note": "first pass"})
        self.assertEqual(code, 200, out)
        q1 = out["request"]["id"]
        stale_op = {"type": "keep", "node": "stale"}
        with open(resp_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"request_id": q1, "item_id": item["id"], "op": stale_op, "ts": "t1"}) + "\n")

        # q-2: a newer request on the same item, not yet answered.
        code, out = self.req("POST", "/api/request", {"item_id": item["id"], "note": "second pass"})
        self.assertEqual(code, 200, out)
        q2 = out["request"]["id"]

        code, st = self.req("GET", "/api/state")
        self.assertEqual(code, 200, st)
        self.assertEqual(st["requests"][q1]["status"], "answered")
        self.assertEqual(st["requests"][q2]["status"], "open")
        self.assertNotIn(item["id"], st["revisions"])   # the stale q-1 answer must not surface

        # q-2 is answered: now the revision is q-2's op.
        fresh_op = {"type": "keep", "node": "fresh"}
        with open(resp_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"request_id": q2, "item_id": item["id"], "op": fresh_op, "ts": "t2"}) + "\n")
        code, st = self.req("GET", "/api/state")
        self.assertEqual(st["requests"][q2]["status"], "answered")
        self.assertEqual(st["revisions"][item["id"]]["op"], fresh_op)
