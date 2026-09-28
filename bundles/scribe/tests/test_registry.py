import json
from datetime import datetime, timedelta, timezone

import pytest

from scribe_fixtures import (
    fixture_brain_db, make_project, write_text, write_json, MINI_TEMPLATE, mini_task, load,
    doc, publish_seed, draft,
)
from scribe_lib.config import validate_all, load_config, group_membership, parse_frontmatter
from scribe_lib import registry
from scribe_lib import report as report_mod
from scribe_lib import brain as brain_mod
from scribe_lib import claims
from scribe_lib.checktask import check_task_task
from scribe_lib.pack import prepare_task
import scribe


def _proj(tmp_path, nodes=None):
    proj = make_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite", nodes=nodes))
    write_text(proj / "templates" / "mini-profile.tmpl.md", MINI_TEMPLATE)
    return proj


def _fan_task(proj, *, extra_frontmatter: str = "") -> None:
    write_text(
        proj / "tasks" / "fan.task.md",
        "---\ntemplate: mini-profile@1\nid: fan\ntitle: \"{{name}} profile\"\nparams:\n  name: x\n"
        "for_each:\n  taxonomy_under: Payments\nout: \"Profiles\"\npublish: auto\n"
        f"{extra_frontmatter}---\n",
    )


def test_validate_refuses_escaping_and_overlapping_out_paths(tmp_path):
    proj = _proj(tmp_path)
    mini_task(proj, "a", out="../escape")
    with pytest.raises(ValueError, match="outside out_root"):
        validate_all(load_config(proj))
    mini_task(proj, "a", out="Discovery")
    mini_task(proj, "b", out="Discovery/Sub")
    with pytest.raises(ValueError, match="overlap"):
        validate_all(load_config(proj))


