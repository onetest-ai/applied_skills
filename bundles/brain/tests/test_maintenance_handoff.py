import importlib.util, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "skills" / "brain-maintenance"
spec = importlib.util.spec_from_file_location("maintenance", HERE / "maintenance.py")
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)


def status(**over):
    base = {"source_plan": {"roots": [{"root_key": "docs", "status": "available"}], "actions": [], "duplicate_content": []},
            "source_action_counts": {}, "parsed_delta": {"blocked_missing_parsed": []},
            "taxonomy_review": {"provisional": False}, "strict_source_error": None,
            "ambiguous_move_hashes": [], "reporting_rebuild_required": False, "narrative_work": []}
    base.update(over); return base


PROFILE = {"deployment": {"enabled": True}, "handoff": {}}


class HandoffTests(unittest.TestCase):
    def test_additions_apply_and_deploy_holds_by_default(self):
        s = status(source_plan={"roots": [{"status": "available"}], "duplicate_content": [],
                                "actions": [{"action": "add", "relative_path": "a.pdf"}]}, source_action_counts={"add": 1})
        r = M.classify_handoff(s, PROFILE)
        self.assertEqual((r["decision"], r["apply"], r["deploy"]), ("apply", ["a.pdf"], "hold"))

    def test_removals_and_ambiguous_moves_are_deferred(self):
        s = status(source_plan={"roots": [{"status": "available"}], "duplicate_content": [],
                                "actions": [{"action": "remove_candidate", "relative_path": "old.pdf"}]},
                   ambiguous_move_hashes=["abc"])
        r = M.classify_handoff(s, PROFILE)
        self.assertEqual(r["decision"], "apply")
        self.assertEqual({d["kind"] for d in r["defer"]}, {"remove_candidate", "ambiguous_move"})

    def test_unavailable_root_or_provisional_taxonomy_aborts(self):
        for s in (status(source_plan={"roots": [{"status": "root_unavailable"}], "actions": [], "duplicate_content": []}),
                  status(taxonomy_review={"provisional": True})):
            self.assertEqual(M.classify_handoff(s, PROFILE)["decision"], "abort")

    def test_reporting_changes_rebuild_marts_strict_and_auto_deploy_opt_in(self):
        r = M.classify_handoff(status(reporting_rebuild_required=True), {"deployment": {"enabled": True}, "handoff": {"deploy": "auto"}})
        self.assertEqual((r["marts"], r["deploy"]), ("rebuild_strict", "auto"))
