"""The pre-send gate for a weekly report: every check is a pure function; `run_gate` wires them to a store and a
week's draft and writes `presend-<stamp>/presend.json` + `presend.html` (the shapes the dashboard, `deliver` and the
send step read). The answer-key checks (G3, G9b) block only in a week a key holds items for; a project without a key
sees them as advisory. Nothing here names a client: the reader's name, the paths and the workbench port come in."""
import datetime as dt
import hashlib
import html
import json
import re
import urllib.request
import zipfile
from pathlib import Path
from zoneinfo import ZoneInfo

ID_RE = re.compile(r"\b(?:E\d{1,2}|[STRD]\d{3})\b")
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_SENTENCE = re.compile(r"[^.·]*?\bheld on ((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2})[^.]*\.?",
                           re.IGNORECASE)
STOP = {"with", "the", "and", "for", "was", "were", "held", "from", "that", "this", "into", "client", "team", "session"}


# ── pure checks (tested in tests/test_presend.py) ─────────────────────────────

def cited_ids(text):
    return set(ID_RE.findall(text))


def check_cited_exist(ids, N):
    """Every id must exist in the tree; merged or cancelled is fine (it has an event saying why), vanished is not."""
    missing = sorted(i for i in ids if i not in N)
    return {"ok": not missing, "detail": {"missing": missing, "checked": len(ids)}}


def _tokens(s):
    return {w for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in STOP}


def check_dates(text, calendar, tz, year):
    """Each "held on <date>" sentence that names a calendar event (>= 2 shared words) must use its local date."""
    wrong = []
    for m in DATE_SENTENCE.finditer(text):
        sentence = m.group(0).strip()
        mon, day = m.group(1).replace(".", "").split()
        said = dt.date(year, MONTHS[mon[:3].lower()], int(day))
        best = []
        for e in calendar:
            subj = e.get("subject") or ""
            if subj.lower().startswith("canceled"):
                continue
            shared = len(_tokens(sentence) & _tokens(subj))
            if shared >= 2:
                local = dt.datetime.fromisoformat(e["start"].replace("Z", "+00:00")).astimezone(ZoneInfo(tz)).date()
                best.append((shared, local, subj))
        if best and said not in {b[1] for b in best}:
            top = max(best, key=lambda b: b[0])
            wrong.append({"said": said.isoformat(), "calendar": top[1].isoformat(), "event": top[2], "sentence": sentence})
    return {"ok": not wrong, "detail": {"wrong": wrong}}


def check_must_report(choices, match, cited):
    """Each item the user marked must-report is cited (through its node) or excluded by the user with a reason."""
    if (choices or {}).get("must_report_policy") == "report_as_built":   # your choice: the report as built is the list
        return {"ok": True, "detail": {"policy": "report_as_built", "cited": len(cited)}}
    if not choices or "must_report" not in choices:
        return {"ok": False, "detail": {"why": "must-report items not chosen yet: tick them on this page, Save choices"}}
    excluded = choices.get("excluded") or {}
    bad = []
    for cid in choices["must_report"]:
        m = match.get(cid)
        if cid in excluded or (m and m.startswith("node:") and m[5:] in cited):
            continue
        bad.append({"cid": cid, "match": m})
    return {"ok": not bad, "detail": {"not_in_report": bad, "chosen": len(choices["must_report"]), "excluded": len(excluded)}}


def check_duplicates(cited, pairs, choices=None):
    """Known duplicate pairs must not be cited together, unless you marked the pair as different work on this page."""
    ok = {frozenset(p) for p in (choices or {}).get("pairs_ok") or []}
    both = [[a, b] for a, b, *_ in pairs if a in cited and b in cited]
    different = [p for p in both if frozenset(p) in ok]
    return {"ok": not [p for p in both if p not in different], "detail": {"pairs": [p for p in both if p not in different],
                                                                        "marked_different": different}}


def check_room_owners(cited, N, flagged, choices):
    """An owner on a cited node created in a room recording (A7) is shown only once the user confirmed it."""
    rooms = {f["id"] for f in flagged}
    done = set((choices or {}).get("owners_confirmed") or [])
    todo = []
    for i in sorted(cited):
        n = N.get(i) or {}
        owner = n.get("internal_owner") or n.get("client_owner")
        if n.get("created_in") in rooms and owner and i not in done:
            todo.append({"id": i, "owner": owner, "meeting": n["created_in"]})
    return {"ok": not todo, "detail": {"to_confirm": todo}}


