import json
import pytest
from scribe_fixtures import (fixture_brain_db, make_project, write_text, write_json, MINI_TEMPLATE, mini_task, load)
from scribe_lib.config import validate_all, load_config
from scribe_lib import registry
from scribe_lib.pack import prepare_task
import scribe


def _proj(tmp_path, nodes=None):
    proj = make_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite", nodes=nodes))
    write_text(proj / "templates" / "mini-profile.tmpl.md", MINI_TEMPLATE)
    return proj


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


def test_fan_out_expands_taxonomy_children(tmp_path):
    proj = _proj(tmp_path, nodes=[("p", "Payments", "intent_l1", None), ("c1", "Refunds", "intent_l2", "p"),
                                  ("c2", "Autopay", "intent_l2", "p")])
    write_text(proj / "tasks" / "fan.task.md",
               "---\ntemplate: mini-profile@1\nid: fan\ntitle: \"{{name}} profile\"\nparams:\n  name: x\n"
               "for_each:\n  taxonomy_under: Payments\nout: \"Profiles\"\npublish: auto\n---\n")
    config, data = load(proj)
    assert sorted(data["instances"]) == ["fan[c1]", "fan[c2]"]
    assert data["instances"]["fan[c2]"]["params"]["name"] == "Autopay"
    assert data["instances"]["fan[c2]"]["out"] == "Profiles/Autopay"


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


def test_prepare_refuses_when_upstream_failed_this_run(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_NOW", "2026-01-05T09:00:00")
    proj = _proj(tmp_path)
    mini_task(proj, "up"); mini_task(proj, "down", upstream=["up"])
    config, _ = load(proj)
    write_json(config.out_root / "_runs" / "2026-01-05.json", [{"task": "up", "status": "failed", "reason": "accept"}])
    r = prepare_task(config, "down")
    assert r["status"] == "skipped" and r["reason"] == "upstream_failed" and r["upstream"] == ["up"]


def test_enable_disable_and_promote(tmp_path):
    proj = _proj(tmp_path)
    mini_task(proj, "m1")
    config, _ = load(proj)
    registry.set_enabled(config, "m1", False)
    assert "enabled: false" in (proj / "tasks" / "m1.task.md").read_text()
    path = registry.promote(config, "m1", "widget-profile")
    text = path.read_text()
    assert "id: widget-profile" in text and "{{name}}" in text and "Widget" not in text
