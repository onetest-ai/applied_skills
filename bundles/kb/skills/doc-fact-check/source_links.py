"""Set every finding's `sources` (step 6b, item 4) from its cited Brain sources, deterministically.

    python source_links.py <run dir>/findings.json [--links <work dir>/source_links.json]

A finding cites its `source` and the `source` of each `conflicts` entry. Each cited Brain path becomes one
`{name, link, folder}` entry, in that order, once: the inventory link when `--links` maps the path
(`{"<Brain path>": {"link": …, "folder": …, "name"?: …}}`, written in step 6), else
`{name: "<Brain path> (not in inventory)", link: "", folder: ""}`. Nothing but `sources` changes, so the main session
never re-types the findings file. Prints {"findings": n, "sources": distinct cited paths, "resolved": linked paths}.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def cited(f: dict) -> list[str]:
    """The Brain paths a finding cites, in order, once each."""
    paths = [f.get("source")] + [c.get("source") for c in f.get("conflicts") or [] if isinstance(c, dict)]
    return list(dict.fromkeys(str(p).strip() for p in paths if p and str(p).strip()))


def link(findings: list[dict], links: dict) -> list[dict]:
    for f in findings:
        out = []
        for path in cited(f):
            hit = links.get(path)
            out.append({"name": hit.get("name") or path, "link": hit.get("link", ""), "folder": hit.get("folder", "")}
                       if isinstance(hit, dict) else {"name": f"{path} (not in inventory)", "link": "", "folder": ""})
        f["sources"] = out
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("findings", type=Path)
    ap.add_argument("--links", type=Path, help="source_links.json: {Brain path: {link, folder[, name]}} from step 6")
    a = ap.parse_args(argv)
    findings = json.loads(a.findings.read_text(encoding="utf-8"))
    links = json.loads(a.links.read_text(encoding="utf-8")) if a.links else {}
    if not isinstance(findings, list) or not isinstance(links, dict):
        print("source_links.py: findings must be a list and --links an object", file=sys.stderr)
        return 2
    link(findings, links)
    tmp = a.findings.with_name(a.findings.name + ".tmp")
    tmp.write_text(json.dumps(findings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, a.findings)
    paths = {p for f in findings for p in cited(f)}
    print(json.dumps({"findings": len(findings), "sources": len(paths), "resolved": len(paths & set(links))}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
