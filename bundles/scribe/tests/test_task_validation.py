"""Final-review I3/I4: task frontmatter is validated against task.schema.json's rules
(a `publish: proposed` typo used to auto-publish; `title` is a file name), `weekly:<dow>`
is a real cadence, and fan-out children get their own substituted titles."""
import pytest
from scribe_fixtures import fixture_brain_db, load, make_project, mini_task, write_text, MINI_TEMPLATE, doc, publish_seed

import scribe
from scribe_lib.config import load_config, validate_all

DETAILS = "D. [RAG:2] <!-- c:bbbb0001 -->"


def _proj(tmp_path, nodes=None):
    proj = make_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite", nodes=nodes))
    write_text(proj / "templates" / "mini-profile.tmpl.md", MINI_TEMPLATE)
    return proj


def test_bad_publish_mode_is_refused(tmp_path):
    proj = _proj(tmp_path)
    mini_task(proj, "m1", publish="proposed")
    with pytest.raises(ValueError, match=r"m1: publish must be one of \['auto', 'propose'\], got 'proposed'"):
        validate_all(load_config(proj))


@pytest.mark.parametrize("cadence", ["weekly", "weekly:monday", "hourly", "Daily"])
def test_bad_cadence_is_refused(tmp_path, cadence):
    proj = _proj(tmp_path)
    mini_task(proj, "m1", cadence=cadence)
    with pytest.raises(ValueError, match="m1: cadence must be one of"):
        validate_all(load_config(proj))


@pytest.mark.parametrize("title", ["../escape", "a/b", "a\\\\b", "..", ".hidden", "  ", "a..b"])
def test_unsafe_title_is_refused(tmp_path, title):
    proj = _proj(tmp_path)
    mini_task(proj, "m1", title=title)
    with pytest.raises(ValueError, match="m1: title"):
        validate_all(load_config(proj))


def test_nul_in_title_is_refused(tmp_path):
    proj = _proj(tmp_path)
    mini_task(proj, "m1", title="a\\0b")   # YAML double-quoted escape -> a NUL byte
    with pytest.raises(ValueError, match="NUL"):
        validate_all(load_config(proj))


def test_wrongly_typed_field_is_refused(tmp_path):
    proj = _proj(tmp_path)
    mini_task(proj, "m1")
    path = proj / "tasks" / "m1.task.md"
    path.write_text(path.read_text().replace("---\nnotes", "enabled: \"yes\"\n---\nnotes"))
    with pytest.raises(ValueError, match="'enabled' must be true or false"):
        validate_all(load_config(proj))


def test_valid_cadences_and_titles_pass(tmp_path):
    proj = _proj(tmp_path)
    mini_task(proj, "a", cadence="weekly:mon", title="Weekly digest v1.2")
    mini_task(proj, "b", cadence="manual", publish="propose")
    validate_all(load_config(proj))


def _weekly(tmp_path, monkeypatch, now, **state):
    monkeypatch.setenv("SCRIBE_NOW", now)
    proj = _proj(tmp_path)
    mini_task(proj, "m1", cadence="weekly:mon")
    config, data = load(proj)
    publish_seed(config, data["instances"]["m1"],
                 doc("m1", 1, "Mini m1", {"overview": "A. [RAG:1] <!-- c:aaaa0001 -->", "details": DETAILS}),
                 1, docx_sha=None, extra_state=state)
    return next(t for t in scribe.compute_plan(config, data)["tasks"] if t["id"] == "m1")


def test_weekly_is_due_on_its_weekday(tmp_path, monkeypatch):
    # 2026-01-05 is a Monday.
    task = _weekly(tmp_path, monkeypatch, "2026-01-05T02:00:00", built_at="2025-12-29T02:00:00")
    assert task["due"] is True and "cadence" in task["reasons"]


def test_weekly_is_not_due_on_another_weekday_even_with_changes(tmp_path, monkeypatch):
    # Tuesday, and the Brain lost a document since the last publish.
    task = _weekly(tmp_path, monkeypatch, "2026-01-06T02:00:00", built_at="2026-01-05T02:00:00",
                   brain_snapshot={"gone.md": "x"})
    assert "brain_changed" in task["reasons"] and task["due"] is False


def test_weekly_is_not_due_twice_on_its_weekday(tmp_path, monkeypatch):
    task = _weekly(tmp_path, monkeypatch, "2026-01-05T09:00:00",
                   built_at="2025-12-29T02:00:00", last_checked="2026-01-05T02:00:00",
                   brain_snapshot={"gone.md": "x"})
    assert "brain_changed" in task["reasons"] and task["due"] is False


def test_fanout_children_get_distinct_substituted_titles_and_files(tmp_path):
    proj = _proj(tmp_path, nodes=[("p", "Payments", "intent_l1", None), ("c1", "Refunds", "intent_l2", "p"),
                                  ("c2", "Charge/backs", "intent_l2", "p")])
    write_text(
        proj / "tasks" / "fan.task.md",
        "---\ntemplate: mini-profile@1\nid: fan\ntitle: \"{{name}} profile\"\nparams:\n  name: x\n"
        "for_each:\n  taxonomy_under: Payments\nout: \"Profiles\"\npublish: auto\n"
        "approved_children: [c1, c2]\n---\n",
    )
    config, data = load(proj)
    c1, c2 = data["instances"]["fan--c1"], data["instances"]["fan--c2"]
    assert c1["title"] == "Refunds profile"
    assert c2["title"] == "Charge-backs profile"   # path-safe: a title is a file name
    assert c1["params"]["name"] == "Refunds"
    assert (c1["out"], c2["out"]) == ("Profiles/Refunds", "Profiles/Charge-backs")


def test_fanout_templated_out_is_substituted_not_nested(tmp_path):
    proj = _proj(tmp_path, nodes=[("p", "Payments", "intent_l1", None), ("c1", "Refunds", "intent_l2", "p")])
    write_text(
        proj / "tasks" / "fan.task.md",
        "---\ntemplate: mini-profile@1\nid: fan\ntitle: \"{{name}}\"\nparams:\n  name: x\n"
        "for_each:\n  taxonomy_under: Payments\nout: \"Profiles/{{name}}-docs\"\npublish: auto\n---\n",
    )
    _, data = load(proj)
    assert data["instances"]["fan--c1"]["out"] == "Profiles/Refunds-docs"
    assert data["instances"]["fan--c1"]["title"] == "Refunds"
