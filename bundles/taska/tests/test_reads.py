"""Reads are versioned per meeting (spec 2026-11-03 read versions): a processed meeting records which reads it has had,
`pending` backfills a missing read by itself inside a window anchored on the newest processed item and bounded per run,
`backfill` requests more or older ones, and `doctor` reports the gaps. Every name below is invented."""
import textwrap
import types

import pytest

import test_leftovers as TL
from conftest import run_cli
from taska_lib import config as C, ledger as L, reads as R, store as S

TURNS = [{"speaker": "Nora Quill", "ts": "00:00:00", "text": "the dock schedule was discussed"}]


def _proj(tmp_path, run=""):
    (tmp_path / "taska.toml").write_text(textwrap.dedent("""
        [project]
        name = "Harbor Logistics"
    """) + (f"[run]\n{run}\n" if run else ""))
    cfg = C.load(tmp_path)
    S.ensure(cfg.store)
    return cfg


def _meeting(cfg, day, n=1, processed=True, **extra):
    eid = f"{day}_meetings_{n:08d}"
    S.write_evidence(cfg.store, {"id": eid, "kind": "meeting", "at": f"{day}T10:00:00", "title": "Daily sync",
                                 "source": "meetings", "turns": TURNS})
    if processed:
        S.append(cfg.store, [dict({"type": "processed", "evidence": eid, "by": "agent", "note": "",
                                   "at": f"{day}T12:00:00"}, **extra)])
    return eid


def _rows(tmp_path):
    return [(r["id"], r["need"], r.get("why")) for r in run_cli(tmp_path, "pending")["items"]]


def _gaps(tmp_path):
    return run_cli(tmp_path, "pending")["gaps"]["decisions"]


def test_the_registry_has_the_decision_read_for_meetings():
    assert R.READS["decisions"].version == 1 and R.READS["decisions"].kinds == frozenset({"meeting"})


def test_done_records_the_read_and_its_version(tmp_path):
    cfg = TL._project(tmp_path)
    S.append(cfg.store, [{"type": "processed", "evidence": TL.EV, "by": "agent", "note": "", "at": "2026-03-02T12:00:00",
                          "decisions": "pending"}])
    assert run_cli(tmp_path, "done", "--evidence", TL.EV, *TL._none_for(1, 2, 3))["status"] == "ok"
    p = TL._processed(cfg)
    assert p["reads"] == {"decisions": 1} and "decisions_read" in p


def test_legacy_decisions_read_is_version_one():
    evs = [{"type": "processed", "evidence": "m1", "decisions_read": {"1": "recorded"}},
           {"type": "processed", "evidence": "m2"}]
    assert L.reads_of(evs) == {"m1": {"decisions": 1}}


def test_a_reprocess_clears_the_reads():
    evs = [{"type": "processed", "evidence": "m1", "reads": {"decisions": 1}},
           {"type": "reprocess", "evidence": "m1"},
           {"type": "processed", "evidence": "m1", "decisions": "pending"}]
    assert L.reads_of(evs) == {}


def test_a_gap_in_the_window_is_pending_as_backfill(tmp_path):
    cfg = _proj(tmp_path)
    a, b = _meeting(cfg, "2026-03-01"), _meeting(cfg, "2026-03-10")
    assert _rows(tmp_path) == [(a, "decisions", "backfill"), (b, "decisions", "backfill")]
    g = _gaps(tmp_path)
    assert g["in_window"] == 2 and g["waiting"] == 0 and g["outside_window"] == 0 and g["oldest"] == "2026-03-01"


def test_a_gap_outside_the_window_is_counted_not_pending(tmp_path):
    cfg = _proj(tmp_path)
    _meeting(cfg, "2026-02-01")
    _meeting(cfg, "2026-03-10", reads={"decisions": 1})
    assert _rows(tmp_path) == []
    g = _gaps(tmp_path)
    assert g["in_window"] == 0 and g["outside_window"] == 1 and g["oldest"] == "2026-02-01"


