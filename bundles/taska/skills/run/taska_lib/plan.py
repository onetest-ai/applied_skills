"""Epics from the project's plan: a json/csv/md file, a list in taska.toml, or nothing."""
import csv
import json
import re
from pathlib import Path


def load_epics(plan_cfg):
    if plan_cfg.get("epics"):
        return [{"name": str(x)} for x in plan_cfg["epics"]]
    f = plan_cfg.get("file")
    if not f:
        return []
    p = Path(f)
    if not p.is_file():
        raise ValueError(f"[plan].file not found: {p}")
    if p.suffix == ".json":
        j = json.loads(p.read_text())
        acts = j.get("activities", j) if isinstance(j, dict) else j
        return [{k: a.get(k) for k in ("name", "start", "end", "kind", "phase")} for a in acts if a.get("name")]
    if p.suffix == ".csv":
        rows = list(csv.DictReader(p.open()))
        return [{"name": r.get("name") or r.get("epic"), "start": r.get("start") or None, "end": r.get("end") or None,
                 "phase": r.get("phase") or None} for r in rows if (r.get("name") or r.get("epic"))]
    out = []
    for line in p.read_text().splitlines():  # markdown: "- Name (2026-09-07..2026-09-27)" or "- Name"
        m = re.match(r"^\s*[-*]\s+(.+?)(?:\s*\((\d{4}-\d{2}-\d{2})\s*(?:\.\.|to|–|-)\s*(\d{4}-\d{2}-\d{2})\))?\s*$", line)
        if m:
            out.append({"name": m.group(1).strip(), "start": m.group(2), "end": m.group(3)})
    return out
