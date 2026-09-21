import os
import sqlite3
import tempfile
import unittest

import graph_migrate as GM
import taxo_impact as TI
import taxo_ops as O
from taxo_fixtures import tag_rows, tagged_store, taxonomy

MERGE = {"type": "merge", "from": "Billing & Payments Admin", "into": "Billing & Payments"}
MOVE = {"type": "move", "node": "Proof of Delivery", "new_parent": "Billing & Payments"}
REMOVE = {"type": "remove", "node": "Transform", "disposition": "demote", "reason": "x"}
RENAME = {"type": "rename", "node": "Refunds", "new_name": "Refund Requests"}


class ImpactTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.td.name, "k.sqlite")
        tagged_store(self.db, taxonomy())
        self.ro = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)

    def tearDown(self):
        self.ro.close()
        self.td.cleanup()

    def test_each_op(self):
        self.assertEqual(TI.impact(self.ro, taxonomy(), [MERGE]),
                         {**TI.ZERO, "tags_repointed": 1, "tags_deduplicated": 1})
        self.assertEqual(TI.impact(self.ro, taxonomy(), [MOVE]), {**TI.ZERO, "chunks_reclassified": 1})
        self.assertEqual(TI.impact(self.ro, taxonomy(), [REMOVE]),
                         {**TI.ZERO, "tags_deleted": 1, "chunks_reclassified": 1,
                          "chunks_left_untagged_until_reclassify": 1})
        self.assertEqual(TI.impact(self.ro, taxonomy(), [RENAME]), {**TI.ZERO, "tags_repointed": 2})

    def test_preview_never_writes(self):
        before = tag_rows(self.db)
        TI.impact(self.ro, taxonomy(), [MERGE, REMOVE])
        self.assertEqual(tag_rows(self.db), before)

    def test_impact_equals_real_migration(self):
        ops = [MERGE, MOVE, REMOVE, RENAME]
        preview = TI.impact(self.ro, taxonomy(), ops)
        _, migs, _ = O.apply_ops(taxonomy(), ops)
        c = sqlite3.connect(self.db)
        reclass, _, stats = GM.run(c, migs)
        c.commit()
        self.assertEqual({k: preview[k] for k in stats}, stats)
        self.assertEqual(preview["chunks_reclassified"], len(reclass))

    def test_delta_with_prior_ops_and_no_db(self):
        d = TI.delta(self.ro, taxonomy(), [MERGE], RENAME)
        self.assertEqual(d["tags_repointed"], 2)
        self.assertEqual(TI.impact(None, taxonomy(), [MERGE]), TI.ZERO)

    def test_invalid_changeset_raises(self):
        with self.assertRaises(O.ChangesetError):
            TI.impact(self.ro, taxonomy(), [{"type": "move", "node": "Nope", "new_parent": "Transform"}])
