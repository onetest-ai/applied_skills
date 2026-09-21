"""Health review end to end, through the real CLIs:

diagnose → hand-made agent results (describe, notags, structure incl. a 3+ cluster, untagged
with a `map`) → plan --mode health → ReviewApp (batch accept of a group that leaves a fallback
out, a subset of a tag fix, a pair merge, a cluster's merge rows, a redo request answered with
`respond`) → submit → taxonomy_merge --review --apply → build_graph → classify_write --merge.
"""
import glob
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import review_server as S
from taxo_fixtures import TAGS, tag_rows, tagged_store, taxonomy, write_json

CTE = Path(__file__).resolve().parent.parent / "skills" / "corpus-taxonomy-extraction"

# The shared fixture's tags, plus two sections for a 3-member near-duplicate cluster.
E2E_TAGS = {**TAGS, 10: ["Track Delivery Status"], 11: ["Track Delivery Updates"]}
# Untagged sections added after the store is built: two FTS candidates for "Payment Plans".
UNTAGGED = {9: "customer asks for payment plans to split the bill",
            12: "payment plans with installment billing for larger orders"}


def run(script, *args):
    r = subprocess.run([sys.executable, str(CTE / script), *args], text=True, capture_output=True)
    assert r.returncode == 0, f"{script} exited {r.returncode}\nstdout: {r.stdout}\nstderr: {r.stderr}"
    return r


def json_line(r):
    lines = r.stdout.strip().splitlines()
    assert len(lines) == 1, r.stdout
    return json.loads(lines[0])


def build_store(db, tax):
    tagged_store(db, tax, tags=E2E_TAGS)
    c = sqlite3.connect(db)
    c.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(text)")
    c.executemany("INSERT INTO chunks_fts(rowid, text) VALUES(?,?)",
                  list(c.execute("SELECT id, text FROM chunks")))
    for cid, text in UNTAGGED.items():
        c.execute("INSERT INTO chunks VALUES(?,?,?,?)", (cid, f"doc{cid}.md", "Payment plans", text))
        c.execute("INSERT INTO chunks_fts(rowid, text) VALUES(?,?)", (cid, text))
    c.commit()
    c.close()