def coverage(items, cited):
    c = {"cited": 0, "in_tree_not_cited": 0, "card": 0, "missing": 0, "total": len(items)}
    for it in items:
        m = it.get("match")
        if m and m.startswith("node:"):
            c["cited" if m[5:] in cited else "in_tree_not_cited"] += 1
        elif m == "card":
            c["card"] += 1
        else:
            c["missing"] += 1
    return c


def check_baseline(base_docx):
    """G1 needs the report the reader last received; without it the gate fails closed, never passes by default."""
    if not base_docx:
        return {"ok": False, "detail": {"why": "no baseline: pass --baseline <the docx the reader last received> "
                                               "(or a presend-*/baseline folder)"}}
    return None


def check_key_covers(week_key, week_meetings):
    """A week the answer key holds nothing for cannot look complete: coverage would read 0 of 0."""
    have = {g["evidence"] for g in week_key}
    return {"ok": bool(week_key), "detail": {"key_items": len(week_key),
                                             "meetings_without_key_items": [m for m in week_meetings if m not in have]}}


def key_blocking(week_key):
    """The answer-key checks (G3, G9b) block only in a week the key holds items for; in a normal week they are advisory
    (the owner's rule, Oct 26: the weekly send must not wait for a hand-built key)."""
    return bool(week_key)


def calendar_from_store(evd, tz):
    """The calendar events the calendar source wrote into the store, as [{subject, start, end}] in UTC. Their times are
    local to the project's timezone; a cancelled event's subject starts with "Canceled: " as in a calendar export."""
    out = []
    for e in evd.values():
        m = e.get("meta") or {}
        if e.get("kind") != "event" or not m.get("start"):
            continue
        utc = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=ZoneInfo(tz)).astimezone(dt.timezone.utc).isoformat()
        subj = e.get("title") or ""
        out.append({"subject": ("Canceled: " + subj) if m.get("what") == "cancelled" else subj,
                    "start": utc(m["start"]), "end": utc(m["end"]) if m.get("end") else None})
    return out


def pick_baseline(reports, week, choices):
    """G1's baseline: the report the reader last received. A file named in the week's choices wins; otherwise the
    newest file a week before this one sent (sent-final.json). None when nothing was sent: G1 then fails closed."""
    if (choices or {}).get("baseline"):
        return Path(choices["baseline"])
    for d in sorted((x for x in Path(reports).iterdir() if x.is_dir() and x.name < week), reverse=True):
        s = d / "sent-final.json"
        if s.exists():
            return d / json.loads(s.read_text())["docx"]
    return None


def known_pairs(N, c17):
    """Duplicate pairs the report must not cite together: lint's look-alikes, every merged node with its survivor,
    and every pending merge card."""
    pairs = [[a, b] for a, b, *_ in c17]
    for i, n in N.items():
        if n.get("state") == "merged" and n.get("merged_into"):
            pairs.append([i, n["merged_into"]])
        for s in n.get("suggestions", []):
            if s.get("op") == "suggest_merge" and s.get("status") == "pending" and s.get("to"):
                pairs.append([i, s["to"]])
    return pairs


def check_fresh(docx_mtime, events_mtime):
    """The checked draft must be built from the tree as it is now (a Confirm after the build changes the report)."""
    return {"ok": docx_mtime >= events_mtime, "detail": {"docx_older_than_tree_by_s": max(0, round(events_mtime - docx_mtime))}}


def exit_code(gates):
    return 1 if any(g["blocking"] and not g["ok"] for g in gates) else 0


# ── wiring ────────────────────────────────────────────────────────────────────

def docx_text(path):
    x = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x))


def latest(folder, pattern):
    """The newest match, never a Word lock file (~$...) or a half-built file (.building...)."""
    c = sorted((p for p in Path(folder).glob(pattern) if not p.name.startswith(("~$", "."))), key=lambda p: p.stat().st_mtime)
    return c[-1] if c else None


def workbench_version(port=8766):
    try:
        return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=3).read())
    except Exception:
        return None


