import json
from scribe_fixtures import (fixture_brain_db, setup_mini_project, load, doc, publish_seed,
                             plan_stale, draft, claim_blocks)
from scribe_lib import claims
from scribe_lib.basedoc import base_task
from scribe_lib.merge import merge_task

DETAILS = "D. [RAG:2] <!-- c:bbbb0001 -->"


def _setup(tmp_path, overview):
    proj = setup_mini_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    config, data = load(proj)
    inst, tpl = data["instances"]["m1"], data["templates"]["mini-profile"]
    publish_seed(config, inst, doc("m1", 1, "Mini m1", {"overview": overview, "details": DETAILS}), 1, docx_sha=None)
    base_task(config, "m1", inst, tpl)
    plan_stale(config, "m1", ["overview"], ["details"])
    return config, data


def _merge(config, data, body):
    draft(config, "m1", "overview", body)
    merge_task(config, "m1", data["instances"], data["templates"])
    m = json.loads((config.work_dir / "m1" / "merge.json").read_text())["sections"]["overview"]
    return m, claim_blocks(config.work_dir / "m1" / "next.md")["overview"]


def test_same_text_new_tag_is_recited_and_keeps_id(tmp_path):
    config, data = _setup(tmp_path, "Alpha fact. [FILE:a.docx#p1] <!-- c:aaaa0001 -->")
    m, blocks = _merge(config, data, "Alpha fact. [RAG:77] <!-- c:aaaa0001 -->\n")
    assert (m["recited"], m["kept"], m["reworded"]) == (1, 0, 0)
    assert blocks[0]["claim_id"] == "aaaa0001"


def test_human_claim_with_swapped_tag_loses_origin(tmp_path):
    config, data = _setup(tmp_path, "Human fixed claim. [RAG:1] <!-- c:aaaa0001 origin=human_modified -->")
    m, blocks = _merge(config, data, "Human fixed claim. [RAG:999] <!-- c:aaaa0001 -->\n")
    assert blocks[0]["origin"] is None and m["recited"] == 1


def test_human_claim_unchanged_keeps_origin(tmp_path):
    config, data = _setup(tmp_path, "Human fixed claim. [RAG:1] <!-- c:aaaa0001 origin=human_modified -->")
    m, blocks = _merge(config, data, "Human fixed claim. [RAG:1] <!-- c:aaaa0001 -->\n\nNew. [RAG:5]\n")
    assert blocks[0]["origin"] == "human_modified" and m["kept"] == 1


def test_invented_id_is_ignored_and_claim_matched_by_text(tmp_path):
    config, data = _setup(tmp_path, "Alpha fact about the widget. [RAG:1] <!-- c:aaaa0001 -->")
    m, blocks = _merge(config, data, "Alpha fact about the widgets. [RAG:1] <!-- c:deadbeef -->\n")
    assert blocks[0]["claim_id"] == "aaaa0001"
    assert (m["reworded"], m["added"], m["dropped_by_check"], m["dropped_by_model"]) == (1, 0, 0, 0)
    assert "deadbeef" not in (config.work_dir / "m1" / "next.md").read_text()


def test_duplicate_ids_are_reminted(tmp_path):
    config, data = _setup(tmp_path, "Alpha. [RAG:1] <!-- c:aaaa0001 -->")
    _, blocks = _merge(config, data, "Alpha. [RAG:1] <!-- c:aaaa0001 -->\n\nTotally new text. [RAG:2] <!-- c:aaaa0001 -->\n")
    ids = [b["claim_id"] for b in blocks]
    assert ids[0] == "aaaa0001" and ids[1] != "aaaa0001" and len(set(ids)) == 2


def test_tie_break_is_deterministic():
    base = claims.parse_blocks("Same text. [RAG:1] <!-- c:bbbb0002 -->\n\nSame text. [RAG:1] <!-- c:aaaa0002 -->\n")
    drafted = claims.parse_blocks("Same text! [RAG:1]\n")
    r = claims.match_claims(base, drafted, "m1", "overview")
    assert r[0]["claim_id"] == "aaaa0002"


def test_base_parser_treats_mermaid_as_code_not_human_claim(tmp_path):
    fence = "```mermaid\nflowchart LR\n  A-->B\n```"
    config, _ = _setup(tmp_path, f"Alpha. [RAG:1] <!-- c:aaaa0001 -->\n\n{fence}")
    base_json = json.loads((config.work_dir / "m1" / "base.json").read_text())
    assert base_json["human_added"] == []
    assert fence in (config.work_dir / "m1" / "base.md").read_text()


def test_parse_units_never_turns_a_fence_into_a_unit():
    from scribe_lib.basedoc import parse_units
    body = "Alpha. [RAG:1] <!-- c:aaaa0001 -->\n\n```mermaid\nflowchart LR\n  A-->B\n```"
    units = parse_units(body, with_ids=True)
    assert [(u["claim_id"], u["origin"]) for u in units] == [("aaaa0001", None)]


def test_check_file_checks_a_human_claim_whose_file_tag_changed(tmp_path):
    from scribe_lib.checkfile import check_file_task
    proj = setup_mini_project(tmp_path)
    config, data = load(proj)
    inst = data["instances"]["m1"]
    base = doc("m1", 2, "Mini m1", {
        "overview": "- A human-corrected statement. [FILE:a.md#L1] <!-- c:cccc0001 origin=human_modified -->",
        "details": DETAILS,
    })
    work_dir = config.work_dir / "m1"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "base.md").write_text(base, encoding="utf-8")
    publish_seed(config, inst, base, 2, docx_sha=None)
    draft(config, "m1", "overview",
          "- A human-corrected statement. [FILE:b.md#L9] <!-- c:cccc0001 origin=human_modified -->\n", evidence=[])
    result = check_file_task(config, "m1", inst)
    assert result["human_origin_skipped"] == 0 and result["checked"] == 1
    assert [f["reason"] for f in result["failed"]] == ["missing evidence quote"]
