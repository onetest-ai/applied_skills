"""doctor: check the system tools `render` needs (the spec's `scribe_doctor`,
wired here as a `scribe.py` subcommand rather than a separate script — same
deterministic check, no project config needed).

Checks, each `{"tool", "found": bool, "version"?: str, "hint"?: str}`, plus the
mermaid check's own `"pinned"` (the `MERMAID_CLI` version `render.py` pins,
so the reported tool and the one `render` actually shells out to can never
silently drift apart):
  - `pandoc` (`pandoc --version`, first line)
  - `soffice` (`soffice --version`, first line; also tries `libreoffice`)
  - mermaid (`npx -y <MERMAID_CLI> --version`) — network/npm-backed, so this
    one can be slow on a cold npx cache; still required, since `render`
    cannot degrade past a failed diagram without `accept` catching it

All three are required for `render` to produce a usable docx+pdf; `doctor`
exits 1 if any is missing (checked by `scribe.py`, via `all_found`).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

from scribe_lib.render import MERMAID_CLI

if TYPE_CHECKING:
    from scribe_lib.config import Config

_TIMEOUT = 30
_MERMAID_TIMEOUT = 60

_HINTS = {
    "pandoc": "brew install pandoc  (or see https://pandoc.org/installing.html)",
    "soffice": "brew install --cask libreoffice",
    "mermaid": f"no install needed — `npx -y {MERMAID_CLI}` fetches it on "
    "first use; check that Node/npm and a network path to the npm registry are "
    "available, and that Chrome/Chromium is installed for puppeteer",
}


def _check(tool: str, cmd: list[str], which: str | None = None, timeout: int = _TIMEOUT) -> dict[str, Any]:
    if which and not shutil.which(which):
        return {"tool": tool, "found": False, "hint": _HINTS.get(tool, "")}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"tool": tool, "found": False, "hint": _HINTS.get(tool, "")}
    except subprocess.TimeoutExpired:
        return {"tool": tool, "found": False, "hint": f"{_HINTS.get(tool, '')} (timed out)"}
    output = (proc.stdout or proc.stderr or "").strip().splitlines()
    version = output[0] if output else ""
    found = proc.returncode == 0
    entry: dict[str, Any] = {"tool": tool, "found": found, "version": version}
    if not found:
        entry["hint"] = _HINTS.get(tool, "")
    return entry


def run_doctor() -> dict[str, Any]:
    pandoc = _check("pandoc", ["pandoc", "--version"], which="pandoc")

    soffice_bin = "soffice" if shutil.which("soffice") else ("libreoffice" if shutil.which("libreoffice") else None)
    if soffice_bin:
        soffice = _check("soffice", [soffice_bin, "--version"], which=soffice_bin)
    else:
        soffice = {"tool": "soffice", "found": False, "hint": _HINTS["soffice"]}

    mermaid = _check(
        "mermaid",
        ["npx", "-y", MERMAID_CLI, "--version"],
        which="npx",
        timeout=_MERMAID_TIMEOUT,
    )
    mermaid["pinned"] = MERMAID_CLI.rsplit("@", 1)[-1]

    checks = [pandoc, soffice, mermaid]
    all_found = all(c["found"] for c in checks)
    return {"status": "ok" if all_found else "error", "checks": checks, "all_found": all_found}


def handoff_status(config: "Config") -> dict[str, Any]:
    """Brain hand-off preflight (spec A1): the newest `*.json` report brain-maintenance's
    `handoff` subcommand wrote under `config.brain_handoff_dir`, by file name — the
    directory is dated `<YYYY-MM-DD>.json` per file, so a lexical sort is a chronological
    one. `stale_brain` is true when the latest hand-off run aborted (nothing was applied)
    OR when there is no report dated today (`config.now`) — scribe continues against the
    last good Brain rather than refusing to run either way, and the caller
    (`compute_plan`/`publish`) surfaces that instead of hiding it.

    No `brain_handoff_dir` configured is not an error — it just means this project isn't
    using hand-off mode: `{"latest": None, "decision": None, "stale_brain": False,
    "reasons": []}`. But once it IS configured, silence is not evidence of freshness
    (review fix round 2, Important #1): a directory with no reports yet, a report that
    cannot be parsed, or a latest report dated before today all mean nobody can say the
    Brain was refreshed today, and are reported the same way a `decision: "abort"` report
    is — `reasons: ["no hand-off report for <today>"]` — so scribe's stale-Brain Changes
    line fires even when the failure was upstream of `handoff` ever running (doctor or
    `status` itself failing before hand-off could classify anything; see
    `maintenance.py handoff --abort-reason` for the brain-side half of this fix).
    """
    empty = {"latest": None, "decision": None, "stale_brain": False, "reasons": []}
    handoff_dir = config.brain_handoff_dir
    if not handoff_dir or not handoff_dir.is_dir():
        return empty
    today = config.now[:10]
    no_report_reasons = [f"no hand-off report for {today}"]
    reports = sorted(handoff_dir.glob("*.json"))
    if not reports:
        return {**empty, "stale_brain": True, "reasons": no_report_reasons}
    latest = reports[-1]
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {**empty, "latest": str(latest), "stale_brain": True, "reasons": no_report_reasons}
    decision = data.get("decision")
    if decision == "abort":
        return {
            "latest": str(latest),
            "decision": decision,
            "stale_brain": True,
            "reasons": data.get("abort_reasons") or [],
        }
    if latest.stem < today:
        return {"latest": str(latest), "decision": decision, "stale_brain": True, "reasons": no_report_reasons}
    return {
        "latest": str(latest),
        "decision": decision,
        "stale_brain": False,
        "reasons": [],
    }
