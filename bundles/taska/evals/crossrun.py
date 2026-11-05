#!/usr/bin/env python3
"""Each key item across many runs on one key: how often a run matches it and how (by quote, or only by position),
where it is lost when it is not, owner and status errors that repeat, and stability's found-correctly verdict. Reads
each record's built quality document (quality.py build); no model call. Writes eval-crossrun/1 and its page.

  crossrun.py --records R1 R2 [R3 ...] --out DIR [--rule FILE]
  crossrun.py --all EVALS_DIR                      one view per key, in EVALS_DIR/crossrun/<key>/

The found-correctly verdict and the refusals (different keys, stale builds) are quality.stability's own."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

EVALS = Path(__file__).resolve().parent
RUN_DIR = EVALS.parent / "skills" / "run"
for p in (RUN_DIR, EVALS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import evalview  # noqa: E402
import quality as Q  # noqa: E402

ORDER = ("blind spot", "flaky", "position only", "steady")
ACTIONS = {
    "blind spot": "No run proposes it: the reader misses it every time. Fix how it is read (an extraction rule, the second read), or check whether the key item is right.",
    "flaky": "Some runs find it, some do not: the reader can find it but not reliably. Decide changes on repeated runs, never on one run; work on consistency, not on a new rule.",
    "position only": "Every match is only by position, never by quote: it may be different work. Have the judge (or you) confirm it before it counts.",
    "steady": "Matched in every run, at least once by quote. Watch the found-correctly verdict once the judge has run.",
    "repeated field error": "The owner or the status is wrong in at least half of the runs that match it: fix owner resolution or status mapping, not recall.",
}


def crossrun(records: list[Path], rule: dict | None = None) -> dict:
    """eval-crossrun/1 for records scored on one key (stability refuses anything else)."""
    st = Q.stability(records, rule)
    correctly = {i["id"]: i for i in st["items"]}
    rows: dict[str, dict] = {}
    for r in records:
        doc = json.loads((Path(r) / "quality" / "quality.json").read_text(encoding="utf-8"))
        for u in doc["units"]:
            for i in u["items"]:
                row = rows.setdefault(i["id"], {"id": i["id"], "kind": i.get("kind") or "", "title": (i.get("expected") or {}).get("title") or "",
                                                "quote": i.get("quote") or "", "unit": u.get("title") or u["id"], "matched": 0, "of": 0,
                                                "quote_matches": 0, "position": 0, "stages": Counter(), "owner_wrong": 0, "status_wrong": 0,
                                                "fields_seen": 0, "runs": {}})
                prod = i.get("produced") or {}
                row["of"] += 1
                if prod.get("node"):
                    row["matched"] += 1
                    row["quote_matches"] += prod.get("matched_by") == "quote"
                    row["position"] += prod.get("matched_by") == "position"
                    row["runs"][Path(r).name] = prod.get("matched_by") or prod["node"]
                else:
                    row["stages"][prod.get("stage") or "not matched"] += 1
                    row["runs"][Path(r).name] = "missed"
                checks = {c["name"]: c for c in i.get("checks") or [] if c.get("by") == "code"}
                row["owner_wrong"] += checks.get("Owner", {}).get("verdict") == "no"
                row["status_wrong"] += checks.get("Status", {}).get("verdict") == "no"
                row["fields_seen"] += "Owner" in checks or "Status" in checks
    items = []
    for row in rows.values():
        row["class"] = ("blind spot" if not row["matched"] else "flaky" if row["matched"] < row["of"] else
                        "position only" if not row["quote_matches"] else "steady")
        half = max(1, row["fields_seen"]) / 2
        row["repeated"] = [f for f, n in (("owner", row["owner_wrong"]), ("status", row["status_wrong"])) if n and n >= half]
        c = correctly.get(row["id"]) or {}
        row["correctly"] = {"found": c.get("found"), "of": c.get("of"), "stable": c.get("stable"), "verdicts": c.get("verdicts")}
        row["stages"] = dict(row.pop("stages"))
        items.append(row)
    items.sort(key=lambda x: (ORDER.index(x["class"]), x["matched"], x["id"]))
    return {"schema": "eval-crossrun/1", "key": st["key"], "key_sha": st["key_sha"], "records": st["records"], "items": items,
            "actions": ACTIONS,
            "summary": {"runs": len(records), "items": len(items), "classes": dict(Counter(x["class"] for x in items)),
                        "repeated_field_errors": sum(bool(x["repeated"]) for x in items),
                        "stages": dict(sum((Counter(x["stages"]) for x in items), Counter())),
                        "stable": st["summary"]["stable"], "stable_judged": st["summary"]["stable_judged"], "validated": st["summary"]["validated"],
                        "cohort": st["summary"].get("cohort")}}


def _incomplete(rec: Path) -> bool:
    f = rec / "score.json"
    return f.exists() and bool(json.loads(f.read_text(encoding="utf-8")).get("incomplete"))


def by_key(evals_dir: Path) -> dict[str, list[Path]]:
    """Every complete record with a built quality document, grouped by the key it was scored on (oldest record first);
    a run that left key meetings unprocessed would read as every item missed in it."""
    out: dict[str, list[Path]] = {}
    for m in sorted(Path(evals_dir).glob("*/meta.json")):
        rec = m.parent
        meta = json.loads(m.read_text(encoding="utf-8"))
        if (rec / "quality" / "quality.json").exists() and meta.get("key_sha") and not _incomplete(rec):
            out.setdefault(meta.get("key_version") or meta["key_sha"][:8], []).append(rec)
    return out


PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Eval Across Runs</title><style>""" + evalview.STYLE + """</style></head>
<body><main><h1>Each key item across runs</h1><p class=m id=head>Waiting for crossrun.json…</p><p class=lead id=lead></p>
<h2>What to do, by class</h2><div id=actions></div>
<h2>Every key item</h2><div class=wrap><table id=items></table></div>
<p class=m>Read from each record's quality document; the found-correctly verdict is the stability rule's (eval-crossrun/1).</p></main>
<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const TONE = {'blind spot': 'bad', flaky: 'warn', 'position only': 'warn', steady: 'good', 'repeated field error': 'warn'};
function show(d) {
  const s = d.summary, cl = s.classes || {};
  document.getElementById('head').innerHTML = `key <code>${esc(d.key)}</code> · ${s.runs} runs: ${d.records.map(r => `<code>${esc(r.record)}</code>`).join(' ')}`;
  document.getElementById('lead').innerHTML = `Of ${s.items} key items: <b>${cl['blind spot'] || 0}</b> never matched, <b>${cl.flaky || 0}</b> matched in some runs only, `
    + `<b>${cl['position only'] || 0}</b> matched only by position, <b>${cl.steady || 0}</b> steady; <b>${s.repeated_field_errors}</b> with an owner or status error that repeats. `
    + (d.records.every(r => r.judge === 'not judged yet')
      ? `No judge has run on these records, so found correctly is known only where code decides it (a proposal card, a meeting with no nodes): ${s.stable[0]} items are steadily not found correctly for that reason.`
      : `Found correctly is stable for ${s.stable[0]} of ${s.stable[1]} (${s.stable_judged[0]} of ${s.stable_judged[1]} items the judge was asked about)${s.validated ? '' : ', judge not validated'}${s.cohort === 'one version' ? '' : ' — these runs are not shown to be one version: read this as a trend, not as repeatability'}.`);
  const counts = {...cl, 'repeated field error': s.repeated_field_errors};
  document.getElementById('actions').innerHTML = Object.entries(d.actions).map(([k, v]) => `<div class="box ${TONE[k] || ''}"><b>${esc(k)} (${counts[k] || 0}).</b> ${esc(v)}</div>`).join('');
  const runs = d.records.map(r => r.record);
  document.getElementById('items').innerHTML = `<tr><th>Key item</th><th>Class</th><th>Matched</th><th>Per run</th><th>Lost at</th><th>Repeats</th><th>Found correctly</th></tr>` + d.items.map(x =>
    `<tr><td><b>${esc(x.title)}</b><br><span class=m>${esc(x.id)} · ${esc(x.kind)} · ${esc(x.unit)}</span><br><span class=m>“${esc(x.quote)}”</span></td>
     <td><span class="pill ${x.class === 'steady' ? 'p-done' : x.class === 'blind spot' ? '' : 'p-gate'}">${esc(x.class)}</span></td>
     <td class=n>${x.matched}/${x.of}${x.position ? `<br><span class=m>${x.position} by position</span>` : ''}</td>
     <td class=m>${runs.map(r => `${esc(r.slice(4, 13))}: ${esc(x.runs[r] || '—')}`).join('<br>')}</td>
     <td class=m>${Object.entries(x.stages).map(([k, n]) => `${esc(k)} ${n}`).join('<br>') || '—'}</td>
     <td class=m>${x.repeated.length ? x.repeated.map(f => `${f} wrong ${f === 'owner' ? x.owner_wrong : x.status_wrong}`).join('<br>') : '—'}</td>
     <td class=m>${x.correctly.of ? `${x.correctly.found}/${x.correctly.of}${x.correctly.stable ? ' · stable' : ''}` : 'not judged'}</td></tr>`).join('');
}
fetch('crossrun.json', {cache: 'no-store'}).then(r => r.json()).then(show).catch(e => { document.getElementById('head').textContent = 'crossrun.json not readable: ' + e; });
</script></body></html>"""