class HealthReviewE2E(unittest.TestCase):
    def test_full_health_cycle(self):
        with tempfile.TemporaryDirectory() as td:
            tdir = os.path.join(td, "taxonomy")
            tax = taxonomy(version=1)
            tree = tax["intent_taxonomy"]["tree"]
            tree["Billing & Payments"].append("Payment Plans")                          # 0 tags
            tree["Delivery & Pickup"] += ["Track Delivery Status", "Track Delivery Updates"]
            cur = os.path.join(tdir, "current.json")
            write_json(cur, tax)
            db = os.path.join(td, "k.sqlite")
            build_store(db, tax)
            run("build_graph.py", "--taxonomy", cur, "--db", db)       # the store was built from v1
            work = os.path.join(tdir, "work", "health")

            # 1. diagnose
            diag = json_line(run("taxonomy_review.py", "diagnose", "--taxonomy", cur, "--db", db, "--out", work))
            dirs = {t["kind"]: t["dir"] for t in diag["tasks"]}
            self.assertTrue({"describe", "notags", "structure", "untagged"} <= set(dirs))
            self.assertNotIn("metric_not_governed", {k for k, v in diag["problems"].items() if v})  # no governed file
            problems = json.load(open(os.path.join(work, "problems.json")))
            payment = next(p for p in problems["no_tags"] if p["node"] == "Payment Plans")
            self.assertEqual(sorted(x["chunk_id"] for x in payment["candidates"]), [9, 12])
            cluster = next(p for p in problems["near_duplicate"] if len(p["members"]) == 3)
            self.assertEqual(cluster["members"], ["Track Delivery", "Track Delivery Status", "Track Delivery Updates"])
            self.assertFalse(cluster["pattern"])

            # 2. the agents' results, by hand. One describe node is left out: it must come back
            #    as a fallback that "Accept all remaining" leaves alone.
            to_describe = [e["node"] for bf in sorted(glob.glob(os.path.join(dirs["describe"], "batch_*.json")))
                           for e in json.load(open(bf))]
            undescribed = "Proof of Delivery"
            self.assertIn(undescribed, to_describe)
            write_json(os.path.join(dirs["describe"], "result_0.json"), {"descriptions": [
                {"node": n, "description": f"Synthetic description of {n}."} for n in to_describe if n != undescribed]})
            write_json(os.path.join(dirs["notags"], "result_0.json"), {"fixes": [
                {"node": "Payment Plans", "fix": "tag", "chunk_ids": [9, 12], "reason": "both discuss payment plans"}]})
            write_json(os.path.join(dirs["structure"], "result_0.json"), {"fixes": [
                {"kind": "near_duplicate", "subject": "Billing & Payments Admin", "fix": "merge",
                 "into": "Billing & Payments", "reason": "same topic"},
                {"kind": "near_duplicate", "subject": "Track Delivery", "fix": "merge",
                 "into": "Track Delivery", "reason": "status and updates are the same request"}]})
            untagged_batch = json.load(open(os.path.join(dirs["untagged"], "batch_0.json")))
            self.assertIn(8, [r["id"] for r in untagged_batch])
            write_json(os.path.join(dirs["untagged"], "result_0.json"),
                       {"proposals": [], "map": {"8": ["Refunds"]}})

            # 3. plan --mode health
            plan = json_line(run("taxonomy_review.py", "plan", "--mode", "health", "--taxonomy", cur,
                                 "--work", work, "--db", db))
            self.assertEqual(plan.get("skipped_files", []), [])
            review_path = plan["review"]
            review = json.load(open(review_path))
            items = review["items"]
            self.assertTrue(all("_fallback" not in i for i in items))

            def one(pred):
                hits = [i for i in items if pred(i)]
                self.assertEqual(len(hits), 1, hits)
                return hits[0]

            # 4. decide in the app (the same handlers the HTTP routes call)
            app = S.ReviewApp(review_path, db_path=db, reviewer="Pat", live_channel=True)
            self.assertTrue(app.state()["live_channel"])

            # 4a. the pair merge, as proposed
            pair = one(lambda i: i["kind"] == "near_duplicate" and i["group"] is None)
            self.assertEqual(pair["op"], {"type": "merge", "from": "Billing & Payments Admin",
                                          "into": "Billing & Payments"})
            code, out = app.decide({"action": "approve", "item_id": pair["id"]})
            self.assertEqual(code, 200, out)

            # 4b. the cluster comes back as a group of merge rows; accept the whole group
            rows = [i for i in items if i["kind"] == "near_duplicate" and i["group"]]
            self.assertEqual(sorted(i["op"]["from"] for i in rows), ["Track Delivery Status", "Track Delivery Updates"])
            self.assertTrue(all(i["op"]["type"] == "merge" and i["op"]["into"] == "Track Delivery" for i in rows))
            self.assertEqual(len({i["group"] for i in rows}), 1)
            code, out = app.decisions({"records": [{"action": "approve", "item_id": i["id"]} for i in rows]})
            self.assertEqual(code, 200, out)

            merged_away = ["Billing & Payments Admin", "Track Delivery Status", "Track Delivery Updates"]

            # 4c. "Accept all remaining" on the description group: every non-fallback row whose
            #     category is not being merged away by a fix already accepted above — the app's
            #     `batchable` leaves those rows out (a describe on a merged-away node would be
            #     refused as "changed twice"), so the batch goes through in one call.
            desc_group = [i for i in items if i["group"] == "missing_description"]
            fallback = one(lambda i: i["group"] == "missing_description" and i["op"]["node"] == undescribed)
            self.assertTrue(fallback.get("fallback"))
            self.assertEqual(fallback["op"]["type"], "keep")
            eligible = [i for i in desc_group if i["status"] == "proposed" and not i.get("fallback")]
            self.assertEqual(len(eligible), len(to_describe) - 1)
            recs = [{"action": "approve", "item_id": i["id"]} for i in eligible if i["op"]["node"] not in merged_away]
            self.assertEqual(len(recs), len(eligible) - len(merged_away))
            code, out = app.decisions({"records": recs})
            self.assertEqual(code, 200, out)

            # 4d. the no-tags tag fix, narrowed to a subset of its chunk ids
            tag_item = one(lambda i: i["kind"] == "no_tags" and i["op"]["node"] == "Payment Plans")
            self.assertEqual(tag_item["op"], {"type": "tag", "node": "Payment Plans", "chunk_ids": [9, 12]})
            code, out = app.decide({"action": "amend", "item_id": tag_item["id"],
                                    "op": {"type": "tag", "node": "Payment Plans", "chunk_ids": [9]}})
            self.assertEqual(code, 200, out)

            # 4e. the untagged `map` assignment became a tag op on an existing category
            mapped = one(lambda i: i["kind"] == "untagged_sections" and i["op"]["type"] == "tag")
            self.assertEqual(mapped["op"], {"type": "tag", "node": "Refunds", "chunk_ids": [8]})
            code, out = app.decide({"action": "approve", "item_id": mapped["id"]})
            self.assertEqual(code, 200, out)

            # 4f. redo with a note on the similar-metrics item (no agent result → fallback keep):
            #     request in the app, `respond` from the CLI, the revision shows in state, approve
            #     posts an amend with it
            sim = one(lambda i: i["kind"] == "similar_metrics")
            self.assertTrue(sim.get("fallback"))
            code, out = app.request({"item_id": sim["id"], "note": "these are the same metric"})
            self.assertEqual(code, 200, out)
            req_id = out["request"]["id"]
            revised = {"type": "metric_merge", "from": "Avg Handle Time", "into": "Average Handle Time"}
            resp = json_line(run("taxonomy_review.py", "respond", "--review", review_path, "--request", req_id,
                                 "--op", json.dumps(revised), "--reason", "same measure, shorter name"))
            self.assertEqual(resp["status"], "recorded")
            st = app.state()
            self.assertEqual(st["revisions"][sim["id"]]["op"], revised)
            self.assertEqual(st["requests"][req_id]["status"], "answered")
            code, out = app.decide({"action": "amend", "item_id": sim["id"], "op": revised})
            self.assertEqual(code, 200, out)

            code, out = app.submit({})
            self.assertEqual(code, 200, out)

            # 5. apply, rebuild the graph, write the approved tags additively
            before = tag_rows(db)
            applied = run("taxonomy_merge.py", "--review", review_path, "--apply")
            res = json.loads(applied.stdout.strip().splitlines()[0])
            self.assertEqual(res["status"], "applied")
            self.assertTrue(res["taxonomy_changed"])
            self.assertIsNone(res["governed_drafts_file"])
            tags_dir = os.path.dirname(res["tags_file"])
            self.assertIn(f"--results {tags_dir} --merge", applied.stderr)
            self.assertEqual(json.load(open(res["tags_file"])), {"9": ["Payment Plans"], "8": ["Refunds"]})

            run("build_graph.py", "--taxonomy", cur, "--db", db)
            migrated = set(tag_rows(db))
            run("classify_write.py", "--db", db, "--results", tags_dir, "--merge")
            after = set(tag_rows(db))

            # descriptions are in current.json, except the fallback row nobody accepted
            new = json.load(open(cur))
            self.assertEqual(new["version"], 2)
            descs = new.get("descriptions") or {}
            for n in ("Billing & Payments", "Payment Plans", "Refunds", "Track Delivery", "Transform"):
                self.assertEqual(descs.get(n), f"Synthetic description of {n}.")
            self.assertNotIn(undescribed, descs)
            for n in merged_away:
                self.assertNotIn(n, descs)

            # the merges moved their tags and their nodes are gone
            self.assertNotIn("Billing & Payments Admin", new["intent_taxonomy"]["tree"])
            self.assertEqual(new["intent_taxonomy"]["tree"]["Delivery & Pickup"], ["Track Delivery", "Proof of Delivery"])
            gone = {"billing_payments_admin", "track_delivery_status", "track_delivery_updates"}
            self.assertFalse(gone & {cat for _, cat in after})
            for cid in (3, 4):
                self.assertIn((cid, "billing_payments"), after)
            for cid in (10, 11):
                self.assertIn((cid, "track_delivery"), after)

            # the new tags exist (L2 → L1 rollup), the unchecked candidate stayed untagged,
            # and every tag the chunks had after migration is still there
            self.assertLessEqual({(9, "payment_plans"), (9, "billing_payments"),
                                  (8, "refunds"), (8, "billing_payments")}, after)
            self.assertNotIn(12, {cid for cid, _ in after})
            self.assertLessEqual(migrated, after)
            self.assertEqual(after - migrated, {(9, "payment_plans"), (9, "billing_payments"),
                                                (8, "refunds"), (8, "billing_payments")})
            for cid, cat in before:
                if cat not in gone:
                    self.assertIn((cid, cat), after)

            # the revised metric merge applied; no metric fix → no governed drafts
            self.assertEqual([m["metric"] for m in new["metrics"]], ["Average Handle Time", "Porch Rate"])
            self.assertFalse(os.path.exists(os.path.join(tdir, "work", "governed_metric_drafts.json")))


if __name__ == "__main__":
    unittest.main()