def test_the_window_is_configurable(tmp_path):
    cfg = _proj(tmp_path, "backfill_days = 40")
    a = _meeting(cfg, "2026-02-01")
    _meeting(cfg, "2026-03-10", reads={"decisions": 1})
    assert _rows(tmp_path) == [(a, "decisions", "backfill")]


def test_the_cap_takes_the_oldest_and_the_rest_wait(tmp_path):
    cfg = _proj(tmp_path)
    ids = [_meeting(cfg, f"2026-03-{d:02d}") for d in range(1, 8)]
    assert _rows(tmp_path) == [(i, "decisions", "backfill") for i in ids[:5]]
    g = _gaps(tmp_path)
    assert g["in_window"] == 7 and g["waiting"] == 2


def test_the_cap_is_configurable_and_can_be_zero(tmp_path):
    cfg = _proj(tmp_path, "backfill_max = 0")
    _meeting(cfg, "2026-03-01")
    assert _rows(tmp_path) == [] and _gaps(tmp_path)["waiting"] == 1


def test_the_window_is_anchored_on_the_newest_processed_item(tmp_path):
    cfg = _proj(tmp_path)
    a, b = _meeting(cfg, "2026-01-01"), _meeting(cfg, "2026-01-05")
    c = _meeting(cfg, "2026-03-01", processed=False)   # newer, but not processed: not the anchor
    assert _rows(tmp_path) == [(a, "decisions", "backfill"), (b, "decisions", "backfill"), (c, "extract", None)]


def test_an_extracted_meeting_is_pending_whatever_the_cap(tmp_path):
    cfg = _proj(tmp_path, "backfill_max = 0")
    a = _meeting(cfg, "2026-03-01", decisions="pending")
    assert _rows(tmp_path) == [(a, "decisions", "extracted")]


def test_a_read_meeting_has_no_gap(tmp_path):
    cfg = _proj(tmp_path)
    _meeting(cfg, "2026-03-01", decisions_read={"1": "recorded"})
    _meeting(cfg, "2026-03-02", n=2, reads={"decisions": 1})
    assert _rows(tmp_path) == [] and _gaps(tmp_path)["in_window"] == 0


def test_a_newer_read_version_is_a_gap_again(tmp_path, monkeypatch):
    import taska as TK
    cfg = _proj(tmp_path)
    a = _meeting(cfg, "2026-03-01", reads={"decisions": 1})
    monkeypatch.setitem(R.READS, "decisions", R.Read("decisions", 2, frozenset({"meeting"})))
    rows, gaps = TK._queue(cfg.store, types.SimpleNamespace(run={}))
    assert [(r["id"], r["why"]) for r in rows] == [(a, "backfill")] and gaps["decisions"]["in_window"] == 1


def test_backfill_dry_run_lists_and_writes_nothing(tmp_path):
    cfg = _proj(tmp_path)
    a = _meeting(cfg, "2026-01-01")
    _meeting(cfg, "2026-03-01", reads={"decisions": 1})
    before = (cfg.store / "events.jsonl").read_bytes()
    r = run_cli(tmp_path, "backfill", "--read", "decisions", "--since", "2025-12-01", "--dry-run")
    assert r["status"] == "ok" and r["dry_run"] and r["requested"] == [a]
    assert (cfg.store / "events.jsonl").read_bytes() == before