def run_gate(cfg, week, docx, reports, choices=None, baseline=None, key=None, calendar=None, external=None,
             reader="the reader", port=8766, progress=None):
    """The gate on `docx` for `week` (a Monday). `reports` holds reports/WEEK/; `choices` is the week's choices file
    (default reports/WEEK/presend-choices.json); `baseline` a docx or a folder (default: the last sent report);
    `key`, `calendar`, `external` optional eval files. Writes presend.json/.html under reports/WEEK/presend-<stamp>/
    and returns the result with `stamp` and `exit` (1 when a blocking gate fails)."""
    from . import evalscore as ES, ingest_audit as IA, ledger as L, lint as LI, store as S
    reports, docx = Path(reports), Path(docx)
    wk = reports / week
    monday = dt.date.fromisoformat(week)
    days = {(monday + dt.timedelta(d)).isoformat() for d in range(5)}
    choices_p = Path(choices) if choices else wk / "presend-choices.json"
    choices = json.loads(choices_p.read_text()) if choices_p.exists() else None
    base_dir = Path(baseline) if baseline else None
    base_docx = (base_dir if base_dir and base_dir.suffix == ".docx" else
                 latest(base_dir, "*.docx") if base_dir else pick_baseline(reports, week, choices))
    if not base_docx and latest(wk, "presend-*/baseline"):
        base_docx = latest(latest(wk, "presend-*/baseline"), "*.docx")
    store = cfg.store
    evd = S.evidence(store)
    N = L.fold(store)[0]
    text = docx_text(docx)
    cited = cited_ids(text)
    base_cited = cited_ids(docx_text(base_docx)) if base_docx else set()
    head, key_items = ES.load_gold(key) if key and Path(key).exists() else ({}, [])
    week_key = [g for g in key_items if evd.get(g["evidence"], {}).get("date") in days]
    sc = ES.score(store, week_key) if week_key else {"items": []}
    match = {it["cid"]: it["match"] for it in sc["items"]}
    flagged = IA.room_audio(evd)
    cal = calendar_from_store(evd, cfg.timezone)
    if not cal and calendar and Path(calendar).exists():
        cal = json.loads(Path(calendar).read_text())
        cal = cal if isinstance(cal, list) else cal.get("events", [])
    check = json.loads(Path(str(docx) + ".check.json").read_text()) if Path(str(docx) + ".check.json").exists() else {}
    v = workbench_version(port)
    pairs = known_pairs(N, LI.lint(N)["C17_duplicates"])
    week_meetings = sorted(i for i, x in evd.items() if x.get("kind") == "meeting" and not x.get("duplicate_of")
                           and x.get("date") in days)
    closed_not_in_week = [i for i in sorted(cited) if i in N and N[i]["state"] in ("done", "cancelled")
                          and not any(h.get("date") in days and h.get("op") in ("status", "done") for h in N[i]["history"])
                          and (N[i].get("closed") or "") not in days]
    gates = [
        dict(id="G0", name="Workbench runs the installed code", blocking=True,
             ok=bool(v) and not v.get("stale"), detail={"workbench": v}),
        dict(id="G1", name="Nothing the last report showed has vanished", blocking=True,
             **(check_baseline(base_docx) or check_cited_exist(base_cited, N))),
        dict(id="G2", name="Every cited item exists in the tree", blocking=True, **check_cited_exist(cited, N)),
        dict(id="G2b", name="Closed items without a closure this week (check they are not shown as this week's progress)",
             blocking=False, ok=not closed_not_in_week, detail={"items": closed_not_in_week}),
        dict(id="G3", name="Every must-report item is in the report or excluded by you", blocking=key_blocking(week_key),
             **check_must_report(choices, match, cited)),
        dict(id="G4", name="No known duplicate pair cited twice", blocking=True, **check_duplicates(cited, pairs, choices)),
        dict(id="G5", name="Dates agree with the calendar", blocking=True,
             **check_dates(text, cal, cfg.timezone, monday.year)),
        dict(id="G6", name="Owners from room recordings confirmed by you", blocking=True,
             **check_room_owners(cited, N, flagged, choices)),
        dict(id="G7", name="Report built cleanly (no build errors, version stamp)", blocking=True,
             ok=not check.get("errors") and bool(check.get("version")), detail={"errors": check.get("errors"),
                                                                               "version": check.get("version")}),
        dict(id="G7b", name="The draft was built from the tree as it is now", blocking=True,
             **check_fresh(docx.stat().st_mtime, (Path(store) / "events.jsonl").stat().st_mtime)),
        dict(id="G9b", name="The answer key covers this week's meetings", blocking=key_blocking(week_key),
             **check_key_covers(week_key, week_meetings)),
    ]
    unmatched = []
    if external and Path(external).exists():
        try:
            from . import compare_external as CE
            ce = CE.compare_external(store, json.loads(Path(external).read_text()), None, cfg.timezone)
            unmatched = list(ce["unmatched_meetings"])
        except Exception as x:   # an external oracle is a helper: its absence never blocks
            unmatched = [{"error": str(x)}]
    gates.append(dict(id="G8", name="Meetings an external record saw that taska never received", blocking=False,
                      ok=not unmatched, detail={"unmatched": unmatched}))
    states = []
    for g in week_key:
        m = match.get(g["cid"])
        state = ("cited" if m and m.startswith("node:") and m[5:] in cited else "in tree, not cited"
                 if m and m.startswith("node:") else "card" if m == "card" else "missing")
        states.append({"cid": g["cid"], "date": evd[g["evidence"]]["date"], "meeting": evd[g["evidence"]].get("title"),
                       "title": g.get("title"), "commitment": g.get("commitment"), "owner": g.get("owner"),
                       "match": m, "state": state, "source": g.get("source")})
    now = dt.datetime.now()
    res = {"week": week, "built": now.isoformat(timespec="seconds"), "docx": str(docx),
           "docx_sha256": hashlib.sha256(docx.read_bytes()).hexdigest(),   # the send step: only this file may go out
           "baseline": str(base_docx) if base_docx else None, "key": head, "gates": gates,
           "coverage": coverage([{"match": match.get(g["cid"])} for g in week_key], cited),
           "diff": {"added": sorted(cited - base_cited), "removed": sorted(base_cited - cited)},
           "items": states, "room_audio": flagged, "choices": choices}
    stamp = f"{now:%Y%m%d-%H%M}"
    out = wk / f"presend-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "presend.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str))
    (out / "presend.html").write_text(page(res, reader), encoding="utf-8")
    if progress:
        with open(progress, "a") as f:
            f.write(json.dumps({"ts": res["built"], "kind": "presend", "week": week, "docx": docx.name,
                                "gates_failed": [g["id"] for g in gates if g["blocking"] and not g["ok"]],
                                "coverage": res["coverage"], "key_sha": head.get("sha256")}) + "\n")
    return dict(res, stamp=stamp, page=str(out / "presend.html"), exit=exit_code(gates),
                failed=[g["id"] for g in gates if not g["ok"]])


