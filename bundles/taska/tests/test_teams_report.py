"""Teams findings page and dry-run sync. Invented data only."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))
sys.path.insert(0, os.path.dirname(__file__))

from taska_lib import teams_report as R
from taska_lib.sources import teams_common as TC
import test_teams_common as T

TASKA = os.path.join(os.path.dirname(__file__), "../skills/run/taska.py")


def _store_with_findings():
    d = T.store_with_tree()
    TC.process(d, T.Cfg(), {}, [T.msg("m1", "Final stakeholder contact list is shared with the client <b>now</b>"),
                                T.msg("m2", "Thinking about billing maps for later this month", ts="2026-10-21T17:00:00Z")],
               cursor_key="k", judge_fn=T.fake_judge({("Final stakeholder", "S001"): {"done": (0.8, 0.1)},
                                                      ("Thinking", "S002"): {"done": (0.2, 0.2)}}), card=T.CARD)
    return d


def test_page_lists_every_finding_and_counts_rejections():
    d = _store_with_findings()
    html = R.render(d)
    f = [json.loads(l) for l in open(os.path.join(d, "out", "teams-findings.jsonl"))]
    assert all(x["id"] in html for x in f)
    assert "<b>1</b>below threshold" in html and "<b>1</b>proposals" in html


def test_page_escapes_message_text():
    html = R.render(_store_with_findings())
    assert "<b>now</b>" not in html


def test_page_is_written_to_out():
    d = _store_with_findings()
    p = R.write(d, "2026-10-22")
    assert p.endswith("teams-findings-2026-10-22.html") and os.path.exists(p)


def _snapshot(root):
    return {os.path.relpath(os.path.join(dp, f), root): open(os.path.join(dp, f), "rb").read()
            for dp, _, fs in os.walk(root) for f in fs}


def test_dry_sync_leaves_the_live_store_untouched(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    subprocess.run([sys.executable, TASKA, "--project", str(proj), "init", "--name", "Demo"], check=True, capture_output=True)
    subprocess.run([sys.executable, TASKA, "--project", str(proj), "status"], capture_output=True)  # store exists, as in real use
    before = _snapshot(proj / ".taska")
    r = subprocess.run([sys.executable, TASKA, "--project", str(proj), "sync", "--dry"], capture_output=True, text=True)
    out = json.loads(r.stdout)
    assert out["dry_store"] and not out["dry_store"].startswith(str(proj))
    assert _snapshot(proj / ".taska") == before
