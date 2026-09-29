import importlib.util, re, sqlite3, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "skills" / "knowledge-pipeline"
spec = importlib.util.spec_from_file_location("brain_sync", HERE / "brain_sync.py")
S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)


class BuiltAtTests(unittest.TestCase):
    def test_write_built_at_upserts_iso_utc(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "k.sqlite"
            c = sqlite3.connect(db)
            S.ensure_meta(c)
            S.write_built_at(c, now="2026-09-28T10:00:00Z")
            S.write_built_at(c, now="2026-09-29T11:00:00Z")
            self.assertEqual(S.read_meta(c, "built_at"), "2026-09-29T11:00:00Z")
            S.write_built_at(c)
            self.assertRegex(S.read_meta(c, "built_at"), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
