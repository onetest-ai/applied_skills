"""Recorded golden-bank trials and the free replay gate.

record_bank(bank, tier, k, project=None) -> {"dir", "recorded", "skipped"}
    Paid: k trials per task through bank.run_bank, then every finished (`done`) trial, passing or failing, is filed
    under recorded/<bank version>/<task>-<n>/ with a CHANGELOG line. A refresh writes the new recordings first and
    only then replaces the recordings of the tasks it ran; a failure leaves the old ones in place.
save(trial_dir, dest, slim=True) -> dest
    One recording: taska.toml, the copy's .taska ledger (events.jsonl, every evidence/*.json), trace.json, grade.json
    and recording.json (how the trial was graded: code_has_second_read, whether its meeting was processed). Slim blanks
    `text` and `turns` of every evidence item but the task's own. Nothing keeps a session id or an absolute local path:
    trace.json loses `session`, a path under the copy becomes copy-relative, any other absolute path becomes `<abs>`
    and a session-like id `<session>`. Never the Claude session or subagent transcripts, the trial's meta.json, the
    copy's wrapper script or any other store file.
strays(rec) -> [paths]
    What a recording must not hold: a top-level entry other than KEEP and .taska, and inside .taska anything other than
    events.jsonl and evidence/*.json (so any other *.jsonl).
replay_recorded(bank, recs) -> [{"recording", "task", "status", "recorded", "replayed", "drift"}]
    Free: re-grades each recording's task through bank.replay (a one-task view, with the recorded grading flags) and
    names each verdict that differs from the recorded one.
check_recorded(root, bank_dir) -> [problems]
    The gate: every recorded version has its bank file, replays without drift and holds no strays.
promote(tasks, grades) / promote_bank(bank, version, note) -> (tasks | new bank file, changes)
    A reviewed check turns golden when at least 2 of 3 recordings (2/3 of them when there are more) give it the same
    verdict; a check pending the owner's ruling never does. Difficulty is easy (found in every recording), medium
    (some) or hard (none). Both need 3 recordings; with fewer the check is left as it is. The bank is immutable, so
    the result is a new bank version. It keeps the source version's write inputs (bank.vN.meta.json, from
    generate.py write) with `promoted_from`, so it regenerates from the same seeds.
"""
import argparse
import contextlib
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path

