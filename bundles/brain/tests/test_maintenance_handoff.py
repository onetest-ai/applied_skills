import importlib.util, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "skills" / "brain-maintenance"
spec = importlib.util.spec_from_file_location("maintenance", HERE / "maintenance.py")
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)


class HandoffSkillTextTests(unittest.TestCase):
    def test_skill_pins_exit_2_is_expected_and_continues_to_handoff(self):
        """Review fix round 1, Important #7: step 2 (`maintenance.py status`)
        exits 2 whenever `ready` is false, which is also true for a mere
        `remove_candidate` or ambiguous move — both of which hand-off simply
        defers, not stops for. An agent following the interactive workflow's
        "stop if … ambiguous moves, or unreviewed removal candidates" text
        would stop hand-off runs that should have continued to classify and
        apply. Pin the sentence that overrides that for hand-off mode."""
        text = (HERE / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## Unattended hand-off mode", text)
        section = " ".join(text[text.index("## Unattended hand-off mode"):].split())
        self.assertIn("Exit 2 here is expected and normal in hand-off mode", section)
        self.assertIn("continue to step 3 regardless of `status`'s exit code", section)
        self.assertIn("only the `handoff` classification below decides apply vs. abort", section)


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

    def test_ambiguous_move_add_is_deferred_only_never_applied(self):
        """Review fix round 1, Important #3: the `add` half of a same-sha
        add+missing pair `build_status` could not collapse into an
        unambiguous `move` must not ALSO land in `apply` just because "add"
        is structurally safe — applying it anyway mints a duplicate source
        identity for content that is really still `old.pdf`, renamed."""
        s = status(source_plan={"roots": [{"status": "available"}], "duplicate_content": [],
                                "actions": [{"action": "add", "relative_path": "a.pdf", "sha256": "sha1"},
                                            {"action": "missing", "relative_path": "old.pdf", "sha256": "sha1"}]},
                   ambiguous_move_hashes=["sha1"])
        r = M.classify_handoff(s, PROFILE)
        self.assertEqual(r["decision"], "apply")
        self.assertNotIn("a.pdf", r["apply"])
        self.assertIn("sha1", [d["detail"] for d in r["defer"] if d["kind"] == "ambiguous_move"])

    def test_duplicate_content_add_is_deferred_only_never_applied(self):
        s = status(source_plan={"roots": [{"status": "available"}],
                                "actions": [{"action": "add", "relative_path": "copy2.pdf", "sha256": "shaX"}],
                                "duplicate_content": [{"sha256": "shaX", "paths": [
                                    {"root_key": "docs", "relative_path": "copy1.pdf"},
                                    {"root_key": "docs", "relative_path": "copy2.pdf"},
                                ]}]})
        r = M.classify_handoff(s, PROFILE)
        self.assertEqual(r["decision"], "apply")
        self.assertNotIn("copy2.pdf", r["apply"])
        self.assertEqual({d["kind"] for d in r["defer"]}, {"duplicate_content"})

    def test_deletion_limit_exceeded_blocker_aborts(self):
        r = M.classify_handoff(status(blockers=["deletion_limit_exceeded"]), PROFILE)
        self.assertEqual(r["decision"], "abort")
        self.assertIn("blocker: deletion_limit_exceeded", r["abort_reasons"])

    def test_manifest_invalid_blocker_aborts(self):
        r = M.classify_handoff(status(blockers=["manifest_invalid"]), PROFILE)
        self.assertEqual(r["decision"], "abort")
        self.assertIn("blocker: manifest_invalid", r["abort_reasons"])

    def test_legacy_unlinked_delete_and_unmanaged_and_empty_lane_blockers_abort(self):
        for blocker in ("legacy_unlinked_delete_forbidden", "unmanaged_parsed_documents", "required_lane_empty"):
            with self.subTest(blocker=blocker):
                r = M.classify_handoff(status(blockers=[blocker]), PROFILE)
                self.assertEqual(r["decision"], "abort")
                self.assertIn(f"blocker: {blocker}", r["abort_reasons"])

    def test_deferred_blockers_alone_do_not_abort(self):
        """`source_actions_need_human_resolution` (from a mere `remove_candidate`)
        and `ambiguous_source_move` are exactly what hand-off defers rather than
        aborts on — the blockers loop must not turn every deferrable status into
        an abort."""
        s = status(source_plan={"roots": [{"status": "available"}], "duplicate_content": [],
                                "actions": [{"action": "remove_candidate", "relative_path": "old.pdf"}]},
                   blockers=["source_actions_need_human_resolution", "ambiguous_source_move"])
        r = M.classify_handoff(s, PROFILE)
        self.assertEqual(r["decision"], "apply")


class ApplyPlanTests(unittest.TestCase):
    def test_apply_plan_contains_only_apply_classified_actions(self):
        """Review fix round 1, Important #4: `source_registry.cmd_apply` treats
        every add/content_change/move in whatever plan file it is given as
        structurally safe (it has no subset flag), so a filtered plan is what
        keeps a deferred ambiguous-move/duplicate-content add, or a
        remove_candidate/missing action, out of `./brain source apply`."""
        s = status(source_plan={
            "roots": [{"status": "available"}], "version": 1, "created_at": "2026-01-01T00:00:00Z",
            "config_sha256": "cfg-abc123",
            "actions": [
                {"action": "add", "relative_path": "a.pdf", "sha256": "sha1", "root_key": "docs"},
                {"action": "add", "relative_path": "amb.pdf", "sha256": "sha2", "root_key": "docs"},
                {"action": "missing", "relative_path": "amb-old.pdf", "sha256": "sha2", "root_key": "docs"},
                {"action": "remove_candidate", "relative_path": "gone.pdf", "sha256": "sha3", "root_key": "docs"},
            ],
            "duplicate_content": [],
        }, ambiguous_move_hashes=["sha2"])
        r = M.classify_handoff(s, PROFILE)
        plan = M.build_apply_plan(s, r)

        self.assertEqual(plan["version"], 1)
        self.assertEqual(plan["config_sha256"], "cfg-abc123")
        self.assertEqual([a["relative_path"] for a in plan["actions"]], ["a.pdf"])
        self.assertTrue(all(a["action"] in ("add", "content_change", "move") for a in plan["actions"]))

    def test_apply_plan_is_accepted_by_source_registry_apply_validation(self):
        """Feeds the filtered plan through the real, unmodified
        `source_registry.build_plan`/`cmd_apply` validation path against a
        temp fixture db+root+config — no mutation beyond that temp fixture —
        confirming the filtered plan's shape (header fields + actions) is
        exactly what `cmd_apply` accepts, and that only the apply-classified
        `a.pdf` gets registered — `deferred.pdf` (excluded from `apply`
        because it is a `remove_candidate` in this fixture) never does, even
        though a freshly-generated plan would still carry it as an entry."""
        import json as _json
        import tempfile
        import types

        registry_path = HERE.parent / "knowledge-pipeline" / "source_registry.py"
        spec2 = importlib.util.spec_from_file_location("source_registry", registry_path)
        registry = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(registry)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            (docs / "a.pdf").write_bytes(b"safe content")
            brain_toml = root / "brain.toml"
            brain_toml.write_text(
                'version = 1\n[sources.roots.docs]\npath = "docs"\nmode = "import"\ninclude = ["**/*.pdf"]\n',
                encoding="utf-8",
            )
            db_path = root / "knowledge.sqlite"
            config = registry.load_config(brain_toml)

            con = registry.connect(str(db_path))
            registry.ensure_schema(con)
            con.commit()
            con.close()

            with registry.connect_readonly(str(db_path)) as con:
                built = registry.build_plan(con, config)

            actions_by_rel = {a["relative_path"]: a for a in built["actions"]}
            self.assertIn("a.pdf", actions_by_rel)

            # `classify_handoff` never sees `deferred.pdf` on disk (it never
            # existed) — it is injected directly into the status's source_plan
            # to exercise the filtering without a second real file on disk.
            built_with_extra = dict(built)
            built_with_extra["actions"] = built["actions"] + [
                {"action": "remove_candidate", "relative_path": "deferred.pdf", "root_key": "docs", "sha256": "deadbeef"}
            ]

            s = status(source_plan=built_with_extra, ambiguous_move_hashes=[])
            r = M.classify_handoff(s, PROFILE)
            self.assertEqual(r["apply"], ["a.pdf"])
            filtered = M.build_apply_plan(s, r)
            self.assertEqual([a["relative_path"] for a in filtered["actions"]], ["a.pdf"])

            plan_path = root / "apply_plan.json"
            plan_path.write_text(_json.dumps(filtered), encoding="utf-8")

            args = types.SimpleNamespace(plan=str(plan_path), db=str(db_path), config=str(brain_toml))
            registry.cmd_apply(args)

            with registry.connect_readonly(str(db_path)) as con:
                registered = {row[0] for row in con.execute("SELECT relative_path FROM sources")}
            self.assertEqual(registered, {"a.pdf"})
