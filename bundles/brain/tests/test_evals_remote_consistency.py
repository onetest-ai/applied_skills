import consistency_diff as cd


def _row(eval_id, provider, success):
    """One result row in the shape promptfoo actually writes for a config built by
    evals/generate_promptfoo.py: the eval id lives in testCase.metadata, never in vars."""
    return {"vars": {"question": "q", "context": "file:///x.js", "BRAIN_URL": "u", "query_suffix": "s"},
            "testCase": {"metadata": {"eval_id": eval_id, "category": "c"}},
            "provider": {"id": provider}, "success": success}


def _snap(ver, rows):
    # rows: list of (eval_id, provider, success)
    return {"knowledge_version": ver, "results": {"results": [_row(*r) for r in rows]}}


def test_summarize_reads_eval_id_from_real_promptfoo_rows():
    s = cd.summarize(_snap("v1", [("E001", "haiku", True), ("E001", "sonnet", True)]))
    assert set(s["by_question"]) == {"E001"}
    assert s["overall"]["accuracy"] == 1.0
    assert s["overall"]["questions"] == 1


def test_metadata_at_row_top_level_is_also_accepted():
    row = {"metadata": {"eval_id": "E9"}, "provider": {"id": "p"}, "success": True}
    assert "E9" in cd.summarize({"results": {"results": [row]}})["by_question"]


def test_rows_without_an_eval_id_are_counted_not_dropped_silently():
    snap = _snap("v1", [("E001", "haiku", True)])
    snap["results"]["results"].append({"vars": {}, "provider": {"id": "haiku"}, "success": False})
    assert cd.summarize(snap)["overall"]["unidentified_rows"] == 1


def test_snapshot_with_no_eval_ids_is_an_error_not_zero_accuracy():
    snap = {"knowledge_version": "v1", "results": {"results": [{"vars": {}, "success": True}]}}
    try:
        cd.summarize(snap)
    except ValueError as e:
        assert "eval_id" in str(e)
    else:
        raise AssertionError("expected ValueError for a snapshot with no eval ids")


def test_summarize_run_to_run_and_cross_model():
    s = cd.summarize(_snap("v1", [("q1", "haiku", True), ("q1", "haiku", True), ("q1", "opus", False)]))
    assert s["by_question"]["q1"]["run_to_run"]["haiku"] == 1.0
    assert s["by_question"]["q1"]["cross_model"] < 1.0


def test_diff_flags_regression():
    d = cd.diff(_snap("v1", [("q1", "haiku", True)]), _snap("v2", [("q1", "haiku", False)]))
    assert d["by_question"]["q1"]["temporal"] in ("regressed", "flipped")
    assert d["warn_same_version"] is False


def test_diff_reports_questions_dropped_from_the_current_run():
    d = cd.diff(_snap("v1", [("q1", "haiku", True), ("q2", "haiku", True)]),
                _snap("v2", [("q1", "haiku", True)]))
    assert d["by_question"]["q2"]["temporal"] == "missing"


def test_diff_warns_same_version():
    base = _snap("v1", [("q1", "haiku", True)])
    assert cd.diff(base, _snap("v1", [("q1", "haiku", True)]))["warn_same_version"] is True


def test_report_lists_missing_questions_and_makes_no_ambiguity_claim():
    cur = _snap("v2", [("q1", "haiku", True)])
    d = cd.diff(_snap("v1", [("q1", "haiku", True), ("q2", "haiku", True)]), cur)
    md = cd.render_md(cd.summarize(cur), d)
    assert "q2" in md
    assert "ambigu" not in md.lower()
