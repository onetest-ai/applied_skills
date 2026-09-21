import json, os, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path

import taxonomy_merge as M
from taxo_fixtures import tagged_store, taxonomy, write_json

CTE = Path(__file__).resolve().parent.parent / "skills" / "corpus-taxonomy-extraction"


def run(script, *args):
    r = subprocess.run([sys.executable, str(CTE / script), *args], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return r


class EmitTests(unittest.TestCase):
    def test_emit_keeps_most_common_description(self):
        with tempfile.TemporaryDirectory() as td:
            con = {"summary": {"intent_classes": {"raw_mentions": 3, "clusters": 2, "ambiguous_clusters": 0}},
                   "clusters": {"intent_classes": [
                       {"canonical_guess": "Billing", "normalized": "billing", "variants": ["Billing"], "n_mentions": 3,
                        "n_sources": 2, "sources": ["a"], "avg_confidence": 0.8, "ambiguous": False,
                        "members": [{"name": "Billing", "level": "L1", "description": "Charges and invoices."},
                                    {"name": "Billing", "level": "L1", "description": "Charges and invoices."},
                                    {"name": "Billing", "level": "L1", "description": "Other."}]},
                       {"canonical_guess": "Refunds", "normalized": "refunds", "variants": ["Refunds"], "n_mentions": 1,
                        "n_sources": 1, "sources": ["a"], "avg_confidence": 0.7, "ambiguous": False,
                        "members": [{"name": "Refunds", "level": "L2", "parent": "Billing", "description": ""}]}],
                       "metrics": [], "entities": []}}
            write_json(os.path.join(td, "c.json"), con); os.makedirs(os.path.join(td, "map"))
            run("emit_taxonomy.py", "--consolidated", os.path.join(td, "c.json"), "--map-dir", os.path.join(td, "map"),
                "--out-json", os.path.join(td, "t.json"), "--out-md", os.path.join(td, "t.md"))
            t = json.load(open(os.path.join(td, "t.json")))
            self.assertEqual(t["descriptions"], {"Billing": "Charges and invoices."})
            self.assertIn("Charges and invoices.", open(os.path.join(td, "t.md")).read())


class VocabTests(unittest.TestCase):
    def test_classify_and_refine_vocab_show_descriptions(self):
        with tempfile.TemporaryDirectory() as td:
            tax = taxonomy(); tax["descriptions"] = {"Refunds": "Money returned after a charge."}
            write_json(os.path.join(td, "t.json"), tax)
            db = os.path.join(td, "k.sqlite"); tagged_store(db, tax)
            run("classify_prep.py", "--db", db, "--taxonomy", os.path.join(td, "t.json"), "--out", os.path.join(td, "c"))
            self.assertIn("    - Refunds — Money returned after a charge.", open(os.path.join(td, "c", "vocab.md")).read())
            run("taxonomy_refine_prep.py", "--db", db, "--taxonomy", os.path.join(td, "t.json"), "--out", os.path.join(td, "r"))
            self.assertIn("Refunds — Money returned after a charge.", open(os.path.join(td, "r", "vocab.md")).read())
            self.assertIn('"description"', open(os.path.join(td, "r", "instructions.md")).read())

    def test_classify_vocab_collapses_embedded_newlines(self):
        with tempfile.TemporaryDirectory() as td:
            tax = taxonomy(); tax["descriptions"] = {"Refunds": "Line one.\nLine two."}
            write_json(os.path.join(td, "t.json"), tax)
            db = os.path.join(td, "k.sqlite"); tagged_store(db, tax)
            run("classify_prep.py", "--db", db, "--taxonomy", os.path.join(td, "t.json"), "--out", os.path.join(td, "c"))
            vocab = open(os.path.join(td, "c", "vocab.md")).read()
            self.assertIn("    - Refunds — Line one. Line two.", vocab)
            self.assertEqual(sum(1 for l in vocab.splitlines() if "Refunds" in l), 1)


class PlanAdditionsTests(unittest.TestCase):
    def test_description_carried(self):
        items, _ = M.plan_additions(taxonomy(), [
            {"name": "AI Handoffs", "level": "L1", "description": ""},
            {"name": "AI handoffs", "level": "L1", "description": "Calls escalated from the bot."}])
        self.assertEqual(items[0]["description"], "Calls escalated from the bot.")


class GraphAndVaultTests(unittest.TestCase):
    def test_build_graph_adds_column_and_writes_descriptions(self):
        with tempfile.TemporaryDirectory() as td:
            tax = taxonomy(); tax["descriptions"] = {"Refunds": "Money back.", "branch": "A physical branch."}
            db = os.path.join(td, "k.sqlite"); tagged_store(db, taxonomy())  # old 4-column graph_nodes
            c = sqlite3.connect(db)
            c.execute("ALTER TABLE chunks ADD COLUMN ord INTEGER")
            c.execute("UPDATE chunks SET ord=id")
            c.commit(); c.close()
            write_json(os.path.join(td, "t.json"), tax)
            run("build_graph.py", "--taxonomy", os.path.join(td, "t.json"), "--db", db)
            c = sqlite3.connect(db)
            d = dict(c.execute("SELECT label, description FROM graph_nodes WHERE description IS NOT NULL"))
            self.assertEqual(d, {"Refunds": "Money back.", "branch": "A physical branch."})
            out = os.path.join(td, "vault")
            run("to_obsidian.py", "--db", db, "--out", out)
            found = [p for p in Path(out).rglob("*.md") if "Money back." in p.read_text()]
            self.assertTrue(found)
