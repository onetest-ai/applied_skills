"""Workbench timeline: one old meeting must not stretch the strip over years of empty weeks, and the year must be
visible where the reader looks. The strip's week plan is a pure JS function (between PURE markers in
workbench.html), run here with node. Invented dates."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parents[1] / "skills" / "run" / "workbench.html"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")


def _js(expr):
    src = HTML.read_text()
    m = re.search(r"/\*PURE:START\*/(.*?)/\*PURE:END\*/", src, re.S)
    assert m, "the pure helpers must sit between PURE markers"
    r = subprocess.run(["node", "-e", m.group(1) + f"\nconsole.log(JSON.stringify({expr}));"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_long_empty_stretch_collapses_into_one_gap():
    plan = _js('weekPlan(["2023-10-06", "2026-08-03", "2026-08-10"])')
    gaps = [w for w in plan if w.get("gap")]
    assert len(gaps) == 1 and "2 yr" in gaps[0]["gap"], gaps
    assert len(plan) < 10, f"{len(plan)} columns for three meetings"


def test_year_is_labelled_right_after_a_gap():
    plan = _js('weekPlan(["2023-10-06", "2026-08-03", "2026-08-10"])')
    i = next(k for k, w in enumerate(plan) if w.get("gap"))
    assert plan[0]["label"] == "Oct 2023" and plan[i + 1]["label"] == "Aug 2026", plan


def test_short_quiet_stretch_is_not_collapsed():
    plan = _js('weekPlan(["2026-08-03", "2026-08-24"])')          # two empty weeks between: keep them visible
    assert not [w for w in plan if w.get("gap")] and len(plan) == 4, plan


def test_fmt_date_shows_year_only_when_it_differs():
    assert _js('[fmtDate("2023-10-06", "2026-10-15"), fmtDate("2026-10-06", "2026-10-15"), fmtDate("2026-10-15", "2026-10-15", true)]') \
        == ["Oct 6, 2023", "Oct 6", "Oct 15, 2026"]


def test_manual_edits_are_never_gated_on_the_viewed_call():
    """Core promise: the manager can change anything by hand from any view. Edits always apply to the current tree
    (the server stamps them on the latest evidence), so no control may be disabled because a past call is shown,
    except the criteria checkboxes: they send a number that can differ in the current tree (test_workbench_version.py)."""
    src = HTML.read_text()
    assert not re.search(r"const ro\s*=.*latest\(\)", src), "edit controls are disabled outside the latest call"
    assert "LIVE && latest()" not in src, "an edit control is still gated on the latest call"


def test_deliverables_view_matches_pack_rules_and_follows_the_viewed_call():
    """Parity with deliverables.py (one rule set, two renderers) and timeline-awareness: a file that lands after
    the viewed call is not shown as landed yet."""
    from taska_lib.deliverables import deliverables
    calls = [{"id": "m1", "evidence_kind": "meeting", "date": "2026-03-02", "title": "Daily"},
             {"id": "f1", "evidence_kind": "file", "date": "2026-03-04", "title": "Report v1"},
             {"id": "f2", "evidence_kind": "file", "date": "2026-03-06", "title": "Report v2"}]
    hist = {"S1": [{"evidence": "f1", "applied": True, "by": "agent"}, {"evidence": "f2", "applied": True, "by": "agent"}],
            "S2": [{"evidence": "f2", "applied": False, "by": "agent"}],
            "S3": [{"evidence": "f2", "applied": True, "by": "human"}]}
    nodes = {k: {"id": k, "kind": "story", "title": k, "state": st, "internal_owner": None, "due": None,
                 "ac": [{"text": "a", "met": True}, {"text": "b", "met": False}]}
             for k, st in [("S1", "in_progress"), ("S2", "open"), ("S3", "open"), ("S4", "merged")]}
    nodes["T1"] = {"id": "T1", "kind": "task", "title": "t", "state": "open"}
    ev = {c["id"]: {"kind": c["evidence_kind"], "date": c["date"], "title": c["title"]} for c in calls}
    py = {d["id"]: d for d in deliverables({k: dict(n, history=hist.get(k, [])) for k, n in nodes.items()}, ev)}
    js = {d["id"]: d for d in _js(f"deliverableRows({json.dumps(nodes)}, {json.dumps(hist)}, {json.dumps(calls)}, 2)")}
    # `first_draft_due` and `rag` feed the client pack only (pack.build_client); the workbench never shows them,
    # so the twin compares on every field the view renders
    py = {i: {k: v for k, v in d.items() if k not in ("first_draft_due", "rag")} for i, d in py.items()}
    assert js == py, (js, py)
    at_f1 = {d["id"]: d["landed"] for d in _js(
        f"deliverableRows({json.dumps(nodes)}, {json.dumps(hist)}, {json.dumps(calls)}, 1)")}
    assert at_f1["S1"] == {"date": "2026-03-04", "title": "Report v1"}, at_f1   # v2 has not landed yet at call 2
