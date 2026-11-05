#!/usr/bin/env python3
"""The verdict page's evidence for one run against an adopted baseline: the slice of each meeting (size by structure),
the candidate's misses ranked by cause (slice, kind, cross-run class) and the found counts per slice. Offline; no
model call. Slices read the run copy's evidence, `<project>/.taska/evidence/<id>.json`."""
import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

EVALS = Path(__file__).resolve().parent
RUN_DIR = EVALS.parent / "skills" / "run"
for p in (RUN_DIR, EVALS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import compare  # noqa: E402
import evalview  # noqa: E402
from taska_lib.evalscore import load_gold  # noqa: E402

SMALL = 10000
LARGE = 30000
UNKNOWN = "unknown"
NO_CLASS = "n/a"
SCHEMA = "eval-verdict/1"
P_HOLD = 0.05
SLICE_HOLD = -2


def slice_of(evidence: dict | None) -> str:
    """"S turns", "M one block", ...: size by the summed turn text, structure by named speakers."""
    if not evidence:
        return UNKNOWN
    turns = evidence.get("turns") or []
    size = sum(len(t.get("text") or "") for t in turns) or len(evidence.get("text") or "")
    named = {t.get("speaker") for t in turns if t.get("speaker") not in (None, "", "?")}
    band = "S" if size < SMALL else "M" if size <= LARGE else "L"
    return f"{band} {'turns' if len(turns) >= 2 and len(named) >= 2 else 'one block'}"


def meeting_slices(project: Path, evidence_ids: set[str]) -> dict[str, str]:
    """Slice of each meeting in the run copy; "unknown" when its evidence file is missing or unreadable."""
    out: dict[str, str] = {}
    for eid in evidence_ids:
        try:
            evidence = json.loads((Path(project) / ".taska" / "evidence" / f"{eid}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            evidence = None
        out[eid] = slice_of(evidence if isinstance(evidence, dict) else None)
    return out


def causes(missed: list[str], key: dict[str, dict], slices: dict[str, str], classes: dict[str, str]) -> list[dict]:
    """Misses grouped by (slice, kind, class), most misses first, ties by slice then kind."""
    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for cid in missed:
        item = key[cid]
        groups[(slices.get(item.get("evidence"), UNKNOWN), item.get("kind") or "", classes.get(cid, NO_CLASS))].append(cid)
    rows = [{"slice": s, "kind": k, "class": c, "misses": len(ids), "items": ids} for (s, k, c), ids in groups.items()]
    return sorted(rows, key=lambda r: (-r["misses"], r["slice"], r["kind"], r["class"]))


def slice_table(base_hits: set[str], cand_hits: set[str], key: dict[str, dict], slices: dict[str, str]) -> list[dict]:
    """Key items per slice with how many each run found; delta = candidate minus baseline."""
    count: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for cid, item in key.items():
        row = count[slices.get(item.get("evidence"), UNKNOWN)]
        row[0] += 1
        row[1] += cid in base_hits
        row[2] += cid in cand_hits
    return [{"slice": s, "items": n, "baseline_found": b, "candidate_found": c, "delta": c - b} for s, (n, b, c) in sorted(count.items())]


def _read(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def candidate(evals_dir: Path, tier: str) -> Path | None:
    """Newest complete record of the tier on the current key (the key of the newest record of any tier)."""
    folders = sorted(p for p in Path(evals_dir).iterdir() if p.is_dir() and (p / "meta.json").is_file()) if Path(evals_dir).is_dir() else []
    metas = {p: _read(p / "meta.json") or {} for p in folders}
    if not folders:
        return None
    current = metas[folders[-1]].get("key_sha")
    for p in reversed(folders):
        if metas[p].get("model_tier") != tier or metas[p].get("key_sha") != current:
            continue
        score = _read(p / "score.json")
        if score is not None and not score.get("incomplete"):
            return p
    return None


def efficiency(trajectory: dict, meetings: int = 0) -> dict:
    """Calls, wasted calls (errors that were not the gate's own refusals), their rate and minutes per key meeting."""
    s = (trajectory or {}).get("summary") or {}
    calls, errors, refusals = s.get("tool_calls"), s.get("tool_errors"), s.get("refusals") or 0
    wasted = max((errors or 0) - refusals, 0) if calls is not None else None
    duration = s.get("duration_s")
    return {"tool_calls": calls, "wasted": wasted, "wasted_rate": round(wasted / calls, 4) if calls else None,
            "gate_refusals": refusals if calls is not None else None,
            "minutes_per_meeting": round(duration / 60 / meetings, 2) if duration is not None and meetings else None}


def _gate(name: str, value: float | None, bar: float | None, low_is_bad: bool = True) -> dict:
    if bar is None:
        status = "not set"
    elif value is None:
        status = UNKNOWN
    else:
        status = "pass" if (value >= bar if low_is_bad else value <= bar) else "fail"
    return {"name": name, "value": value, "bar": bar, "status": status}


def gates(score: dict, eff: dict, quality: dict, bars: dict) -> list[dict]:
    """Committed and decisions lower bounds (raw: any quote or position hit), the same judged strictly (unknown until
    every item is judged), the wasted-call rate and precision (unknown until judged answers exist)."""
    precision = ((quality or {}).get("summary") or {}).get("precision") or {}
    judged = precision.get("validated") is True
    row = _gate("precision", precision.get("lb95") if judged else None, bars.get("precision_lb_min"))
    if not judged:
        row.update(value=None, status=UNKNOWN)
    strict = ((quality or {}).get("summary") or {}).get("strict") or {}

    def strict_gate(cat: str) -> dict:
        s = strict.get(cat) or {}
        why = ("not judged" if not s else f"{s.get('unresolved')} items not judged yet" if not s.get("certified") else
               f"judge not validated ({s.get('flag') or 'no validation recorded'})" if s.get("validated") is not True else None)
        g = _gate(f"{cat} strict", None if why else s.get("lb95"), bars.get(f"{cat}_strict_lb_min"))
        return dict(g, why=why) if why and g["bar"] is not None else g

    return [_gate("committed", (score.get("B7") or {}).get("lb95"), bars.get("committed_lb_min")),
            _gate("decisions", (score.get("B13") or {}).get("lb95"), bars.get("decisions_lb_min")),
            strict_gate("committed"), strict_gate("decisions"),
            _gate("wasted", eff.get("wasted_rate"), bars.get("wasted_rate_max"), low_is_bad=False),
            row]


def _classes(evals_dir: Path, key_version: str) -> dict[str, str]:
    doc = _read(Path(evals_dir) / "crossrun" / str(key_version) / "crossrun.json") or {}
    return {i["id"]: i.get("class") or NO_CLASS for i in doc.get("items") or [] if "id" in i}


def _hits(score: dict) -> set[str]:
    return {i["cid"] for i in score.get("items") or [] if i.get("match")}


def _stamp(record: Path, meta: dict) -> dict:
    return {"record": record.name, "code_version": meta.get("code_version")}


EXIT = {"ship": 0, "inconclusive": 3, "invalid": 4, "hold": 5}   # 1 a failed run, 2 a refused command


def build(evals_dir: Path, tier: str, bars: dict, baseline: str | None, record: str | None = None) -> dict:
    """The eval-verdict/1 document for the tier's candidate record (the requested `record`, else the newest complete
    one) against the adopted baseline record, with execution, evaluation and release status kept apart."""
    doc = _build(Path(evals_dir), tier, bars, baseline, record)
    doc.setdefault("execution_status", "complete" if doc["candidate"] else "missing")
    doc["evaluation_status"] = "scored" if doc["candidate"] else "not_scored"
    doc["release_status"] = doc["verdict"]
    return doc


def _build(evals_dir: Path, tier: str, bars: dict, baseline: str | None, record: str | None) -> dict:
    doc: dict = {"schema": SCHEMA, "tier": tier, "key_version": None, "key_sha": None, "candidate": None, "baseline": None,
                 "verdict": "inconclusive", "why": [], "gates": [], "diff": None, "causes": [], "slices": [],
                 "efficiency": efficiency({}), "links": {}}
    if record is not None:
        cand = evals_dir / record
        if (_read(cand / "meta.json") or {}).get("model_tier") != tier:
            doc["why"] = [f"no record {record} of tier {tier}"]
            return doc
        score = _read(cand / "score.json")
        if score is None or score.get("incomplete"):
            doc.update(why=[f"record {record} is incomplete: it has no verdict"], execution_status="incomplete")
            return doc
    else:
        cand = candidate(evals_dir, tier)
    if cand is None:
        doc["why"] = [f"no complete record of tier {tier} on the current key"]
        return doc
    meta, score = _read(cand / "meta.json") or {}, _read(cand / "score.json") or {}
    if meta.get("manifest_changed"):
        doc.update(verdict="invalid", why=[f"the {' and '.join(meta['manifest_changed'])} code changed while record {cand.name} "
                                           "ran: it measures no one version"])
        return doc
    try:
        _, items = load_gold(meta["key_path"])
        project = Path(meta["project"])
    except KeyError as e:
        doc["why"] = [f"record {cand.name} has no {e.args[0]} in meta.json"]
        return doc
    except (OSError, ValueError, TypeError) as e:
        doc["why"] = [f"the answer key of record {cand.name} is not readable: {e}"]
        return doc
    key = {i["cid"]: i for i in items}
    key_version = meta.get("key_version")
    slices = meeting_slices(project, {i.get("evidence") for i in items if i.get("evidence")})
    eff = efficiency(_read(cand / "trajectory.json") or {}, len(slices))
    rows = gates(score, eff, _read(cand / "quality" / "quality.json") or {}, bars)
    doc.update(key_version=key_version, key_sha=meta.get("key_sha"), candidate=_stamp(cand, meta), gates=rows, efficiency=eff,
               links={"quality": f"../../{cand.name}/quality/index.html", "crossrun": f"../../crossrun/{key_version}/index.html",
                      "trajectory": f"../../{cand.name}/trajectory.html"})
    classes = _classes(evals_dir, key_version)
    inconclusive: list[str] = []
    cause_ids = [c for c in key if c not in _hits(score)]
    hold = [f"gate {g['name']} failed: {g['value']} against {g['bar']}" for g in rows if g["status"] == "fail"]
    base_dir = evals_dir / baseline if baseline else None
    is_self = base_dir is not None and base_dir.name == cand.name
    base_meta = _read(base_dir / "meta.json") if base_dir else None
    base_score = _read(base_dir / "score.json") if base_dir else None
    if base_dir is None or base_meta is None or base_score is None:
        inconclusive.append("no adopted baseline: run verdict.py adopt")
    elif is_self:
        inconclusive.append("the candidate is the adopted baseline: no change to judge")
    else:
        doc["baseline"] = _stamp(base_dir, base_meta)
        reason = compare.incompatible(base_meta, meta, allow_scope_change=True)
        if reason:
            inconclusive.append(reason)
        else:
            partial = bool(base_score.get("incomplete"))
            scope = base_meta.get("snapshot_of") != meta.get("snapshot_of")
            shared = set(base_meta.get("processed") or []) & set(meta.get("processed") or []) if (partial or scope) else None
            if partial:
                inconclusive.append("the baseline record is incomplete: only the meetings both runs processed are compared")
            if scope:
                inconclusive.append("different snapshots: only the meetings both runs processed are compared")
            diff = compare.paired(base_score["items"], score["items"], key, meetings=shared)
            doc["diff"] = diff
            scoped = {c: i for c, i in key.items() if shared is None or i.get("evidence") in shared}
            doc["slices"] = slice_table(_hits(base_score), _hits(score), scoped, slices)
            cause_ids = diff["still_missed"] + diff["lost"]
            if shared is None:
                m = diff["mcnemar_committed"]
                if m["lost"] > m["gained"] and m["p"] < P_HOLD:
                    hold.append(f"McNemar: committed items lost {m['lost']} against gained {m['gained']} (p={m['p']})")
                hold += [f"slice {r['slice']} lost {-r['delta']} items net" for r in doc["slices"] if r["delta"] <= SLICE_HOLD]
    doc["causes"] = causes(cause_ids, key, slices, classes)
    if hold:
        doc.update(verdict="hold", why=hold + inconclusive)
    elif inconclusive:
        doc["why"] = inconclusive
    elif all(g["bar"] is None for g in rows):
        doc["why"] = ["no bars set (evals/bars.json)"]
    elif [g for g in rows if g["bar"] is not None and g["status"] != "pass"]:
        doc["why"] = [f"gate {g['name']} has a bar but no value" for g in rows if g["bar"] is not None and g["status"] != "pass"]
    else:
        doc.update(verdict="ship", why=["every gate with a bar passes, no significant loss of committed items (McNemar), no slice at net -2 or worse"])
    return doc


PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Eval Verdict</title><style>""" + evalview.STYLE + """
.p-fail{background:var(--bad-bg);color:var(--bad)}</style></head>
<body><main><h1>Eval verdict</h1><p class=m id=head>Waiting for verdict.json…</p><div id=verdict></div>
<h2>What the verdict says</h2>
<ul class=lead>
<li>Verdict first: it says whether this change may ship against the adopted baseline and which gate decided.</li>
<li>Lost items are regressions: open each, read the quote and the trace before believing a cause.</li>
<li>Causes: the top row is where the next fix pays most. A <code>blind spot</code> row needs a reading fix or a key check; a <code>flaky</code> row needs consistency (decide on repeated runs), not a new rule.</li>
<li>Slices: a slice that lost items is a hold even when the average rose.</li>
<li>Precision says <code>unknown</code> until the judge has answered; do not read the key-match lower bound as precision.</li>
</ul>
<h2>Gates</h2><div class=wrap><table id=gates></table></div>
<h2>Lost</h2><div id=lost></div>
<h2>Gained</h2><div id=gained></div>
<h2>Causes</h2><div class=wrap><table id=causes></table></div>
<h2>Slices</h2><div class=wrap><table id=slices></table></div>
<h2>Efficiency</h2><div class=tiles id=eff></div>
<p class=m id=links></p></main>
<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const val = v => v === null || v === undefined ? 'not recorded' : esc(v);
const TONE = {ship: 'good', hold: 'bad', inconclusive: 'warn'};
const PILL = {pass: 'p-done', fail: 'p-fail', 'not set': 'p-gate', unknown: 'p-gate'};
function ids(list, href) {
  if (!list || !list.length) return '<p class=m>none</p>';
  return '<ul>' + list.map(c => `<li>${href ? `<a href="${esc(href)}#${encodeURIComponent(c)}"><code>${esc(c)}</code></a>` : `<code>${esc(c)}</code>`}</li>`).join('') + '</ul>';
}
function show(data) {
  const d = data, q = (d.links || {}).quality, diff = d.diff;
  document.getElementById('head').innerHTML = `tier <code>${esc(d.tier)}</code> · key <code>${esc(d.key_version)}</code> · candidate <code>${val((d.candidate || {}).record)}</code> · baseline <code>${val((d.baseline || {}).record)}</code>`;
  document.getElementById('verdict').innerHTML = `<div class="box ${TONE[d.verdict] || ''}"><b style="font-size:1.4rem">${esc(d.verdict)}</b><ul>${(d.why || []).map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>`;
  document.getElementById('gates').innerHTML = '<tr><th>Gate</th><th>Value</th><th>Bar</th><th>Status</th></tr>' + (d.gates || []).map(g =>
    `<tr><td>${esc(g.name)}</td><td class=n>${val(g.value)}</td><td class=n>${val(g.bar)}</td><td><span class="pill ${PILL[g.status] || ''}">${esc(g.status)}</span></td></tr>`).join('');
  document.getElementById('lost').innerHTML = diff ? ids(diff.lost, q) + `<p class=m>${diff.mcnemar_committed.lost} committed lost against ${diff.mcnemar_committed.gained} gained (p=${val(diff.mcnemar_committed.p)}), over ${diff.n} key items.</p>` : '<p class=m>not recorded: there is no comparison.</p>';
  document.getElementById('gained').innerHTML = diff ? ids(diff.gained, q) : '<p class=m>not recorded: there is no comparison.</p>';
  document.getElementById('causes').innerHTML = '<tr><th>Slice</th><th>Kind</th><th>Class</th><th>Misses</th><th>Items</th></tr>' + (d.causes || []).map(c =>
    `<tr><td>${esc(c.slice)}</td><td>${esc(c.kind)}</td><td>${esc(c.class)}</td><td class=n>${c.misses}</td><td class=m>${c.items.map(i => `<code>${esc(i)}</code>`).join(' ')}</td></tr>`).join('');
  document.getElementById('slices').innerHTML = '<tr><th>Slice</th><th>Items</th><th>Baseline found</th><th>Candidate found</th><th>Change</th></tr>' + (d.slices || []).map(r =>
    `<tr><td>${esc(r.slice)}</td><td class=n>${r.items}</td><td class=n>${r.baseline_found}</td><td class=n>${r.candidate_found}</td><td class="n ${r.delta < 0 ? 'down' : r.delta > 0 ? 'up' : ''}">${r.delta > 0 ? '+' : ''}${r.delta}</td></tr>`).join('');
  const e = d.efficiency || {};
  document.getElementById('eff').innerHTML = [['Tool calls', e.tool_calls], ['Wasted calls', e.wasted], ['Wasted rate', e.wasted_rate], ['Gate refusals', e.gate_refusals], ['Minutes per unit', e[Object.keys(e).find(k => k.startsWith('minutes_per_'))]]]
    .map(([k, v]) => `<div class=tile>${k}<b>${val(v)}</b></div>`).join('');
  const l = d.links || {};
  document.getElementById('links').innerHTML = ['quality', 'crossrun', 'trajectory'].filter(k => l[k]).map(k => `<a href="${esc(l[k])}">${esc(k)}</a>`).join(' · ');
}
fetch('verdict.json', {cache: 'no-store'}).then(r => r.json()).then(show).catch(e => { document.getElementById('head').textContent = 'verdict.json not readable: ' + e; });
</script></body></html>"""


def write(out: Path, doc: dict) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(PAGE, encoding="utf-8")
    tmp = out / "verdict.json.tmp"
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out / "verdict.json")
    return out / "verdict.json"


def adopt(project: Path, evals_dir: Path, record: str) -> dict:
    """Pin the record as its tier's baseline in <project>/evals/baseline.json; refuses an unknown, incomplete or tierless record."""
    folder = Path(evals_dir) / record
    meta, score = _read(folder / "meta.json"), _read(folder / "score.json")
    if meta is None or score is None:
        raise ValueError(f"no record {record} in {evals_dir}")
    if score.get("incomplete"):
        raise ValueError(f"record {record} is incomplete: it cannot be a baseline")
    tier = meta.get("model_tier")
    if not tier:
        raise ValueError(f"record {record} has no model_tier: it cannot be a baseline")
    path = Path(project) / "evals" / "baseline.json"
    current = _read(path) or {}
    replaced = current.get(tier)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({**current, tier: record}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return {"tier": tier, "record": record, "replaced": replaced}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write the verdict page for each tier that has a candidate")
    b.add_argument("--project", required=True)
    b.add_argument("--tier", action="append", help="repeatable; default low, med, high")
    b.add_argument("--evals-dir", help="default <project>/reports/evals")
    b.add_argument("--out", help="default <evals-dir>/verdict/<tier>")
    b.add_argument("--record", help="judge this record (one --tier), never an older complete one")
    ad = sub.add_parser("adopt", help="pin a complete record as its tier's baseline")
    ad.add_argument("--project", required=True)
    ad.add_argument("--evals-dir", help="default <project>/reports/evals")
    ad.add_argument("record")
    a = ap.parse_args(argv)
    project = Path(a.project)
    evals_dir = Path(a.evals_dir) if a.evals_dir else project / "reports" / "evals"
    if a.cmd == "adopt":
        try:
            result = adopt(project, evals_dir, a.record)
        except ValueError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if a.out and len(a.tier or []) > 1:
        print("refused: --out names one page, so it takes at most one --tier", file=sys.stderr)
        return 2
    if a.record and len(a.tier or []) != 1:
        print("refused: --record names one record, so it takes exactly one --tier", file=sys.stderr)
        return 2
    bars = _read(project / "evals" / "bars.json") or {}
    baselines = _read(project / "evals" / "baseline.json") or {}
    result = {}
    for tier in a.tier or ["low", "med", "high"]:
        if a.record is None and candidate(evals_dir, tier) is None:
            continue
        out = Path(a.out) if a.out else evals_dir / "verdict" / tier
        doc = build(evals_dir, tier, bars, baselines.get(tier), a.record)
        write(out, doc)
        result[tier] = {"verdict": doc["verdict"], "page": str(out / "index.html"), "why": doc["why"]}
    print(json.dumps(result, ensure_ascii=False))
    return max((EXIT[r["verdict"]] for r in result.values()), default=EXIT["inconclusive"])


if __name__ == "__main__":
    sys.exit(main())
