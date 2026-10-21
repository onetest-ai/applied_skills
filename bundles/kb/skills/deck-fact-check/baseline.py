"""Write the fact-check rerun baseline (step 10; doc-fact-check and deck-fact-check).

    python baseline.py <run dir>/findings.json --document "<name>.docx" --brain-version <knowledge_version>

Writes baseline.md (header for the reuse check in SKILL.md Inputs, then the full findings table with
evidence, trail, source and conflicts, the last rendered "span — source, date; …") and baseline.sources.json
(one entry per cited chunk id or governed metric, in the shape _shared/authoring.md defines, plus the finding id) beside findings.json. Reads run.json there.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling imports work under python -I too

from findings_report import conflicts_text  # noqa: E402

CHUNK_ID = re.compile(r"\b\d{12,}\b")
METRIC = re.compile(r"get_metric(?:_history)?\(\s*name\s*=\s*[\"']?([A-Za-z0-9_.\-]+)")
COLUMNS = ("id", "section", "quote", "verdict", "severity", "confidence", "evidence", "fix", "source",
           "destination", "trail", "conflicts")


def _cell(v) -> str:
    return " ".join(str(v if v is not None else "").split()).replace("|", "\\|")


def write_baseline(findings_path: Path, document: str, brain_version: str, today: str | None = None):
    findings_path = Path(findings_path)
    run = json.loads((findings_path.parent / "run.json").read_text(encoding="utf-8"))
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    lines = [f"# Fact-check baseline — {document}", "",
             f"- source_sha256: {run.get('source_sha256', '')}", f"- mode: {run.get('mode', '')}",
             f"- knowledge_version: {brain_version}", f"- claims: {len(findings)}",
             f"- date: {today or dt.date.today().isoformat()}", "",
             "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    sources, seen = [], set()
    for f in findings:
        lines.append("| " + " | ".join(_cell(conflicts_text(f.get(c)) if c == "conflicts" else f.get(c))
                                       for c in COLUMNS) + " |")
        trail, src = str(f.get("trail") or ""), f.get("source")
        for cid in CHUNK_ID.findall(trail):
            e = {"tag": f"RAG:{cid}", "kind": "narrative", "chunk_id": cid, "source_file": src, "finding": f["id"]}
            if (e["tag"], f["id"]) not in seen:
                seen.add((e["tag"], f["id"]))
                sources.append(e)
        for name in METRIC.findall(trail):
            e = {"tag": f"MART:{name}", "kind": "metric", "metric": name, "grain": None, "source_file": src,
                 "finding": f["id"]}
            if (e["tag"], f["id"]) not in seen:
                seen.add((e["tag"], f["id"]))
                sources.append(e)
    md = findings_path.with_name("baseline.md")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sj = findings_path.with_name("baseline.sources.json")
    sj.write_text(json.dumps(sources, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return md, sj


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("findings")
    ap.add_argument("--document", required=True)
    ap.add_argument("--brain-version", required=True)
    a = ap.parse_args(argv)
    try:
        md, sj = write_baseline(Path(a.findings), a.document, a.brain_version)
    except (OSError, ValueError) as e:
        print(f"baseline.py: {e}", file=sys.stderr)
        return 2
    print(md)
    print(sj)
    return 0


if __name__ == "__main__":
    sys.exit(main())
