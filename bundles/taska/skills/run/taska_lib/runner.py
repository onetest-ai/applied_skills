"""One scheduled run (`taska run-schedule <name>`): the project's own steps ([[run.steps]] in taska.toml, filtered by
the schedule's kind), then — for a preview or a send — the read-only workbench copy, the docx when the project has a
report template and a prose file, the gate on that docx, and the delivery (deliver.json + mail.md). Every step is
recorded through runlog; a send is HELD, to the owner, when a required step or the gate failed. One run at a time
per project (.taska/run.lock); the steps never run from here twice.

A step's command may use {week} {dir} {project} {kind} {name} {python} {taska}."""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import deliver as DL, gate as GT, report as RP, runlog as RL, schedules as SCH, workbench as WB

STEP_KEYS = {"name", "command", "kinds", "required"}


def steps(cfg, kind):
    """The project's steps that apply to `kind`, validated; ValueError names the bad field."""
    out = []
    for i, s in enumerate((cfg.run or {}).get("steps") or []):
        unknown = set(s) - STEP_KEYS
        if unknown or not s.get("name") or not s.get("command"):
            raise ValueError(f"[[run.steps]] #{i + 1}: needs name and command; known keys {sorted(STEP_KEYS)}, got {sorted(s)}")
        kinds = s.get("kinds") or list(SCH.KINDS)
        if any(k not in SCH.KINDS for k in kinds):
            raise ValueError(f"[[run.steps]] {s['name']}: kinds must be among {SCH.KINDS}")
        if kind in kinds:
            out.append({"name": s["name"], "command": s["command"], "required": bool(s.get("required", True))})
    return out


def week_for(kind, today):
    """The week a run reports on: an update works on this week; a preview/send follow the schedules rule."""
    if kind == "update":
        return (today - dt.timedelta(days=today.weekday())).isoformat()
    return SCH.week_for(today)


def expand(command, cfg, s, week, wd):
    return command.format(week=week, dir=str(wd), project=str(cfg.project_dir), kind=s.kind, name=s.name,
                          python=sys.executable, taska=str(Path(__file__).resolve().parent.parent / "taska.py"))


def _run_shell(cmd, cwd):
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)


def _owner(cfg, s):
    """The owner's address: [report].owner, else where the project's preview schedule goes, else this schedule's To."""
    if (cfg.report or {}).get("owner"):
        return cfg.report["owner"]
    prev = next((x for x in cfg.schedules if x.kind == "preview" and x.to), None)
    return prev.to[0] if prev else (s.to[0] if s.to else None)


def run(cfg, s, week=None, today=None, run=None, reports=None, dry_run=False):
    """Run schedule `s`. Returns {week, dir, kind, steps, verdict: done|ready|held|busy, held_why, deliver, commands}."""
    today = today or dt.date.today()
    week = week or week_for(s.kind, today)
    reports = Path(reports) if reports else cfg.project_dir / "reports"
    wd = reports / week
    plan = steps(cfg, s.kind)
    commands = [expand(x["command"], cfg, s, week, wd) for x in plan]
    out = {"week": week, "dir": str(wd), "kind": s.kind, "steps": [], "verdict": "done", "held_why": None, "deliver": None,
           "commands": commands}
    if dry_run:
        return out
    lock = Path(cfg.store) / "run.lock"
    if SCH.lock_busy(lock):
        return dict(out, verdict="busy", held_why=f"a run is in progress ({lock})")
    shutil.rmtree(lock, ignore_errors=True)
    lock.mkdir(parents=True)
    (lock / "pid").write_text(str(os.getpid()))
    try:
        wd.mkdir(parents=True, exist_ok=True)
        RL.begin(wd)
        failed = []
        runner = run or _run_shell
        for x, cmd in zip(plan, commands):
            rc, text = runner(cmd, str(cfg.project_dir))
            RL.step(wd, x["name"], rc, text[-300:])
            out["steps"].append({"name": x["name"], "rc": rc, "required": x["required"]})
            if rc and x["required"]:
                failed.append(x["name"])
        if s.kind in ("preview", "send"):
            _report(cfg, s, week, wd, reports, out, failed)
        RL.finish(wd)
        if s.kind == "send":
            out["verdict"] = "held" if failed else "ready"
            out["held_why"] = f"failed: {', '.join(failed)}" if failed else None
        return out
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def _report(cfg, s, week, wd, reports, out, failed):
    """The workbench copy, the docx (when there is a template and prose), the gate, the delivery."""
    try:
        WB.export(cfg.store, cfg, wd / f"workbench {dt.date.today().isoformat()}.html")
        RL.step(wd, "workbench", 0, "read-only copy")
    except Exception as e:   # the copy is never the reason a run stops
        RL.step(wd, "workbench", 1, f"{type(e).__name__}: {e}")
    prose = wd / "prose.json"
    docx = None
    if (cfg.report or {}).get("docx_glob") and prose.exists():
        title = (cfg.report or {}).get("title") or cfg.name
        docx = wd / f"{title} weekly report {dt.date.today().isoformat()}.docx"
        try:
            r = RP.build(cfg, week, prose, docx)
            RL.step(wd, "build_docx", 1 if r.get("errors") else 0, f"{len(r.get('slots', []))} slots, {len(r.get('errors', []))} failed")
        except Exception as e:
            RL.step(wd, "build_docx", 1, f"{type(e).__name__}: {e}")
            docx = None
            failed.append("build_docx")
    if docx and docx.exists():
        try:
            g = GT.run_gate(cfg, week, docx, reports, reader=(cfg.report or {}).get("greet") or "the reader")
            RL.step(wd, "gate", g["exit"], f"failed: {', '.join(g['failed'])}" if g["failed"] else "passed")
            if g["exit"]:
                failed.append("gate")
        except Exception as e:
            RL.step(wd, "gate", 1, f"{type(e).__name__}: {e}")
            failed.append("gate")
    owner = _owner(cfg, s)
    held = s.kind == "send" and bool(failed)
    to = owner if (s.kind == "preview" or held) else (s.to[0] if s.to else owner)
    if not to:
        RL.step(wd, "deliver", 1, "no address: set the schedule's To or [report].owner")
        failed.append("deliver")
        return
    d = DL.deliver(cfg, wd, to, schedule=s, status=str(wd / "run-status.partial.json") if (wd / "run-status.partial.json").exists() else None,
                   kind=s.kind)
    if s.kind == "send" and not held and owner and owner not in d["cc"] and owner != to:
        d["cc"] = list(d["cc"]) + [owner]   # the owner stays in copy of every send
    if held:
        d["subject"] = f"HELD: {d['subject']} — {', '.join(failed)} failed"
        d["needs_attention"] = True
    if held or (s.kind == "send" and owner):
        (wd / "deliver.json").write_text(json.dumps(d, indent=1, ensure_ascii=False))
        (wd / "mail.md").write_text(DL.mail_md(d, dict(s.include)))
    RL.step(wd, "deliver", 0, f"{s.kind} to {to}" + (" (HELD)" if held else ""))
    out["deliver"] = str(wd / "deliver.json")
