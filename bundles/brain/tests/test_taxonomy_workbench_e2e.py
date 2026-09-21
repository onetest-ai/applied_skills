"""v0 draft review → v1 → build/tag → browse review (merge, move, remove, metric) → v2 → migrate."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import decisions as D
import graph_migrate as GM
import review_server as S
import taxonomy_merge as M
import taxonomy_review as R
from taxo_fixtures import TAGS, tag_rows, taxonomy, write_json

CTE = Path(__file__).resolve().parent.parent / "skills" / "corpus-taxonomy-extraction"


def py(script, *args):
    r = subprocess.run([sys.executable, str(CTE / script), *args], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return r


class WorkbenchE2E(unittest.TestCase):
    def test_full_cycle(self):
        with tempfile.TemporaryDirectory() as td:
            tdir = os.path.join(td, "taxonomy")
            v0 = os.path.join(tdir, "taxonomy_v0.json")
            write_json(v0, taxonomy())
            db = os.path.join(td, "k.sqlite")

            # 1. first build: draft review ratifies v0 with one merge
            path, rv = R.build_plan("draft", v0)
            app = S.ReviewApp(path, reviewer="Pat")
            admin = next(i for i in rv["items"] if i["op"]["node"] == "Billing & Payments Admin")
            self.assertEqual(app.decide({"action": "amend", "item_id": admin["id"],
                                         "op": {"type": "merge", "from": "Billing & Payments Admin",
                                                "into": "Billing & Payments"}})[0], 200)
            self.assertEqual(app.submit({})[0], 200)
            self.assertEqual(M.apply_review(path)["version"], 1)
            cur = os.path.join(tdir, "current.json")

            # 2. build graph from current, then tag chunks as classify_write would
            con = sqlite3.connect(db)
            con.executescript("CREATE TABLE chunks(id INTEGER PRIMARY KEY, source TEXT, title TEXT, text TEXT);")
            con.executemany("INSERT INTO chunks VALUES(?,?,?,?)",
                            [(cid, f"d{cid}.md", f"S{cid}", "t") for cid in TAGS])
            con.commit()
            con.close()
            py("build_graph.py", "--taxonomy", cur, "--db", db)
            res = os.path.join(td, "cls")
            tags = {str(k): [l for l in v if l != "Billing & Payments Admin"] for k, v in TAGS.items()}
            write_json(os.path.join(res, "result_0.json"), tags)
            py("classify_write.py", "--db", db, "--results", res)
            before = tag_rows(db)

            # 3. browse review: rename, move, remove, metric merge
            path2, _ = R.build_plan("browse", cur, db=db)
            app2 = S.ReviewApp(path2, db_path=db, reviewer="Pat")
            for op in ({"type": "rename", "node": "Refunds", "new_name": "Refund Requests"},
                       {"type": "move", "node": "Proof of Delivery", "new_parent": "Billing & Payments"},
                       {"type": "remove", "node": "Transform", "disposition": "demote", "reason": "phase"},
                       {"type": "metric_merge", "from": "Avg Handle Time", "into": "Average Handle Time"}):
                code, out = app2.decide({"action": "propose", "op": op})
                self.assertEqual(code, 200, out)
            app2.submit({})
            self.assertEqual(M.apply_review(path2)["version"], 2)

            # 4. build_graph migrates: tags move, removed tags go, reclassify queue written
            py("build_graph.py", "--taxonomy", cur, "--db", db)
            after = tag_rows(db)
            self.assertEqual(sorted(c for c, cat in after if cat == "refund_requests"),
                             sorted(c for c, cat in before if cat == "refunds"))
            self.assertNotIn("transform", {cat for _, cat in after})
            self.assertEqual(len(after), len(before) - 1)          # only Transform's one tag is gone
            rc_path = os.path.join(tdir, "work", "reclassify.json")
            self.assertEqual(json.load(open(rc_path))["chunk_ids"], [6, 7])
            self.assertEqual(GM.read_version(sqlite3.connect(db)), 2)

            # 5. reclassify the queue; the queue file disappears
            write_json(os.path.join(res, "result_0.json"), {"6": ["Proof of Delivery"], "7": []})
            py("classify_write.py", "--db", db, "--results", res, "--reclassify-done", rc_path)
            self.assertFalse(os.path.exists(rc_path))
            roll = sqlite3.connect(db).execute(
                "SELECT category_id FROM chunk_topics WHERE chunk_id=6 ORDER BY 1").fetchall()
            self.assertEqual([r[0] for r in roll], ["billing_payments", "proof_of_delivery"])

            # 6. the record: two submitted, two applied; v2 history names the reviewer
            recs = D.read(os.path.join(tdir, "decisions.jsonl"))
            self.assertEqual(sum(r["action"] == "applied" for r in recs), 2)
            v2 = json.load(open(os.path.join(tdir, "taxonomy_v2.json")))
            self.assertEqual(v2["history"][-1]["reviewer"], "Pat")
            self.assertEqual([m["metric"] for m in v2["metrics"]], ["Average Handle Time", "Porch Rate"])
