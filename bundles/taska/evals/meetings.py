"""Shared meeting machinery for the eval generators: dates, VTT assembly, filler draws and the sync of a fresh project."""
import datetime as dt
import json
import random
import subprocess
from pathlib import Path

_BASE_DATE = dt.date(2026, 3, 2)  # Monday


def meeting_date(seed: int) -> str:
    """ISO date derived from seed: advance `seed` weekdays from 2026-03-02."""
    d = _BASE_DATE
    n = int(seed)
    while n > 0:
        d += dt.timedelta(days=1)
        while d.isoweekday() > 5:          # skip Sat/Sun
            d += dt.timedelta(days=1)
        n -= 1
    return d.isoformat()


def ts(seconds: int) -> str:
    """Integer seconds -> 'HH:MM:SS'."""
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def vtt_cue(start: int, end: int, speaker: str, text: str) -> str:
    """One WebVTT cue."""
    return f"{ts(start)}.000 --> {ts(end)}.000\n<v {speaker}>{text}</v>"


def build_vtt(cues: list[tuple[int, int, str, str]]) -> str:
    """A WebVTT file from (start, end, speaker, text) cues."""
    return "WEBVTT\n\n" + "\n\n".join(vtt_cue(*c) for c in cues) + "\n"


def filler(rng: random.Random, pool: list[str], n: int) -> list[str]:
    """n draws from pool, in order."""
    return [rng.choice(pool) for _ in range(n)]


def _toml(people: list[dict], teams: list[dict], calls: Path) -> str:
    """The project's people, teams and meetings source; every string a TOML basic string (json.dumps escapes a
    backslash, a quote and control characters the way TOML reads them)."""
    def q(v: object) -> str:
        return json.dumps(v, ensure_ascii=False)
    out = ""
    for p in people:
        out += f'[[people]]\nname = {q(p["name"])}\nside = {q(p["side"])}\n'
    for t in teams:
        out += f'[[teams]]\nname = {q(t["name"])}\nmembers = {q(t["members"])}\n'
        if t.get("aliases"):
            out += f'aliases = {q(t["aliases"])}\n'
    return out + f'[sources.meetings]\nadapter = "meetings_folder"\npath = {q(str(calls))}\n'


def sync_project(vtts: dict[str, str], people: list[dict], out: Path, taska_cmd: list[str],
                 teams: list[dict] | None = None, name: str = "Harbor Logistics") -> dict[str, str]:
    """Write the VTTs into out/calls, init the project there with its people (and teams), run `taska sync` and return
    {file name: evidence id} (a meeting's evidence title is its file name without .vtt)."""
    out = Path(out)
    calls = out / "calls"
    calls.mkdir(parents=True, exist_ok=True)
    for file, vtt in vtts.items():
        (calls / file).write_text(vtt)
    subprocess.run([*taska_cmd, "--project", str(out), "init", "--name", name], capture_output=True, check=True)
    toml = out / "taska.toml"
    toml.write_text(toml.read_text() + "\n" + _toml(people, teams or [], calls))
    subprocess.run([*taska_cmd, "--project", str(out), "sync"], capture_output=True, check=True)
    r = subprocess.run([*taska_cmd, "--project", str(out), "pending"], capture_output=True, text=True, check=True)
    by_title = {row["title"]: row["id"] for row in json.loads(r.stdout).get("items") or []}
    return {file: by_title.get(Path(file).stem, "") for file in vtts}
