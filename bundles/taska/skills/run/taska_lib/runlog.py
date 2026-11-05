"""A run's step record (reports/WEEK/run-status.json), written so a run stopped part-way never erases the last finished
run's record: the run writes run-status.partial.json while it works, and the partial record replaces run-status.json
only when the run finishes.

A report-only rebuild keeps the steps named in `keep` (the ingest steps: their failures must stay visible) and replaces
every earlier rebuild's steps, so one stale failure never marks a later, clean rebuild as failed."""
import json
import os
import time
from pathlib import Path

KEEP = ("model", "sync", "taska_run")


def _final(wd):
    return Path(wd) / "run-status.json"


def _partial(wd):
    return Path(wd) / "run-status.partial.json"


def _read(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def _write(p, d):
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    os.replace(tmp, p)


def begin(wd, report_only=False, keep=KEEP):
    prev = (_read(_final(wd)) or {}).get("steps", []) if report_only else []
    _write(_partial(wd), {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "pid": os.getppid(),
                          "steps": [dict(s, kept=True) for s in prev if s["id"] in keep]})


def step(wd, sid, rc, detail=""):
    d = _read(_partial(wd)) or {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "steps": []}
    d["steps"].append({"id": sid, "ok": int(rc) == 0, "detail": str(detail)[-300:]})
    _write(_partial(wd), d)


def finish(wd):
    d = _read(_partial(wd))
    if d is None:
        return
    d["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _write(_final(wd), d)
    _partial(wd).unlink(missing_ok=True)


def unfinished(wd):
    """The run that started and never finished, or None: {started, last_step, pid}."""
    d = _read(_partial(wd))
    if d is None:
        return None
    return {"started": d.get("started"), "pid": d.get("pid"),
            "last_step": d["steps"][-1]["id"] if d.get("steps") else None}


def failed(wd, this_run=False):
    """Failed step ids of the finished run; this_run leaves out the steps a report-only rebuild kept."""
    return [s["id"] for s in (_read(_final(wd)) or {}).get("steps", []) if not s.get("ok") and not (this_run and s.get("kept"))]