def write(out: Path, doc: dict) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(PAGE, encoding="utf-8")
    tmp = out / "crossrun.json.tmp"
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out / "crossrun.json")
    return out / "crossrun.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--records", nargs="+", help="two or more record folders scored on one key, each built")
    g.add_argument("--all", help="a records folder: one view per key with two or more built records")
    ap.add_argument("--out", help="with --records: where to write (required)")
    ap.add_argument("--rule", help="the bars each record's judge is validated against (as for quality.py build)")
    a = ap.parse_args(argv)
    rule = json.loads(Path(a.rule).read_text(encoding="utf-8")) if a.rule else {}
    groups = {"": [Path(r) for r in a.records]} if a.records else by_key(Path(a.all))
    result = {}
    for key, recs in groups.items():
        out = Path(a.out) if a.records else Path(a.all) / "crossrun" / key
        try:
            if len(recs) < 2:
                raise ValueError("needs two or more built records on one key")
            doc = crossrun(recs, rule)
        except ValueError as e:
            result[key or "records"] = {"status": "refused", "error": str(e)}
            continue
        write(out, doc)
        result[key or "records"] = {"status": "ok", "page": str(out / "index.html"), "runs": len(recs), "classes": doc["summary"]["classes"]}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if all(v["status"] == "ok" for v in result.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