_HERE = Path(__file__).resolve().parent
EVALS = _HERE.parent
RUN_DIR = EVALS.parent / "skills" / "run"
for _p in (RUN_DIR, EVALS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bank as B  # noqa: E402
import run as RUN  # noqa: E402
from taska_lib import config as C, store as S  # noqa: E402
from taska_lib.workbench import code_version  # noqa: E402

GOLDEN = _HERE
RECORDED = _HERE / "recorded"
STORE = ".taska"
HEADER = "recording.json"
KEEP = ("taska.toml", "trace.json", "grade.json", HEADER)
MIN_RECORDINGS = 3
SESSION_KEYS = ("session", "session_id", "sessionId")
# transcript text, quotes and every field the fold matches by equality (criteria, revisions, waiting): kept verbatim
VERBATIM = ("text", "turns", "title", "quote", "quote_norm", "ac_text", "ac_add", "waiting_for", "what", "who")
_NAME = re.compile(r"^(.+)-(\d+)$")
_ABS = re.compile(r"(?<![\w/:.~-])/(?:[^\s/\"'<>|:;,()\[\]{}]+/)+[^\s/\"'<>|:;,()\[\]{}]*")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

Verdict = tuple[str, str | None]
RunBank = Callable[..., Path]
Json = dict | list | str | int | float | bool | None


def recordings(version_dir: Path) -> list[Path]:
    """The recordings of one bank version, in name order."""
    d = Path(version_dir)
    return sorted(p for p in d.iterdir() if p.is_dir() and _NAME.match(p.name)) if d.is_dir() else []


def recorded_versions(root: Path) -> list[Path]:
    r = Path(root)
    return sorted(d for d in r.iterdir() if d.is_dir() and not d.name.startswith(".") and recordings(d)) if r.is_dir() else []


def strays(rec: Path) -> list[str]:
    rec = Path(rec)
    out = [p.name for p in rec.iterdir() if p.name not in KEEP and p.name != STORE]
    store = rec / STORE
    for p in store.iterdir() if store.is_dir() else []:
        if p.name == "evidence" and p.is_dir():
            out += [f"{STORE}/evidence/{q.name}" for q in p.iterdir() if not (q.is_file() and q.suffix == ".json")]
        elif not (p.name == "events.jsonl" and p.is_file()):
            out.append(f"{STORE}/{p.name}")
    return sorted(out)


def _scrub_str(s: str, copies: list[str]) -> str:
    for c in copies:
        s = s.replace(c + "/", "").replace(c, ".")
    return _UUID.sub("<session>", _ABS.sub("<abs>", s))


def scrub(obj: Json, copies: list[str]) -> Json:
    """No session id and no absolute local path; transcript text and quotes are kept verbatim."""
    if isinstance(obj, dict):
        return {k: v if k in VERBATIM else scrub(v, copies) for k, v in obj.items() if k not in SESSION_KEYS}
    if isinstance(obj, list):
        return [scrub(v, copies) for v in obj]
    return _scrub_str(obj, copies) if isinstance(obj, str) else obj


def _write(path: Path, body: Json) -> None:
    path.write_text(json.dumps(body, indent=1, ensure_ascii=False), encoding="utf-8")


def save(trial_dir: Path, dest: Path, evidence: str, slim: bool = True) -> Path:
    """File one finished trial of the task on `evidence` as a recording at dest (which must not exist)."""
    trial_dir, dest = Path(trial_dir), Path(dest)
    copy = Path(json.loads((trial_dir / "meta.json").read_text(encoding="utf-8"))["copy"])
    copies = sorted({str(copy), str(copy.resolve())}, key=len, reverse=True)
    store = Path(C.load(copy).store)
    grade = json.loads((trial_dir / "grade.json").read_text(encoding="utf-8"))
    evs = S.events(store)
    dest.mkdir(parents=True)
    (dest / "taska.toml").write_text(_scrub_str((copy / "taska.toml").read_text(encoding="utf-8"), copies),
                                     encoding="utf-8")
    (dest / STORE / "evidence").mkdir(parents=True)
    (dest / STORE / "events.jsonl").write_text(
        "".join(json.dumps(scrub(e, copies), ensure_ascii=False) + "\n" for e in evs), encoding="utf-8")
    for f in sorted((store / "evidence").glob("*.json")):
        item = json.loads(f.read_text(encoding="utf-8"))
        if slim and item.get("id") != evidence:
            item |= {k: "" if k == "text" else [] for k in ("text", "turns") if k in item}
        _write(dest / STORE / "evidence" / f.name, scrub(item, copies))
    if not slim:
        for f in (store / "evidence").glob("*.md"):
            shutil.copy2(f, dest / STORE / "evidence" / f.name)
    _write(dest / "trace.json", scrub(json.loads((trial_dir / "trace.json").read_text(encoding="utf-8")), copies))
    _write(dest / "grade.json", scrub(grade, copies))
    _write(dest / HEADER, {"task": grade["task"], "code_has_second_read": True, "processed": evidence in S.processed(evs)})
    return dest


def verdicts(grade: dict) -> dict[str, Verdict]:
    """{check id: (result, ruled_result)}: a check pending the owner's ruling keeps its verdict as ruled_result."""
    return {c["id"]: (c.get("result"), c.get("ruled_result")) for c in grade.get("checks") or []}


def _drift(rec: dict, rep: dict) -> list[str]:
    a, b = verdicts(rec), verdicts(rep)
    out = sorted(cid for cid in set(a) | set(b) if a.get(cid) != b.get(cid))
    return out + [k for k in ("status", "pass") if rec.get(k) != rep.get(k)]


def replay_recorded(bank_path: Path, recs: list[Path]) -> list[dict]:
    """Grade each recording's task again (bank.replay on a one-task view, graded as the trial was: nothing is written,
    no model is called) and compare every verdict with the recorded one."""
    rows = []
    for rec in map(Path, recs):
        recorded = json.loads((rec / "grade.json").read_text(encoding="utf-8"))
        tid = recorded["task"]
        try:
            head = json.loads((rec / HEADER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            head = {}
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / rec.name
            stub.mkdir()
            _write(stub / "meta.json", {"project": str(rec.resolve()), "as_trial": True,
                                        "code_has_second_read": head.get("code_has_second_read", True)})
            result = B.replay(bank_path, [stub], [tid])
        rep = next((t for t in result["trials"] if t["task"] == tid),
                   {"status": "missing", "pass": False, "checks": [], "reason": f"{tid} is not a task of {bank_path}"})
        rows.append({"recording": rec.name, "task": tid, "status": rep["status"], "reason": rep.get("reason", ""),
                     "recorded": verdicts(recorded), "replayed": verdicts(rep), "drift": _drift(recorded, rep)})
    return rows


def check_recorded(root: Path, bank_dir: Path) -> list[str]:
    """Every problem of the recordings under root: a version without its bank file, drift, a stray file."""
    problems = []
    for vdir in recorded_versions(root):
        bank = Path(bank_dir) / f"bank.{vdir.name}.jsonl"
        if not bank.is_file():
            problems.append(f"recordings of {vdir.name} have no bank file {bank}")
            continue
        for row in replay_recorded(bank, recordings(vdir)):
            if row["drift"]:
                problems.append(f"{vdir.name}/{row['recording']}: replayed verdicts differ on {', '.join(row['drift'])}"
                                + (f" ({row['reason']})" if row["reason"] else ""))
        for rec in recordings(vdir):
            if strays(rec):
                problems.append(f"{vdir.name}/{rec.name} holds files a recording must not: {', '.join(strays(rec))}")
    return problems


@contextlib.contextmanager
def _scratch(bank: list[dict]) -> Iterator[Path]:
    """A fresh project synced from the seeds the bank's tasks were generated from (generate.build_project), removed
    afterwards."""
    from golden import generate as G
    tmp = Path(tempfile.mkdtemp(prefix="golden-record-"))
    try:
        yield G.build_project(bank, tmp / "project")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _project(project: Path | None, bank: list[dict]) -> contextlib.AbstractContextManager[Path]:
    return contextlib.nullcontext(Path(project)) if project else _scratch(bank)


def _swap(vdir: Path, staging: Path, recorded: list[str], old: Path) -> None:
    """Replace the recordings of the tasks that have new ones. The old ones are moved aside and deleted only once every
    new one is in place; a failed rename puts the new ones back in staging (kept for inspection) and the old ones
    back where they were."""
    fresh = {_NAME.match(n)[1] for n in recorded}
    moved: list[str] = []
    placed: list[str] = []
    try:
        old.mkdir(parents=True)
        for rec in recordings(vdir):
            if _NAME.match(rec.name)[1] in fresh:
                rec.rename(old / rec.name)
                moved.append(rec.name)
        vdir.mkdir(parents=True, exist_ok=True)
        for name in recorded:
            (staging / name).rename(vdir / name)
            placed.append(name)
    except BaseException:
        for name in reversed(placed):
            (vdir / name).rename(staging / name)
        for name in moved:
            (old / name).rename(vdir / name)
        old.rmdir()
        raise
    shutil.rmtree(old)
    staging.rmdir()


def record_bank(bank_path: Path, tier: str, k: int, project: Path | None = None, ai: list[str] | None = None,
                tasks: list[str] | None = None, parallel: int = 4, run: RunBank = B.run_bank,
                root: Path = RECORDED, note: str = "recorded") -> dict:
    """Paid (through `run`): k trials per task, every `done` trial filed as a recording, one CHANGELOG line."""
    head, bank = B.load_bank(bank_path)
    chosen = [t for t in bank if not tasks or t["task"] in tasks]
    with _project(project, chosen) as proj:   # run refuses a project whose meetings are not the bank's
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        out = run(proj, bank_path, tier, k, tasks, parallel, ai,
                  out_root=proj / "reports" / "evals" / "bank" / f"record-{head['version']}-{stamp}")
        root = Path(root)
        vdir = root / head["version"]
        staging = root / f".staging-{stamp}"
        evidence = {t["task"]: t["evidence"] for t in chosen}
        recorded, skipped = [], []
        try:
            for folder in sorted((Path(out) / "trials").iterdir()):
                g = json.loads((folder / "grade.json").read_text(encoding="utf-8"))
                if g.get("status") != "done":
                    skipped.append({"trial": folder.name, "status": g.get("status")})
                    continue
                save(folder, staging / folder.name, evidence[g["task"]])
                recorded.append(folder.name)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)   # only copies made by this run; nothing recorded is touched
            raise
        if recorded:
            _swap(vdir, staging, recorded, root / f".old-{stamp}")
    if recorded:
        with open(root / "CHANGELOG.md", "a", encoding="utf-8") as f:
            f.write(f"- {head['version']} ({dt.date.today().isoformat()}): {len(recorded)} recordings of "
                    f"{len({_NAME.match(n)[1] for n in recorded})} tasks, tier {tier}, k={k}, "
                    f"code {code_version(RUN_DIR)} — {note}\n")
    return {"dir": str(vdir), "recorded": recorded, "skipped": skipped}


def _verdict_of(cid: str, v: dict[str, Verdict]) -> tuple[Verdict | None, ...]:
    return v.get(cid), v.get(cid + ":dup")


def _found(cid: str, v: dict[str, Verdict]) -> bool:
    result, ruled = v.get(cid) or (None, None)
    return (ruled or result) == "pass"


def promote(tasks: list[dict], grades: dict[str, list[dict]]) -> tuple[list[dict], list[dict]]:
    """Status and difficulty of every check from its task's recorded grades; only `done` grades are recordings."""
    out, changes = [], []
    for t in tasks:
        t = json.loads(json.dumps(t))
        done = [verdicts(g) for g in grades.get(t["task"], []) if g.get("status") == "done"]
        if len(done) >= MIN_RECORDINGS:
            for typ in B.CHECK_TYPES[:3]:
                for c in t.get(typ, []):
                    before = (c.setdefault("status", "draft"), c.get("difficulty"))
                    found = sum(_found(c["id"], v) for v in done)
                    c["difficulty"] = "easy" if found == len(done) else "hard" if found == 0 else "medium"
                    top = Counter(_verdict_of(c["id"], v) for v in done).most_common(1)[0][1]
                    if before[0] == "reviewed" and not c.get("pending_ruling") and 3 * top >= 2 * len(done):
                        c["status"] = "golden"
                    if (c["status"], c["difficulty"]) != before:
                        changes.append({"task": t["task"], "id": c["id"], "status": c["status"],
                                        "difficulty": c["difficulty"], "was": {"status": before[0], "difficulty": before[1]}})
        out.append(t)
    return out, changes


def promote_bank(bank_path: Path, version: str, note: str, root: Path = RECORDED) -> tuple[Path, list[dict]]:
    """A new bank version whose statuses and difficulties follow the recordings of this version."""
    head, bank = B.load_bank(bank_path)
    grades: dict[str, list[dict]] = {}
    for rec in recordings(Path(root) / head["version"]):
        g = json.loads((rec / "grade.json").read_text(encoding="utf-8"))
        grades.setdefault(g["task"], []).append(g)
    tasks, changes = promote(bank, grades)
    from golden import generate as G
    new = B.write_bank(Path(bank_path).parent, tasks, version, dt.date.today().isoformat(), note)
    source = G.meta_path(bank_path)
    if source.is_file():   # the seeds, rulings and reviewed ids that regenerate it are the source version's
        G.write_meta(new, json.loads(source.read_text(encoding="utf-8")) | {
            "version": version, "promoted_from": head["version"], "date": dt.date.today().isoformat(), "note": note})
    return new, changes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Record golden-bank trials (paid), check them (free) or promote from them.")
    ap.add_argument("--bank", required=True, type=Path)
    ap.add_argument("--tier", choices=list(RUN.TIERS))
    ap.add_argument("--k", type=int, choices=(1, 3), default=3)
    ap.add_argument("--project", type=Path, help="a project holding the bank's meetings (default: synced from its seeds)")
    ap.add_argument("--tasks", nargs="+")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--ai", nargs="+", help="the command that reaches Claude (default: taska ai)")
    ap.add_argument("--note", default="recorded")
    ap.add_argument("--yes", action="store_true", help="this costs model calls")
    ap.add_argument("--check", action="store_true", help="free: replay every recording against its bank file")
    ap.add_argument("--promote", metavar="VERSION", help="free: write bank.VERSION with statuses from the recordings")
    a = ap.parse_args(argv)
    if a.check:
        problems = check_recorded(RECORDED, a.bank.parent)
        print(json.dumps({"problems": problems}, ensure_ascii=False, indent=1))
        return 1 if problems else 0
    if a.promote:
        new, changes = promote_bank(a.bank, a.promote, a.note)
        print(json.dumps({"written": str(new), "changes": changes}, ensure_ascii=False, indent=1))
        return 0
    if not a.tier:
        ap.error("--tier is required to record")
    if not a.yes:
        print("recording costs model calls: re-run with --yes")
        return 2
    try:
        _, bank = B.load_bank(a.bank)
        with _project(a.project, [t for t in bank if not a.tasks or t["task"] in a.tasks]) as project:
            probe = [*a.ai, "--probe"] if a.ai else [*RUN.TASKA, "--project", str(project), "ai", "--probe"]
            if subprocess.run(probe).returncode != 0:
                print("Claude could not be reached: see the reasons above; nothing was recorded")
                return 1
            res = record_bank(a.bank, a.tier, a.k, project, a.ai, a.tasks, a.parallel, note=a.note)
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
