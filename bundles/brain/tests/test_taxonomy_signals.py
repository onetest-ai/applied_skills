import sqlite3, unittest
import numpy as np

import taxonomy_signals as TS

TAX = {"intent_taxonomy": {"tree": {
    "Billing": ["Payment Processing", "Payment Transactions and Processing", "Delivery Window"],
    "Delivery": ["Track Delivery"]}, "unassigned_l2": []},
    "entities": {"system": ["RMS"], "systems": ["Engage"]}}

# Toy embedding: fixed vectors per label, so similarity is known.
VEC = {"Payment Processing": [1, 0, 0], "Payment Transactions and Processing": [0.98, 0.2, 0],
       "Delivery Window": [0, 0, 1], "Track Delivery": [0, 0.1, 1], "Billing": [1, 0.3, 0],
       "Delivery": [0, 0.3, 1], "system": [0, 1, 0], "systems": [0, 1, 0]}


def embed(texts):
    return np.array([VEC[t] for t in texts], dtype=np.float32)


def store(chunks):
    """chunks: {id: (vector, [category_ids])}"""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE chunks_vec(embedding BLOB)")
    c.execute("CREATE TABLE chunk_topics(chunk_id INT, category_id TEXT, category_label TEXT, kind TEXT)")
    for cid, (v, cats) in chunks.items():
        c.execute("INSERT INTO chunks_vec(rowid, embedding) VALUES(?,?)", (cid, np.array(v, dtype=np.float32).tobytes()))
        for cat in cats:
            c.execute("INSERT INTO chunk_topics VALUES(?,?,?,?)", (cid, cat, cat, "x"))
    return c


class SignalsTests(unittest.TestCase):
    def test_label_duplicates_cluster_within_level_only(self):
        out = TS.compute(TAX, store({}), embed)
        members = [sorted(cl["members"]) for cl in out["label_clusters"]]
        self.assertIn(["Payment Processing", "Payment Transactions and Processing"], members)
        self.assertFalse(any("system" in m for m in members))          # entities never cluster
        cl = next(c for c in out["label_clusters"] if "Payment Processing" in c["members"])
        self.assertEqual((cl["level"], cl["parent"]), ("L2", "Billing"))

    def test_misplaced_l2_whose_chunks_sit_with_another_l1(self):
        chunks = {1: ([1, 0, 0], ["billing"]), 2: ([1, 0.1, 0], ["billing"]),
                  3: ([0, 0, 1], ["delivery", "track_delivery"]), 4: ([0, 0.1, 1], ["delivery"]),
                  5: ([0, 0, 1], ["delivery_window"]), 6: ([0, 0.05, 1], ["delivery_window"]),
                  7: ([0.1, 0, 1], ["delivery_window"])}
        out = TS.compute(TAX, store(chunks), embed, min_chunks=3)
        self.assertEqual([(m["node"], m["better_parent"]) for m in out["misplaced"]], [("Delivery Window", "Delivery")])

    def test_sparse_l2_is_not_judged_for_misplacement(self):
        chunks = {1: ([1, 0, 0], ["billing"]), 3: ([0, 0, 1], ["delivery"]), 5: ([0, 0, 1], ["delivery_window"])}
        self.assertEqual(TS.compute(TAX, store(chunks), embed, min_chunks=3)["misplaced"], [])

    def test_output_is_deterministic(self):
        a = TS.compute(TAX, store({}), embed)
        b = TS.compute(TAX, store({}), embed)
        self.assertEqual(a, b)

    def test_min_chunks_zero_skips_an_l2_with_no_tagged_chunks(self):
        chunks = {1: ([1, 0, 0], ["billing"]), 3: ([0, 0, 1], ["delivery"])}   # no L2 has a centroid
        self.assertEqual(TS.compute(TAX, store(chunks), embed, min_chunks=0)["misplaced"], [])
