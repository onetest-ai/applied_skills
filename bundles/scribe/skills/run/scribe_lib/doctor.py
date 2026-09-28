"""doctor: check the system tools `render` needs (the spec's `scribe_doctor`,
wired here as a `scribe.py` subcommand rather than a separate script — same
deterministic check, no project config needed).

Checks, each `{"tool", "found": bool, "version"?: str, "hint"?: str}`:
  - `pandoc` (`pandoc --version`, first line)
  - `soffice` (`soffice --version`, first line; also tries `libreoffice`)
  - mermaid (`npx -y @mermaid-js/mermaid-cli --version`) — network/npm-backed,
    so this one can be slow on a cold npx cache; still required, since
    `render` cannot degrade past a failed diagram without `accept` catching it

All three are required for `render` to produce a usable docx+pdf; `doctor`
exits 1 if any is missing (checked by `scribe.py`, via `all_found`).
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Any

_TIMEOUT = 30
_MERMAID_TIMEOUT = 60

_HINTS = {
    "pandoc": "brew install pandoc  (or see https://pandoc.org/installing.html)",
    "soffice": "brew install --cask libreoffice",
    "mermaid": "no install needed — `npx -y @mermaid-js/mermaid-cli` fetches it on "
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
        ["npx", "-y", "@mermaid-js/mermaid-cli", "--version"],
        which="npx",
        timeout=_MERMAID_TIMEOUT,
    )

    checks = [pandoc, soffice, mermaid]
    all_found = all(c["found"] for c in checks)
    return {"status": "ok" if all_found else "error", "checks": checks, "all_found": all_found}