def page(r, reader="the reader"):
    e = lambda s: html.escape(str(s if s is not None else "—"))
    gate_rows = "".join(
        f"<tr><td><b>{e(g['id'])}</b></td><td>{e(g['name'])}</td><td class={'ok' if g['ok'] else ('bad' if g['blocking'] else 'warn')}>"
        f"{'pass' if g['ok'] else ('BLOCKS' if g['blocking'] else 'check')}</td><td><code>{e(json.dumps(g['detail'], ensure_ascii=False)[:600])}</code></td></tr>"
        for g in r["gates"])
    ch = r.get("choices") or {}
    picked, excl, conf = set(ch.get("must_report") or []), ch.get("excluded") or {}, set(ch.get("owners_confirmed") or [])
    item_rows = "".join(
        f"<tr><td><input type=checkbox class=mr data-cid='{e(i['cid'])}' {'checked' if i['cid'] in picked else ''}></td>"
        f"<td>{e(i['date'])}</td><td>{e(i['title'])}<br><span class=m>{e(i['meeting'])} · {e(i['commitment'])}"
        f"{' · owner ' + e(i['owner']) if i['owner'] else ''}</span></td><td class=s-{i['state'].split(',')[0].replace(' ', '')}>{e(i['state'])}"
        f"{'<br><span class=m>' + e(i['match']) + '</span>' if i['match'] else ''}</td>"
        f"<td><input class=ex data-cid='{e(i['cid'])}' placeholder='reason to leave it out' value='{e(excl.get(i['cid'], ''))}'></td></tr>"
        for i in r["items"])
    g6 = next(g for g in r["gates"] if g["id"] == "G6")["detail"]["to_confirm"]
    own_rows = "".join(f"<label class=own><input type=checkbox class=oc data-id='{e(o['id'])}' {'checked' if o['id'] in conf else ''}> "
                       f"<b>{e(o['id'])}</b> owner {e(o['owner'])} <span class=m>(room recording {e(o['meeting'])})</span></label>" for o in g6)
    g4 = next(g for g in r["gates"] if g["id"] == "G4")["detail"]
    okp = {frozenset(p) for p in (ch.get("pairs_ok") or [])}
    pair_rows = "".join(f"<label class=own><input type=checkbox class=pk data-a='{e(a)}' data-b='{e(b)}' "
                        f"{'checked' if frozenset((a, b)) in okp else ''}> <b>{e(a)}</b> and <b>{e(b)}</b> are different work</label>"
                        for a, b in g4["pairs"] + g4.get("marked_different", []))
    cov = r["coverage"]
    blocked = [g["id"] for g in r["gates"] if g["blocking"] and not g["ok"]]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pre-send Check</title><style>
