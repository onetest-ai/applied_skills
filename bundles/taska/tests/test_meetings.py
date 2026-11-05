"""The shared meeting machinery (evals/meetings.py): VTT assembly, dates, filler, and the project sync. Invented names."""
import hashlib
import json
import random
import sys
from pathlib import Path

_TASKA_ROOT = Path(__file__).resolve().parents[1]
for _p in (_TASKA_ROOT / "skills" / "run", _TASKA_ROOT / "evals", _TASKA_ROOT / "evals" / "planted"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import meetings as M
from generate import generate, generate_planning, generate_workshop
from conftest import run_cli


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_meeting_date_skips_weekends():
    assert M.meeting_date(0) == "2026-03-02"
    assert M.meeting_date(4) == "2026-03-06"
    assert M.meeting_date(5) == "2026-03-09"


def test_ts_and_cue():
    assert M.ts(3725) == "01:02:05"
    assert M.vtt_cue(5, 9, "Nora Quill", "Hello.") == "00:00:05.000 --> 00:00:09.000\n<v Nora Quill>Hello.</v>"


def test_build_vtt_joins_cues_under_a_header():
    vtt = M.build_vtt([(0, 3, "A", "one."), (4, 7, "B", "two.")])
    assert vtt == ("WEBVTT\n\n00:00:00.000 --> 00:00:03.000\n<v A>one.</v>\n\n"
                   "00:00:04.000 --> 00:00:07.000\n<v B>two.</v>\n")


def test_filler_draws_with_the_given_rng():
    pool = ["a", "b", "c", "d"]
    rng = random.Random(3)
    assert M.filler(random.Random(3), pool, 6) == [rng.choice(pool) for _ in range(6)]
    assert M.filler(random.Random(3), pool, 0) == []


PLANTED_PINS = {
    "generate": ("2207ec497629343e4e7d45513afbf2c97857d591a7446d5647c092fa1b81812f",
                 "d364344d701ca53ea8b9dc4d2a35b78a6c61f8ccf0d40739be74a907fcb99642"),
    "generate_planning": ("d5381026db0bc711141ef3ad147af6c8eedabfdbaaa3e44f25071be6bf6726fe",
                          "a6e5c10087f6ce1dc79f716e31676c1675ad3123528818f1db20c4cfd586d247"),
    "generate_workshop": ("c67428533336fd9dec6115a5f134b75c55cf3ed1fb1f79c679d287cd229d4098",
                          "40c4dc82db672f4c7a947203deb082ff82c223ddc2d66d762ca980966b0697fa"),
}


def test_planted_output_is_byte_identical_after_the_move():
    for fn in (generate, generate_planning, generate_workshop):
        vtt, gold = fn(1)
        assert (_sha(vtt), _sha(json.dumps(gold, sort_keys=True))) == PLANTED_PINS[fn.__name__]


def test_sync_project_returns_evidence_ids_by_file(tmp_path):
    import conftest
    cmd = [sys.executable, str(conftest.RUN_DIR / "taska.py")]
    people = [{"name": "Nora Quill", "side": "internal"}, {"name": "Opal Brennick", "side": "client"}]
    vtts = {"2026-03-02 Harbor sync.vtt": "WEBVTT\n\n00:00:05.000 --> 00:00:09.000\n<v Nora Quill>Morning everyone, the yard walkthrough is booked for Tuesday.</v>\n",
            "2026-03-03 Harbor review.vtt": "WEBVTT\n\n00:00:05.000 --> 00:00:09.000\n<v Opal Brennick>The new badges arrive on Friday at the main gate.</v>\n"}
    out = tmp_path / "proj"
    out.mkdir()
    ids = M.sync_project(vtts, people, out, cmd)
    assert set(ids) == set(vtts)
    assert all(ids.values()) and len(set(ids.values())) == 2
    assert (out / "calls" / "2026-03-02 Harbor sync.vtt").exists()
    assert "Nora Quill" in (out / "taska.toml").read_text()


def test_toml_strings_are_valid_basic_strings_whatever_they_hold(tmp_path):
    import tomllib
    calls = tmp_path / 'od\\d "x"' / "calls"
    people = [{"name": 'Nora "N" Quill', "side": "internal"}]
    teams = [{"name": "Dock\\Team", "members": ["Nora Quill"], "aliases": ['dock "crew"']}]
    t = tomllib.loads(M._toml(people, teams, calls))
    assert t["sources"]["meetings"]["path"] == str(calls)
    assert t["people"][0]["name"] == 'Nora "N" Quill' and t["teams"][0]["name"] == "Dock\\Team"


def test_toml_strings_keep_characters_outside_the_basic_plane():
    import tomllib
    t = tomllib.loads(M._toml([{"name": "Nora 🚢 Quill", "side": "internal"}], [], Path("calls")))
    assert t["people"][0]["name"] == "Nora 🚢 Quill"
