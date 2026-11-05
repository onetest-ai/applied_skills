"""evals/crossrun.py: one key item across many runs on one key: how often it is matched, how (quote or position only),
where it is lost, repeated owner and status errors, and stability's found-correctly verdict. Invented data only."""
import json
import sys

from conftest import TASKA_ROOT

EVALS = TASKA_ROOT / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import crossrun as X  # noqa: E402
import quality as Q  # noqa: E402


def _c(name, verdict, by="code"):
    return {"name": name, "verdict": verdict, "by": by, "why": ""}


def _item(cid, node, by=None, stage=None, owner=None, judged=None):
    checks = [_c("Found", "ok" if node else "no")]
    checks += [_c("Owner", owner)] if owner else []
    checks += [_c(Q.ASK["same_work"], judged, "judge")] if judged else []
    produced = {"node": node, "matched_by": by} if node else {"node": None, "stage": stage}
    return {"id": cid, "kind": "task · committed", "quote": f"{cid} was said", "expected": {"title": f"Title {cid}"},
            "produced": produced, "checks": checks}


def _rec(root, name, items, key_sha="k1sha"):
    rec = root / name
    (rec / "quality").mkdir(parents=True)
    (rec / "meta.json").write_text(json.dumps({"key_version": "v1", "key_sha": key_sha}))
    doc = {"schema": "eval-quality/1", "units": [{"id": "m1", "nodes": [{"id": "T1"}], "items": items}],
           "summary": {"found_correct": None, "judged_items": [0, 0], "judge_same_tier": [], "judge_agreement": {}}}
    (rec / "quality" / "quality.json").write_text(json.dumps(doc))
    return rec


def _three(tmp_path):
    never = "read, never proposed"
    return [_rec(tmp_path, "r1", [_item("k1", "T1", "quote", owner="no", judged="ok"), _item("k2", None, stage=never),
                                  _item("k3", "T3", "position"), _item("k4", "T4", "quote")]),
            _rec(tmp_path, "r2", [_item("k1", "T1", "quote", owner="no", judged="ok"), _item("k2", None, stage=never),
                                  _item("k3", "T3", "position"), _item("k4", None, stage=never)]),
            _rec(tmp_path, "r3", [_item("k1", "T1", "quote", owner="ok"), _item("k2", None, stage="meeting not processed yet"),
                                  _item("k3", "T3", "position"), _item("k4", "T4", "quote")])]


def test_each_key_item_is_classed_by_how_many_runs_match_it_and_how(tmp_path):
    out = X.crossrun(_three(tmp_path))
    rows = {r["id"]: r for r in out["items"]}
    assert (rows["k1"]["matched"], rows["k1"]["of"], rows["k1"]["class"]) == (3, 3, "steady")
    assert rows["k2"]["class"] == "blind spot" and rows["k2"]["stages"] == {"read, never proposed": 2, "meeting not processed yet": 1}
    assert rows["k3"]["class"] == "position only" and rows["k3"]["position"] == 3
    assert rows["k4"]["class"] == "flaky" and rows["k4"]["matched"] == 2
    assert rows["k1"]["owner_wrong"] == 2 and rows["k1"]["repeated"] == ["owner"]
    assert rows["k1"]["correctly"]["found"] == 2 and rows["k1"]["correctly"]["stable"] is False   # r3 has no verdict: a missing trial
    assert out["summary"]["classes"] == {"steady": 1, "blind spot": 1, "position only": 1, "flaky": 1}
    assert [r["id"] for r in out["items"]][:2] == ["k2", "k4"]   # blind spots, then flaky, first


def test_every_class_carries_the_action_it_supports(tmp_path):
    out = X.crossrun(_three(tmp_path))
    assert set(out["actions"]) == {"blind spot", "flaky", "position only", "steady", "repeated field error"}
    assert all(out["actions"][c] for c in out["actions"])


def test_runs_on_different_keys_are_refused_as_stability_refuses_them(tmp_path):
    recs = _three(tmp_path)[:2] + [_rec(tmp_path, "r9", [_item("k1", "T1", "quote")], key_sha="k2sha")]
    try:
        X.crossrun(recs)
    except ValueError as e:
        assert "different answer keys" in str(e)
    else:
        raise AssertionError("mixed keys were compared")


def test_the_cli_writes_json_and_a_page_with_the_shared_style(tmp_path, capsys):
    recs = _three(tmp_path)
    assert X.main(["--records", *map(str, recs), "--out", str(tmp_path / "x")]) == 0
    doc = json.loads((tmp_path / "x" / "crossrun.json").read_text())
    page = (tmp_path / "x" / "index.html").read_text()
    assert doc["schema"] == "eval-crossrun/1" and doc["summary"]["runs"] == 3
    import evalview
    assert evalview.STYLE in page and "fetch('crossrun.json'" in page


def test_by_key_picks_every_built_record_on_each_key(tmp_path):
    recs = _three(tmp_path)
    (recs[2] / "quality" / "quality.json").unlink()
    groups = X.by_key(tmp_path)
    assert {k: [r.name for r in v] for k, v in groups.items()} == {"v1": ["r1", "r2"]}


def test_by_key_leaves_out_a_run_that_left_key_meetings_unprocessed(tmp_path):
    recs = _three(tmp_path)
    (recs[1] / "score.json").write_text(json.dumps({"incomplete": True}))
    assert [r.name for r in X.by_key(tmp_path)["v1"]] == ["r1", "r3"]