def test_backfill_requests_ignore_window_and_cap_and_skip_read_meetings(tmp_path):
    cfg = _proj(tmp_path, "backfill_max = 0")
    a, b = _meeting(cfg, "2026-01-01"), _meeting(cfg, "2026-01-02")
    c = _meeting(cfg, "2026-03-01", reads={"decisions": 1})
    assert _rows(tmp_path) == []
    r = run_cli(tmp_path, "backfill", "--read", "decisions", "--since", "2025-12-01")
    assert r["requested"] == [a, b] and r["skipped"] == {c: "read at version 1"}
    e = [x for x in S.events(cfg.store) if x["type"] == "backfill"]
    assert len(e) == 1 and e[0]["read"] == "decisions" and e[0]["evidence_ids"] == [a, b] and "evidence" not in e[0] and e[0]["by"] == "agent"
    assert _rows(tmp_path) == [(a, "decisions", "requested"), (b, "decisions", "requested")]
    g = _gaps(tmp_path)
    assert g["outside_window"] == 0 and g["waiting"] == 0
    r = run_cli(tmp_path, "backfill", "--read", "decisions", "--since", "2025-12-01")   # idempotent: nothing new
    assert r["requested"] == [] and len([x for x in S.events(cfg.store) if x["type"] == "backfill"]) == 1


def test_a_requested_meeting_leaves_pending_once_read(tmp_path):
    cfg = TL._project(tmp_path)
    S.append(cfg.store, [{"type": "processed", "evidence": TL.EV, "by": "agent", "note": "", "at": "2026-03-02T12:00:00"}])
    run_cli(tmp_path, "backfill", "--read", "decisions", "--evidence", TL.EV)
    assert _rows(tmp_path) == [(TL.EV, "decisions", "requested")]
    run_cli(tmp_path, "done", "--evidence", TL.EV, *TL._none_for(1, 2, 3))
    assert _rows(tmp_path) == []
    assert run_cli(tmp_path, "backfill", "--read", "decisions", "--evidence", TL.EV)["skipped"] == {TL.EV: "read at version 1"}


def test_backfill_by_days_counts_back_from_the_newest_processed_item(tmp_path):
    cfg = _proj(tmp_path, "backfill_max = 0")
    _meeting(cfg, "2026-01-01")
    b = _meeting(cfg, "2026-02-20")
    _meeting(cfg, "2026-03-01", reads={"decisions": 1})
    assert run_cli(tmp_path, "backfill", "--read", "decisions", "--days", "20")["requested"] == [b]


def test_backfill_by_evidence_skips_an_unprocessed_meeting_and_refuses_an_unknown_id(tmp_path):
    cfg = _proj(tmp_path)
    a = _meeting(cfg, "2026-03-01", processed=False)
    assert run_cli(tmp_path, "backfill", "--read", "decisions", "--evidence", a)["skipped"] == {a: "not processed"}
    r = run_cli(tmp_path, "backfill", "--read", "decisions", "--evidence", "2026-03-01_nope", check=False)
    assert r["status"] == "error" and "2026-03-01_nope" in r["error"]


def test_decisions_since_is_an_alias_for_requested_and_doctor_says_it_is_deprecated(tmp_path):
    cfg = _proj(tmp_path, 'decisions_since = "2026-01-02"\nbackfill_max = 0')
    _meeting(cfg, "2026-01-01")
    b = _meeting(cfg, "2026-01-02")
    _meeting(cfg, "2026-03-01", reads={"decisions": 1})
    assert _rows(tmp_path) == [(b, "decisions", "requested")]
    d = run_cli(tmp_path, "doctor")
    assert any("deprecated" in w and "taska backfill" in w and "decisions_since" in w
               and "window backfill is off while decisions_since is set" in w for w in d["deprecated"])


def test_decisions_since_turns_window_backfill_off(tmp_path):
    cfg = _proj(tmp_path, 'decisions_since = "2026-03-05"')
    _meeting(cfg, "2026-03-01")
    b = _meeting(cfg, "2026-03-06")
    assert _rows(tmp_path) == [(b, "decisions", "requested")]
    g = _gaps(tmp_path)
    assert g["in_window"] == 1 and g["waiting"] == 1


@pytest.mark.parametrize("args", [["--since", "2026-13-01"], ["--since", "20261001"], ["--since", "soon"],
                                  ["--days", "-1"], ["--days", "x"]])
