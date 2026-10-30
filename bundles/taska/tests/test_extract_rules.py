"""EXTRACT.md is the rule the nightly extractor follows. It must aim at the same definition of work as the readers, the
judges and the scorer (the user's tracking rule), and it must never cap or drop work said in a meeting."""
import re
import textwrap
from pathlib import Path

import pytest

from taska_lib import config as C, ledger as L, store as S

ROOT = Path(__file__).resolve().parent.parent
EXTRACT = (ROOT / "skills" / "run" / "EXTRACT.md").read_text(encoding="utf-8")
FLAT = " ".join(EXTRACT.split())


def _blocks(text):
    return {m.group(1): m.group(2) for m in
            re.finditer(r"<!-- READER-BLOCK:([\w-]+):START -->\n(.*?)\n<!-- READER-BLOCK:\1:END -->", text, re.S)}


@pytest.mark.parametrize("name", ["commitment", "not-work-bullets", "client-owed"])
def test_extract_carries_the_tracking_rule_byte_identical(name):
    ext = _blocks(EXTRACT)
    for doc in ("READER.md", "JUDGE.md"):
        other = _blocks((ROOT / "evals" / doc).read_text(encoding="utf-8"))
        assert ext.get(name) == other[name], f"EXTRACT.md block {name!r} differs from {doc}"


def test_no_count_cap():
    assert "rarely creates more than" not in EXTRACT


def test_work_is_never_silently_dropped():
    assert "never silently dropped" in FLAT


def test_owner_rule_for_room_recordings():
    assert "room recording" in FLAT and "never from the speaker tag" in FLAT


def test_conditional_decision_rule():
    assert "a conditional" in FLAT.lower() and "trigger date" in FLAT


def test_context_lists_room_recordings(tmp_path):
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
        [[people]]
        name = "Nora Quill"
    """))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    room = {"id": "2026-03-02_meetings_aa000001", "kind": "meeting", "at": "2026-03-02T10:00:00", "title": "Readout",
            "source": "meetings", "turns": [{"speaker": "Nora Quill", "ts": "00:00:01", "text": "word " * 2000}]}
    S.write_evidence(cfg.store, room)
    ctx = L.context(cfg.store, cfg)
    assert ctx["room_recordings"] == ["2026-03-02_meetings_aa000001"]


def test_extract_says_what_to_do_with_similar():
    assert "`similar`" in EXTRACT and "suggest_merge" in FLAT and "never keep both silently" in FLAT


def test_new_nodes_pass_the_tracking_rule_before_the_admission_bar():
    # Oct 30: 17 of 20 invented nodes were deck tweaks and booked calls; they have an owner or are a "session", so the
    # admission bar let them in although the rule says they are not work. The rule decides first, the bar second.
    p2 = EXTRACT[EXTRACT.index("## Pass 2"):EXTRACT.index("## Schema")]
    first = p2[:p2.index("**Admission bar:**")]
    assert "not work by the tracking rule" in first and "never created" in first
    assert "a `session` task is a deliverable session" in p2.lower()