:root{{--bg:#f7f6f2;--card:#fff;--ink:#1d1f23;--m:#5d6470;--line:#e2dfd6;--acc:#1f6feb;--good:#1a7f37;--bad:#c0362c;--warn:#9a6700}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#121417;--card:#1b1e23;--ink:#e8e6e1;--m:#9aa1ad;--line:#2c3038;--acc:#6ea8fe;--good:#4ac26b;--bad:#f47067;--warn:#d4a72c}}}}
:root[data-theme="dark"]{{--bg:#121417;--card:#1b1e23;--ink:#e8e6e1;--m:#9aa1ad;--line:#2c3038;--acc:#6ea8fe;--good:#4ac26b;--bad:#f47067;--warn:#d4a72c}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}}
.wrap{{max-width:1040px;margin:0 auto;padding:24px 16px 100px}} h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:26px 0 8px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:0 0 12px;overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:14px}} th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
.m{{color:var(--m);font-size:13px}} .ok{{color:var(--good);font-weight:700}} .bad{{color:var(--bad);font-weight:700}} .warn{{color:var(--warn);font-weight:700}}
.s-cited{{color:var(--good)}} .s-missing{{color:var(--bad)}} .s-card{{color:var(--warn)}} code{{font-size:12px;word-break:break-word}}
input.ex{{width:100%;border:1px solid var(--line);border-radius:6px;padding:4px 6px;background:transparent;color:var(--ink)}}
.own{{display:block;margin:4px 0}} .bar{{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:10px 16px}}
button{{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:9px 14px;font:inherit;font-weight:600;cursor:pointer}}
</style></head><body><div class="wrap">
<h1>Pre-send check: week of {e(r['week'])}</h1>
<p class="m">Internal; never sent. Draft: {e(Path(r['docx']).name)} · built {e(r['built'])} · key {e(r['key'].get('version'))} n={e(r['key'].get('n'))}. Compared with: {e(Path(r['baseline']).name if r['baseline'] else 'no baseline')}.</p>
<div class="card"><b>{'Ready to send: every blocking gate passes.' if not blocked else 'Not ready: blocked by ' + ', '.join(blocked) + '.'}</b>
 Week coverage (key items from this week's meetings): <b>{cov['cited']}</b> in the report, {cov['in_tree_not_cited']} in the tree but not in the report, {cov['card']} waiting as a card, <b>{cov['missing']}</b> missing, of {cov['total']}.</div>
<h2>Gates</h2><div class="card"><table><thead><tr><th>Gate</th><th>Requirement</th><th>Result</th><th>Detail</th></tr></thead><tbody>{gate_rows}</tbody></table></div>
<h2>Diff against the last report</h2><div class="card">Added: {e(', '.join(r['diff']['added']) or 'none')}<br>Removed: {e(', '.join(r['diff']['removed']) or 'none')}</div>
<h2>Your choices</h2>
<div class="card"><b>1. Must-report items.</b> Tick what {e(reader)} must see this week. Leave an item unticked if it doesn't belong in his report, or tick it and give a reason in the last column to leave it out on purpose.
<table><thead><tr><th>Must</th><th>Date</th><th>Item</th><th>Now</th><th>Leave out because</th></tr></thead><tbody>{item_rows}</tbody></table></div>
<div class="card"><b>3. Look-alike items cited together.</b> If a pair is the same work, merge it in the workbench and rebuild; if it is different work, tick it.{pair_rows or '<p class=m>None.</p>'}</div>
<div class="card"><b>2. Owners from room recordings.</b> These owners were read from a transcript where one speaker tag holds most of the words. Tick the ones that are right.{own_rows or '<p class=m>None cited.</p>'}</div>
</div><div class="bar"><button id="save">Save choices</button> <span class="m" id="st">Downloads presend-choices.json; move it to reports/{e(r['week'])}/ and re-run presend.py.</span></div>
<script>
document.getElementById("save").onclick = () => {{
  const must = [...document.querySelectorAll(".mr:checked")].map(x => x.dataset.cid);
  const excluded = {{}}; document.querySelectorAll(".ex").forEach(x => {{ if (x.value.trim()) excluded[x.dataset.cid] = x.value.trim(); }});
  const owners = [...document.querySelectorAll(".oc:checked")].map(x => x.dataset.id);
  const pairs_ok = [...document.querySelectorAll(".pk:checked")].map(x => [x.dataset.a, x.dataset.b]);
  const b = new Blob([JSON.stringify({{saved_at: new Date().toISOString(), week: {json.dumps(r['week'])}, must_report: must, excluded, owners_confirmed: owners, pairs_ok}}, null, 1)], {{type: "application/json"}});
  const a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = "presend-choices.json"; a.click();
  document.getElementById("st").textContent = "Saved to Downloads/presend-choices.json.";
}};
</script></body></html>"""