def test_backfill_refuses_a_bad_since_or_days_with_an_error(tmp_path, args):
    cfg = _proj(tmp_path)
    _meeting(cfg, "2026-03-01")
    r = run_cli(tmp_path, "backfill", "--read", "decisions", *args, check=False)
    assert r.get("status") == "error" and "Traceback" not in str(r)


def test_doctor_reports_the_read_gaps(tmp_path):
    cfg = _proj(tmp_path, "backfill_max = 1")
    _meeting(cfg, "2026-01-01")
    _meeting(cfg, "2026-02-20")
    _meeting(cfg, "2026-02-21")
    _meeting(cfg, "2026-03-01", reads={"decisions": 1})
    r = run_cli(tmp_path, "doctor")["reads"]["decisions"]
    assert r["in_window"] == 2 and r["waiting"] == 1 and r["outside_window"] == 1 and r["oldest"] == "2026-01-01"
    assert r["ok"] is False and "deprecated" not in run_cli(tmp_path, "doctor")


def test_doctor_reads_are_ok_with_nothing_waiting(tmp_path):
    cfg = _proj(tmp_path)
    _meeting(cfg, "2026-03-01")
    r = run_cli(tmp_path, "doctor")["reads"]["decisions"]
    assert r["ok"] is True and r["in_window"] == 1 and r["oldest"] == "2026-03-01"


@pytest.mark.parametrize("bad", ["-1", '"5"', "true", "1.5"])
@pytest.mark.parametrize("key", ["backfill_days", "backfill_max"])
def test_backfill_settings_must_be_non_negative_whole_numbers(tmp_path, key, bad):
    (tmp_path / "taska.toml").write_text(f'[project]\nname = "X"\n[run]\n{key} = {bad}\n')
    with pytest.raises(C.ConfigError, match=key):
        C.load(tmp_path)


def test_backfill_settings_accept_zero(tmp_path):
    (tmp_path / "taska.toml").write_text('[project]\nname = "X"\n[run]\nbackfill_days = 0\nbackfill_max = 0\n')
    assert C.load(tmp_path).run == {"backfill_days": 0, "backfill_max": 0}


def test_the_run_summary_reports_backfill_reads_and_names_the_command():
    from conftest import TASKA_ROOT
    skill = " ".join((TASKA_ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8").split())
    assert "decisions_since" not in skill
    assert '`extracted` (just extracted), `backfill`' in skill and "`requested` (asked for with `$TASKA backfill`)" in skill
    assert "`$TASKA backfill --read decisions --since YYYY-MM-DD` asks for them" in skill
    readme = " ".join((TASKA_ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "`backfill --read decisions" in readme and "`[run].decisions_since` is deprecated" in readme


def test_second_reads_go_extracted_then_requested_then_backfill():
    from conftest import TASKA_ROOT
    skill = " ".join((TASKA_ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8").split())
    order = ("second reads go by `why`: every `extracted` row first, then `requested`, then `backfill`, oldest first "
             "within each, so a backfill read never pushes out a newly extracted meeting's read")
    top = skill[:skill.index("1. `$TASKA doctor`")]
    step5 = skill[skill.index("**Second read.**"):skill.index("**Dependencies.**")]
    assert order in top and "in the same order: `extracted`, then `requested`, then `backfill`" in step5
    summary = skill[skill.index("9. Report to the user"):]
    assert "how many second reads were dispatched this run by `why` (`extracted`, `requested`, `backfill`)" in summary
    assert "the `$TASKA pending` call at the start of the run (step 3), before the reads" in summary
    readme = " ".join((TASKA_ROOT / "README.md").read_text(encoding="utf-8").split())
    for s in ("`not processed`", "`not a meeting`", "`read at version N`", "`already requested`", "`requested`", "`skipped`"):
        assert s in readme[readme.index("`backfill --read decisions"):], s