def test_groups_filter_which_tasks_run(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-05T09:00:00")
    proj = _proj(tmp_path)
    mini_task(proj, "disc-a"); mini_task(proj, "deliv-b")
    (proj / "scribe.toml").write_text((proj / "scribe.toml").read_text() +
        '\n[groups.discovery]\nenabled = true\ntasks = ["disc-*"]\n[groups.delivery]\nenabled = false\ntasks = ["deliv-*"]\n')
    config, data = load(proj)
    plan = scribe.compute_plan(config, data)
    due = {t["task"] for t in plan["tasks"] if t["due"]}
    assert due == {"disc-a"}


# ---------------------------------------------------------------- fix round 1 --
# issue 1: a task's own `enabled:` must hold even inside an enabled group.

def test_disable_blocks_task_even_inside_enabled_group(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-05T09:00:00")
    proj = _proj(tmp_path)
    mini_task(proj, "disc-a")
    (proj / "scribe.toml").write_text((proj / "scribe.toml").read_text() +
        '\n[groups.discovery]\nenabled = true\ntasks = ["disc-*"]\n')
    config, _ = load(proj)
    registry.set_enabled(config, "disc-a", False)

    config, data = load(proj)
    plan = scribe.compute_plan(config, data)
    row = next(t for t in plan["tasks"] if t["task"] == "disc-a")
    assert row["due"] is False and row["enabled"] is False

    listed = registry.list_rows(config)
    list_row = next(r for r in listed["tasks"] if r["task"] == "disc-a")
    assert list_row["enabled"] is False


# issue 2: fan-out never creates a task silently.

def test_fanout_child_starts_disabled_and_reported_until_approved(tmp_path):
    proj = _proj(tmp_path, nodes=[("p", "Payments", "intent_l1", None), ("c1", "Refunds", "intent_l2", "p"),
                                  ("c2", "Autopay", "intent_l2", "p")])
    _fan_task(proj)
    config, data = load(proj)

    assert sorted(data["instances"]) == ["fan--c1", "fan--c2"]
    assert data["instances"]["fan--c2"]["params"]["name"] == "Autopay"
    assert data["instances"]["fan--c2"]["out"] == "Profiles/Autopay"
    assert data["instances"]["fan--c1"]["enabled"] is False
    assert data["instances"]["fan--c2"]["enabled"] is False
    assert sorted(data["new_fanout_children"], key=lambda c: c["task"]) == [
        {"task": "fan--c1", "parent": "fan", "node": "c1"},
        {"task": "fan--c2", "parent": "fan", "node": "c2"},
    ]
    plan = scribe.compute_plan(config, data)
    assert sorted(plan["new_fanout_children"], key=lambda c: c["task"]) == sorted(
        data["new_fanout_children"], key=lambda c: c["task"]
    )
    listed = registry.list_rows(config)
    assert listed["new_fanout_children"]

    # enable records approval on the PARENT, not a nonexistent child file
    path = registry.set_enabled(config, "fan--c1", True)
    assert path.name == "fan.task.md"

    config, data = load(proj)
    assert data["instances"]["fan--c1"].get("enabled", True) is True
    assert [c["task"] for c in data["new_fanout_children"]] == ["fan--c2"]

    # disable is refused on a fan-out child — nothing to disable directly
    with pytest.raises(ValueError):
        registry.set_enabled(config, "fan--c1", False)


def test_fanout_child_already_published_stays_enabled_without_approval(tmp_path):
    proj = _proj(tmp_path, nodes=[("p", "Payments", "intent_l1", None), ("c1", "Refunds", "intent_l2", "p")])
    _fan_task(proj)
    config, data = load(proj)
    publish_seed(
        config, data["instances"]["fan--c1"],
        doc("fan--c1", 1, "Refunds profile", {"overview": "x [RAG:1] <!-- c:aaaa0001 -->", "details": "y"}),
        1, docx_sha=None,
    )

    config, data = load(proj)
    assert data["instances"]["fan--c1"].get("enabled", True) is True
    assert data["new_fanout_children"] == []


def test_fanout_missing_taxonomy_label_is_not_fatal(tmp_path):
    proj = _proj(tmp_path, nodes=[])
    _fan_task(proj)
    config, data = load(proj)  # must not raise

    assert data["instances"] == {}
    assert data["fanout_parent_missing"] == ["Payments"]
    plan = scribe.compute_plan(config, data)
    assert "fanout_parent_missing: Payments" in plan["notes"]
    listed = registry.list_rows(config)
    assert "fanout_parent_missing: Payments" in listed["notes"]


# issue 3: fan-out child ids must not contain brackets.

def test_fanout_child_ids_have_no_brackets_for_tags_and_group_globs(tmp_path):
    proj = _proj(tmp_path, nodes=[("p", "Payments", "intent_l1", None), ("c1", "Refunds", "intent_l2", "p")])
    _fan_task(proj)
    (proj / "scribe.toml").write_text((proj / "scribe.toml").read_text() +
        '\n[groups.fanout]\nenabled = true\ntasks = ["fan--*"]\n')
    config, data = load(proj)
    assert "fan--c1" in data["instances"]

    tag = "[TASK:fan--c1#c:aaaa0001]"
    assert claims.tags_in(f"See it. {tag}") == [tag]
    kind, value = claims.parse_tag(tag)
    assert kind == "TASK" and value == "fan--c1#c:aaaa0001"

    group, enabled = group_membership(config, "fan--c1")
    assert group == "fanout" and enabled is True


def test_downstream_citation_of_fanout_child_resolves_in_check_task(tmp_path):
    proj = _proj(tmp_path, nodes=[("p", "Payments", "intent_l1", None), ("c1", "Refunds", "intent_l2", "p")])
    _fan_task(proj)
    write_text(
        proj / "tasks" / "down.task.md",
        "---\ntemplate: mini-profile@1\nid: down\ntitle: \"Down\"\nparams:\n  name: \"Down\"\n"
        "inputs:\n  raw:\n    exclude: []\n  tasks: [fan--c1]\naudience: team\ncadence: on-brain-update\n"
        "publish: auto\nout: \"down\"\n---\nnotes\n",
    )
    config, data = load(proj)
    assert "fan--c1" in data["instances"]

    publish_seed(
        config, data["instances"]["fan--c1"],
        doc("fan--c1", 1, "Refunds profile", {"overview": "Live claim. [RAG:1] <!-- c:aaaa0001 -->", "details": "D."}),
        1, docx_sha=None,
    )
    draft(config, "down", "overview", "Uses fan-out claim. [TASK:fan--c1#c:aaaa0001]\n")
    r = check_task_task(config, "down", data["instances"])
    assert (r["checked"], r["passed"]) == (1, 1)
    assert r["failed"] == []


# issue 4: budget must actually limit a run.

def test_prepare_stamps_started_at_and_report_records_minutes(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-05T09:00:00")
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    proj = _proj(tmp_path)
    mini_task(proj, "m1")
    config, _ = load(proj)

    prepare_task(config, "m1")
    started_path = config.work_dir / "m1" / "started_at"
    assert started_path.is_file()

    past = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    started_path.write_text(past, encoding="utf-8")

    report_mod.record(config, task="m1", status="noop", reason="test")
    row = next(r for r in report_mod.rows(config) if r["task"] == "m1")
    assert row["minutes"] >= 2.9


def test_budget_seeds_from_minutes_used_today_and_never_starves_first_due(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-05T09:00:00")
    proj = _proj(tmp_path)
    for t in ("a", "b", "c"):
        mini_task(proj, t)
    (proj / "scribe.toml").write_text((proj / "scribe.toml").read_text().replace(
        "top_k = 8", "top_k = 8\nbudget_minutes = 15\ndefault_task_minutes = 10"))
    config, _ = load(proj)
    write_json(config.out_root / "_runs" / "2026-01-05.json", [{"task": "a", "status": "published", "minutes": 12}])

    config, data = load(proj)
    plan = scribe.compute_plan(config, data)
    rows = {t["task"]: t for t in plan["tasks"]}
    assert rows["a"]["deferred"] is False  # already ran this run — counted, not re-admitted
    assert rows["b"]["deferred"] is False  # first NEW due task: never starved even over budget
    assert rows["c"]["deferred"] is True   # 12 (a) + 10 (b) = 22 > 15: genuinely over budget


def test_plan_due_excludes_deferred_but_full_plan_still_lists_it(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-05T09:00:00")
    proj = _proj(tmp_path)
    for t in ("a", "b", "c"):
        mini_task(proj, t)
    (proj / "scribe.toml").write_text((proj / "scribe.toml").read_text().replace(
        "top_k = 8", "top_k = 8\nbudget_minutes = 20\ndefault_task_minutes = 10"))
    config, data = load(proj)

    due_plan = scribe.compute_plan(config, data, due_only=True)
    assert {t["task"] for t in due_plan["tasks"]} == {"a", "b"}

    full_plan = scribe.compute_plan(config, data)
    full = {t["task"]: t for t in full_plan["tasks"]}
    assert set(full) == {"a", "b", "c"}
    assert full["c"]["due"] is True and full["c"]["deferred"] is True


def test_budget_never_defers_a_lone_over_budget_first_due_task(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-05T09:00:00")
    proj = _proj(tmp_path)
    mini_task(proj, "a")
    (proj / "scribe.toml").write_text((proj / "scribe.toml").read_text().replace(
        "top_k = 8", "top_k = 8\nbudget_minutes = 5\ndefault_task_minutes = 100"))
    config, data = load(proj)
    plan = scribe.compute_plan(config, data)
    row = next(t for t in plan["tasks"] if t["task"] == "a")
    assert row["due"] is True and row["deferred"] is False


def test_budget_defers_instead_of_dropping(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-05T09:00:00")
    proj = _proj(tmp_path)
    for t in ("a", "b", "c"):
        mini_task(proj, t)
    (proj / "scribe.toml").write_text((proj / "scribe.toml").read_text().replace(
        "top_k = 8", "top_k = 8\nbudget_minutes = 20\ndefault_task_minutes = 10"))
    config, data = load(proj)
    rows = {t["task"]: t for t in scribe.compute_plan(config, data)["tasks"]}
    assert [rows[t]["deferred"] for t in ("a", "b", "c")] == [False, False, True]


# issue 5: a same-day retry of the upstream must unblock the dependant.

def test_prepare_refuses_when_upstream_failed_this_run(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-05T09:00:00")
    proj = _proj(tmp_path)
    mini_task(proj, "up"); mini_task(proj, "down", upstream=["up"])
    config, _ = load(proj)
    write_json(config.out_root / "_runs" / "2026-01-05.json", [{"task": "up", "status": "failed", "reason": "accept"}])
    r = prepare_task(config, "down")
    assert r["status"] == "skipped" and r["reason"] == "upstream_failed" and r["upstream"] == ["up"]


def test_same_day_retry_of_upstream_unblocks_dependant(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-05T09:00:00")
    monkeypatch.setattr(brain_mod, "search", lambda cfg, q, limit, tag: [])
    proj = _proj(tmp_path)
    mini_task(proj, "up"); mini_task(proj, "down", upstream=["up"])
    config, _ = load(proj)
    write_json(config.out_root / "_runs" / "2026-01-05.json", [
        {"task": "up", "status": "failed", "reasons": ["accept"]},
        {"task": "up", "status": "published", "version": 1, "reasons": []},
    ])
    r = prepare_task(config, "down")
    assert r["status"] == "ok"


def test_skipped_upstream_failed_propagates_transitively(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-05T09:00:00")
    proj = _proj(tmp_path)
    mini_task(proj, "up"); mini_task(proj, "mid", upstream=["up"]); mini_task(proj, "down", upstream=["mid"])
    config, _ = load(proj)
    write_json(config.out_root / "_runs" / "2026-01-05.json", [
        {"task": "up", "status": "failed", "reasons": ["accept"]},
        {"task": "mid", "status": "skipped", "reasons": ["upstream_failed"]},
    ])
    r = prepare_task(config, "down")
    assert r["status"] == "skipped" and r["reason"] == "upstream_failed" and r["upstream"] == ["mid"]


# issue 6: promote must not corrupt structure or silently overwrite.

def test_promote_does_not_touch_a_section_id_matching_a_param_value(tmp_path):
    proj = _proj(tmp_path)
    write_text(
        proj / "tasks" / "ov.task.md",
        "---\ntemplate: mini-profile@1\nid: ov\ntitle: \"Overview\"\nparams:\n  name: \"overview\"\n"
        "inputs:\n  raw:\n    exclude: []\naudience: team\ncadence: on-brain-update\npublish: auto\nout: \"ov\"\n---\nnotes\n",
    )
    config, _ = load(proj)
    path = registry.promote(config, "ov", "overview-profile")
    fm, _ = parse_frontmatter(path)
    section_ids = [s["id"] for s in fm["output"]["sections"]]
    assert "overview" in section_ids  # id untouched, never turned into {{name}}
    assert fm["id"] == "overview-profile"


def test_promote_parameterises_a_substring_match(tmp_path):
    proj = _proj(tmp_path)
    write_text(
        proj / "tasks" / "w.task.md",
        "---\ntemplate: mini-profile@1\nid: w\ntitle: \"Widget\"\nparams:\n  name: \"Widget\"\n"
        "inputs:\n  raw:\n    exclude: []\n    match: [\"Overview of Widget\"]\naudience: team\n"
        "cadence: on-brain-update\npublish: auto\nout: \"w\"\n---\nnotes\n",
    )
    config, _ = load(proj)
    path = registry.promote(config, "w", "widget-profile-2")
    fm, _ = parse_frontmatter(path)
    assert fm["inputs"]["raw"]["match"] == ["Overview of {{name}}"]
    assert "Widget" not in json.dumps(fm)


def test_promote_refuses_existing_template_id_unless_forced(tmp_path):
    proj = _proj(tmp_path)
    mini_task(proj, "m1")
    config, _ = load(proj)
    with pytest.raises(ValueError, match="already exists"):
        registry.promote(config, "m1", "mini-profile")

    path = registry.promote(config, "m1", "mini-profile", force=True)
    fm, _ = parse_frontmatter(path)
    assert fm["id"] == "mini-profile" and fm["version"] == 1


def test_enable_disable_and_promote(tmp_path):
    proj = _proj(tmp_path)
    mini_task(proj, "m1")
    config, _ = load(proj)
    registry.set_enabled(config, "m1", False)
    assert "enabled: false" in (proj / "tasks" / "m1.task.md").read_text()
    path = registry.promote(config, "m1", "widget-profile")
    text = path.read_text()
    assert "id: widget-profile" in text and "{{name}}" in text and "Widget" not in text
