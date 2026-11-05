"""A generic live view of a running eval: one data contract (eval-live/1) and one page that renders any producer's file.

Standard library only; it imports nothing from a bundle. A bundle's producer fills the contract (its units: meetings,
claims, questions, sections; its metrics; its gates) and calls write(); the page polls live.json every 15 s without
reloading and asks three questions: is it finding the work, where is it stuck, is the run healthy. Another bundle
adopts it by copying this file byte-identical, pinned by a drift test.

eval-live/1 (every key present; [] or null when unknown):
  schema, title, unit_label, status (running|finished|stopped), started, updated, elapsed_s, model_tier, record, source,
  metrics [{code, name, ok, n, baseline_ok, baseline_n}], baseline {record, of, compared [{code, name, lost [ITEM], gained [ITEM], p}]},
  verdict {note, by_metric {code: text}}, provenance {this {label: value}, baseline {label: value}},
  units [{id, title, state, found, expected, baseline_found, missed [ITEM], gate [{status, parts, answer}]}],
  stuck {reasons {status: count}}, health [{name, value, baseline}], agents [{name, model, turns, units, last}]
  optional: caveats [{text, href}]   (what qualifies the counts, e.g. a quality check of the matches, with its page)
  ITEM = {id, title, quote, where, stage}   (stage already in words)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

SCHEMA = "eval-live/1"


def mcnemar_exact(lost: int, gained: int) -> float:
    """Two-sided exact McNemar p for `lost` items lost and `gained` items gained between two runs (paired)."""
    n = lost + gained
    if n == 0:
        return 1.0
    k = min(lost, gained)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def compare_items(code: str, name: str, now: dict[str, bool], base: dict[str, bool], items: dict[str, dict]) -> dict:
    """Lost (found by the baseline, not now) and gained items of one metric, with the paired p."""
    lost = [items[i] for i in now if base.get(i) is True and not now[i]]
    gained = [items[i] for i in now if base.get(i) is False and now[i]]
    return {"code": code, "name": name, "lost": lost, "gained": gained, "p": round(mcnemar_exact(len(lost), len(gained)), 3)}


def verdict(compared: list[dict], record: str | None, of: int | None) -> dict:
    """Relative only: worse or better beyond noise (p < 0.05), else within noise. No absolute bar is implied."""
    note = "no pass bar is adopted: "
    if not record:
        return {"note": note + "and there is no baseline to compare with", "by_metric": {}}
    best = (f", the best of {of} run{'s' if of != 1 else ''} so far (partly luck, so later runs tend to look worse "
            f"against it)") if of else ""
    by = {}
    for c in compared:
        lost, gained = len(c["lost"]), len(c["gained"])
        by[c["code"]] = ("worse beyond noise" if c["p"] < 0.05 and lost > gained else
                         "better beyond noise" if c["p"] < 0.05 and gained > lost else "within noise")
    return {"note": note + f"the verdict is relative to {record}{best} (paired exact McNemar)", "by_metric": by}


STYLE = """
:root{--bg:#f6f5f1;--card:#fff;--ink:#1c1e22;--mut:#666b74;--line:#e3e1db;--ok:#1f7a4d;--ok-bg:#eaf6ef;--warn:#9a5b00;--warn-bg:#fff4e2;--bad:#b4233c;--bad-bg:#fdecef;--code:#f1efe9}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121417;--card:#1b1e23;--ink:#e8e6e1;--mut:#9aa0aa;--line:#2b2f36;--ok:#5cc28e;--ok-bg:#16291f;--warn:#e0a23c;--warn-bg:#2d2414;--bad:#ef6b80;--bad-bg:#30181d;--code:#23262c}}
:root[data-theme="dark"]{--bg:#121417;--card:#1b1e23;--ink:#e8e6e1;--mut:#9aa0aa;--line:#2b2f36;--ok:#5cc28e;--ok-bg:#16291f;--warn:#e0a23c;--warn-bg:#2d2414;--bad:#ef6b80;--bad-bg:#30181d;--code:#23262c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}
main{max-width:1040px;margin:0 auto;padding:24px 16px 60px}h1{font-size:1.4rem;margin:0 0 4px}h2{font-size:1.1rem;margin:26px 0 8px;padding-top:8px;border-top:1px solid var(--line)}
.m{color:var(--mut);font-size:.85rem}code{font:.82em ui-monospace,Menlo,monospace;background:var(--code);padding:1px 4px;border-radius:4px;overflow-wrap:anywhere}
.box{border-radius:10px;padding:10px 14px;margin:10px 0;border-left:4px solid var(--mut);background:var(--card)}.good{border-left-color:var(--ok);background:var(--ok-bg)}.bad{border-left-color:var(--bad);background:var(--bad-bg)}.warn{border-left-color:var(--warn);background:var(--warn-bg)}
.lead{font-size:1.05rem}.tiles{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 12px;min-width:170px}
.tile b{display:block;font-size:1.25rem;font-variant-numeric:tabular-nums}.wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;background:var(--card);border-radius:10px;overflow:hidden;margin:6px 0 14px;font-size:.88rem}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{color:var(--mut);font-weight:600}td.n{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-block;font-size:.74rem;font-weight:600;border-radius:99px;padding:1px 8px;background:var(--code)}.p-done{background:var(--ok-bg);color:var(--ok)}.p-gate{background:var(--warn-bg);color:var(--warn)}
.down{color:var(--bad);font-weight:600}.up{color:var(--ok);font-weight:600}details summary{cursor:pointer}ul{margin:4px 0 8px 18px;padding:0}li{margin:4px 0}
.card{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--mut);border-radius:12px;padding:12px 14px;margin:14px 0}.card.good{border-left-color:var(--ok)}.card.warn{border-left-color:var(--warn)}.card.bad{border-left-color:var(--bad)}
.top{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}.ag{margin-left:auto;font-size:.78rem;font-weight:600;background:var(--code);border-radius:99px;padding:1px 9px}
blockquote{margin:8px 0;padding:6px 10px;border-left:3px solid var(--line);font-size:.92rem}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin:6px 0}.col{background:var(--bg);border-radius:8px;padding:8px 10px}.col h4{margin:0 0 4px;font-size:.85rem;color:var(--mut)}
.kv th{width:62px}.miss{color:var(--bad);font-weight:600}
.v{display:inline-block;width:22px;text-align:center;border-radius:6px;font-weight:700}.v-ok{background:var(--ok-bg);color:var(--ok)}.v-no{background:var(--bad-bg);color:var(--bad)}.v-check{background:var(--warn-bg);color:var(--warn)}
.src{font-size:.72rem;font-weight:600;border-radius:99px;padding:1px 7px;background:var(--code)}.s-judge{background:var(--warn-bg);color:var(--warn)}.s-you{background:var(--bad-bg);color:var(--bad)}"""

PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Eval Live</title><style>""" + STYLE + """</style></head>
<body><main><h1 id=title>Eval run, live</h1><p class=m id=head>Waiting for live.json…</p><p class=lead id=lead></p><div id=verdict></div>
<h2>1 · Is it finding the work?</h2><div class=tiles id=tiles></div><div id=caveats></div><div id=versus></div><div class=wrap><table id=units></table></div>
<h2 id=changedh>What changed since the baseline</h2><div id=changed></div>
<h2>2 · Where is it stuck?</h2><div id=stuck></div>
<h2>3 · Is the run healthy?</h2><div class=tiles id=health></div><div class=wrap><table id=agents></table></div>
<p class=m>Refreshes every 15 seconds from live.json, without reloading (eval-live/1).</p></main>
<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const cap = s => s ? s[0].toUpperCase() + s.slice(1) : s;
const pct = (ok, n) => n ? Math.round(100 * ok / n) + '%' : '—';
const ORDER = {'at the gate': 0, writing: 1, reading: 2, queued: 3, done: 4};
const open = new Set();
const item = x => `<li><b>${esc(x.title)}</b> <span class=m>${esc(x.where || '')} · ${esc(x.stage || '')}</span>${x.quote ? `<br>“${esc(x.quote)}”` : ''}${x.nearby ? `<br><span class=m>nearest: ${esc(x.nearby)}</span>` : ''}</li>`;
function show(d) {
  const U = d.unit_label || 'unit', b = d.baseline, units = d.units || [], ms = d.metrics || [];
  document.getElementById('title').textContent = (d.title || 'Eval run') + ', live';
  document.getElementById('head').innerHTML = `<b>${esc(d.status)}</b> · ${Math.round((d.elapsed_s || 0) / 60)} min · tier ${esc(d.model_tier || '?')} · updated ${esc((d.updated || '').slice(11))}`
    + (d.record ? ` · record <code>${esc(d.record)}</code>` : '') + `<br><span class=m>${esc(d.source || '')}</span>`;
  const loss = u => (u.baseline_found ?? u.found) - u.found;
  const worst = [...units].sort((x, y) => loss(y) - loss(x))[0];
  const m0 = ms[0], c0 = b && (b.compared || []).find(c => c.code === (m0 || {}).code);
  document.getElementById('lead').innerHTML = !m0 ? '' : `So far: <b>${esc(m0.name)} ${m0.ok}/${m0.n}</b>` + (c0
    ? ` against ${m0.baseline_ok} on the baseline — <span class=down>lost ${c0.lost.length}</span>, <span class=up>gained ${c0.gained.length}</span>, p = ${c0.p}${c0.p >= 0.05 ? ' (within noise)' : ''}.`
      + (worst && loss(worst) > 0 ? ` Most lost in <b>${esc(worst.title || worst.id)}</b> (${worst.found} against ${worst.baseline_found}).` : '')
    : '; no baseline to compare with.');
  const v = d.verdict;
  const tone = v && Object.values(v.by_metric || {}).some(x => x.startsWith('worse')) ? 'bad' : v && Object.values(v.by_metric || {}).some(x => x.startsWith('better')) ? 'good' : 'warn';
  document.getElementById('verdict').innerHTML = !v ? '' : `<div class="box ${tone}"><b>Verdict:</b> ${Object.entries(v.by_metric || {}).map(([k, x]) => `${esc((ms.find(m => m.code === k) || {}).name || k)} ${esc(x)}`).join('; ') || 'none'}.<br><span class=m>${esc(v.note)}</span></div>`;
  document.getElementById('tiles').innerHTML = ms.map(m => `<div class=tile><span class=m>${esc(m.name)}</span><b>${m.ok}/${m.n} · ${pct(m.ok, m.n)}</b>${m.baseline_ok != null ? `<span class=m>baseline ${m.baseline_ok}/${m.baseline_n ?? m.n}</span>` : ''}</div>`).join('');
  document.getElementById('caveats').innerHTML = (d.caveats || []).map(c => `<div class="box warn">${esc(c.text)}${c.href ? ` <a href="${esc(c.href)}">Open</a>` : ''}</div>`).join('');
  document.getElementById('versus').innerHTML = !b ? '<div class="box warn">No baseline: nothing to compare with yet.</div>' : (b.compared || []).map(c =>
    `<div class=box><b>${esc(c.name)} against <code>${esc(b.record)}</code>${b.of ? ` (best of ${b.of} run${b.of === 1 ? '' : 's'} so far)` : ''}:</b> <span class=down>lost ${c.lost.length}</span>, <span class=up>gained ${c.gained.length}</span> · p = ${c.p}${c.p >= 0.05 ? ' <span class=m>(not distinguishable from run-to-run noise)</span>' : ''}
     ${c.lost.length ? `<details><summary>Lost</summary><ul>${c.lost.map(item).join('')}</ul></details>` : ''}${c.gained.length ? `<details><summary>Gained</summary><ul>${c.gained.map(item).join('')}</ul></details>` : ''}</div>`).join('');
  const rows = [...units].sort((x, y) => loss(y) - loss(x) || (ORDER[x.state] ?? 9) - (ORDER[y.state] ?? 9));
  document.getElementById('units').innerHTML = `<tr><th>${esc(cap(U))} (largest loss first)</th><th>State</th><th>Found / expected</th><th>Baseline</th><th>Missed</th></tr>` + rows.map(u =>
    `<tr><td>${esc(u.title || u.id)}<br><span class=m>${esc(u.id)}</span></td><td><span class="pill ${u.state === 'done' ? 'p-done' : u.state === 'at the gate' ? 'p-gate' : ''}">${esc(u.state)}</span></td>
     <td class=n>${u.found}/${u.expected}</td><td class=n>${u.baseline_found ?? '—'}${u.baseline_found != null && loss(u) > 0 ? ' <span class=down>▼' + loss(u) + '</span>' : u.baseline_found != null && loss(u) < 0 ? ' <span class=up>▲' + (-loss(u)) + '</span>' : ''}</td>
     <td>${(u.missed || []).length ? `<details data-k="${esc(u.id)}" ${open.has(u.id) ? 'open' : ''}><summary>${u.missed.length} missed</summary><ul>${u.missed.map(item).join('')}</ul></details>` : '<span class=m>none</span>'}</td></tr>`).join('');
  const pv = d.provenance || {}, t = pv.this || {}, bl = pv.baseline || {};
  const diff = Object.keys({...t, ...bl}).filter(k => (t[k] ?? '') !== (bl[k] ?? ''));
  document.getElementById('changedh').style.display = b ? '' : 'none';
  document.getElementById('changed').innerHTML = !b ? '' : !diff.length ? '<div class=box>Same as the baseline in every recorded respect.</div>'
    : '<div class=wrap><table><tr><th>What</th><th>This run</th><th>Baseline</th></tr>' + diff.map(k => `<tr><td>${esc(k)}</td><td><code>${esc(t[k] ?? '—')}</code></td><td><code>${esc(bl[k] ?? '—')}</code></td></tr>`).join('') + '</table></div>';
  const reasons = Object.entries((d.stuck || {}).reasons || {}), gated = units.filter(u => (u.gate || []).length);
  document.getElementById('stuck').innerHTML = (reasons.length ? `<div class=box><b>Refused ${reasons.reduce((a, [, n]) => a + n, 0)} times:</b> ${reasons.map(([k, n]) => `${esc(k)} ${n}`).join(' · ')}</div>` : '<div class=box>No gate has refused yet.</div>')
    + (gated.length ? `<div class=wrap><table><tr><th>${esc(cap(U))}</th><th>Gate history (oldest first)</th></tr>` + gated.map(u => `<tr><td>${esc(u.title || u.id)}<br><span class="pill ${u.state === 'done' ? 'p-done' : 'p-gate'}">${esc(u.state)}</span></td><td><ul>${u.gate.map(g =>
      `<li><b>${esc(g.status)}</b>${(g.parts || []).length ? ' · parts ' + g.parts.join(', ') : ''}${g.answer ? `<br><span class=m>answered:</span> “${esc(g.answer)}”` : ''}</li>`).join('')}</ul></td></tr>`).join('') + '</table></div>' : '');
  document.getElementById('health').innerHTML = (d.health || []).map(h => `<div class=tile><span class=m>${esc(h.name)}</span><b>${esc(h.value)}</b>${h.baseline != null ? `<span class=m>baseline ${esc(h.baseline)}</span>` : ''}</div>`).join('');
  document.getElementById('agents').innerHTML = `<tr><th>Agent</th><th>Model</th><th>Turns</th><th>${esc(cap(U))}s</th><th>Last step</th></tr>` + ((d.agents || []).length
    ? d.agents.map(a => `<tr><td>${esc(a.name)}</td><td>${esc(a.model)}</td><td class=n>${a.turns}</td><td>${(a.units || []).map(esc).join(', ')}</td><td>${esc(a.last)}</td></tr>`).join('')
    : '<tr><td colspan=5 class=m>No agent activity recorded yet.</td></tr>');
  document.querySelectorAll('details[data-k]').forEach(el => el.addEventListener('toggle', () => el.open ? open.add(el.dataset.k) : open.delete(el.dataset.k)));
}
async function tick() { try { show(await (await fetch('live.json', {cache: 'no-store'})).json()); } catch (e) { document.getElementById('head').textContent = 'live.json not readable: ' + e; } }
tick(); setInterval(tick, 15000);
</script></body></html>"""


def write(out_dir: Path, doc: dict) -> Path:
    """The page (replaced when this file's PAGE changed) and live.json (written atomically)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    page = out_dir / "index.html"
    if not page.exists() or page.read_text(encoding="utf-8") != PAGE:
        page.write_text(PAGE, encoding="utf-8")
    tmp = out_dir / "live.json.tmp"
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out_dir / "live.json")
    return out_dir / "live.json"


QUALITY_STYLE = """
.acts{margin-top:8px;padding-top:8px;border-top:1px dashed var(--line)}.row,.jr{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:4px 0}.row>*,.jr>*{min-width:0;max-width:100%}
button{font:inherit;font-size:.85rem;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:3px 10px;cursor:pointer}button.on{background:var(--ink);color:var(--card)}button:disabled{opacity:.5;cursor:default}
input,select{font:inherit;font-size:.85rem;max-width:100%;border:1px solid var(--line);border-radius:6px;padding:2px 6px;background:var(--card);color:var(--ink)}input[data-f="fields.title"],input[data-f=note]{flex:1 1 200px}
.savebar{position:sticky;top:0;z-index:2;background:var(--bg);padding:6px 0;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:8px;align-items:center}"""

QUALITY_PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Eval Quality</title><style>""" + STYLE + QUALITY_STYLE + """</style></head>
<body><main><h1 id=title>Quality eval</h1><p class=m id=head>Waiting for quality.json…</p><p class=lead id=lead></p><p class=m id=totals></p>
<div class=savebar><button type=button id=save>Save</button><span class=m id=st>Your answers change nothing until you Save; Save downloads one labels file.</span></div>
<h2>How a pass is decided</h2><div class=wrap><table id=rule></table></div>
<div id=units></div>
<p class=m>Every check names who decided it: <span class=src>code</span> computed, <span class='src s-judge'>judge</span> a model reading checked against your labels, <span class='src s-you'>you</span> needs your call (eval-quality/1). The judge's answers stay hidden until you answer a card.</p></main>
<script>
// LABELS-START
const LABELS = 'eval-labels/1', REASONS = ['vague', 'client_own', 'status', 'brainstorm', 'people', 'other'];
const KINDS = ['task', 'story', 'decision', 'risk'], COMMITS = ['committed', 'proposed'], EDIT = ['title', 'kind', 'commitment', 'owner', 'due'];
function cardIndex(d) {
  const C = {};
  for (const u of d.units || []) {
    for (const i of u.items || []) C[i.qid || i.id] = {type: 'item', c: i, u};
    for (const x of (u.extras || []).concat(u.extras_taken || [], u.containers || [])) if (x.qid) C[x.qid] = {type: 'node', c: x, u};
    for (const x of u.independent_only || []) if (x.qid) C[x.qid] = {type: 'only', c: x, u};
  }
  return C;
}
const nodeQuote = (u, id) => ((u.nodes || []).find(n => n.id === id) || {}).quote || '';
const isItem = (u, id) => (u.items || []).some(i => i.id === id);
function cardEntry(t, s) {
  const c = t.c, e = {action: s.action}, note = String(s.note || '').trim(), f = s.fields || {};
  if (t.type === 'item') {
    if (s.action === 'drop') {
      if (!REASONS.includes(s.reason) || (s.reason === 'other' && !note)) return null;
      e.reason = s.reason;
    }
    if (s.action === 'modify') {
      const was = c.edit || {}, ch = {};
      for (const k of EDIT) if (k in f && (f[k] ?? '') !== (was[k] ?? '')) ch[k] = f[k] ?? '';
      if (!Object.keys(ch).length || ('title' in ch && !String(ch.title).trim())) return null;
      e.fields = ch;
    } else if (!['keep', 'drop'].includes(s.action)) return null;
  } else if (t.type === 'node') {
    const p = c.produced || {};
    if (s.action === 'add') {
      const kind = s.kind || p.kind;
      if (!KINDS.includes(kind) || !COMMITS.includes(s.commitment)) return null;
      const title = String(f.title ?? p.title ?? '').trim();
      e.kind = kind;
      e.commitment = s.commitment;
      e.fields = Object.assign(title ? {title} : {}, {owner: f.owner ?? p.owner ?? '', due: f.due ?? p.due ?? ''});
    } else if (s.action === 'not_work') {
      if (!REASONS.includes(s.reason) || (s.reason === 'other' && !note)) return null;
      e.reason = s.reason;
    } else if (s.action === 'duplicate') {
      if (!s.dup_of) return null;
      e.dup_of = s.dup_of;
      const q = isItem(t.u, s.dup_of) ? '' : nodeQuote(t.u, s.dup_of);
      if (q) e.dup_of_quote = q;
    } else return null;
    if (p.quote) e.quote = p.quote;
  } else return null;
  if (note) e.note = note;
  if (typeof s.seen_judge === 'boolean') e.seen_judge = s.seen_judge;
  if (s.carried_from) e.carried_from = s.carried_from;
  return e;
}
function judgeEntry(t, ask, s) {
  const j = (t.c.judge || []).find(x => x.ask === ask);
  if (!j || (s.disagree && s.value === j.value) || !(j.options || []).concat([j.value]).includes(s.value)) return null;
  const e = {action: s.value === j.value ? 'agree' : 'disagree', expected: s.value};
  if (t.type === 'item') {
    const q = ask === 'which_node' ? (s.value === 'none' ? '' : nodeQuote(t.u, s.value)) : (t.c.produced || {}).quote || '';
    if (q) e.quote = q;
  }
  if (typeof s.seen_judge === 'boolean') e.seen_judge = s.seen_judge;
  if (s.carried_from) e.carried_from = s.carried_from;
  return e;
}
function labelsFile(d, S, savedAt) {
  const C = cardIndex(d), answers = {};
  let incomplete = 0;
  for (const [k, s] of Object.entries(S || {})) {
    if (!s) continue;
    let e = null;
    if (k.startsWith('judge:')) {
      if (!s.disagree && (s.value == null || s.value === '')) continue;
      const rest = k.slice(6), at = rest.lastIndexOf(':'), t = C[rest.slice(0, at)];
      e = t ? judgeEntry(t, rest.slice(at + 1), s) : null;
    } else {
      if (!s.action) continue;
      e = C[k] ? cardEntry(C[k], s) : null;
    }
    if (e) answers[k] = e; else incomplete++;
  }
  return {file: {schema: LABELS, stamp: d.stamp, record: d.record, key_version: d.key_version, key_sha: d.key_sha, saved_at: savedAt, answers}, incomplete};
}
function stateFrom(t, e, from) {
  const s = {action: e.action, carried_from: from};
  for (const k of ['reason', 'note', 'kind', 'commitment', 'dup_of']) if (e[k] != null) s[k] = e[k];
  if (t.type === 'item' && e.action === 'modify') s.fields = Object.assign({}, t.c.edit || {}, e.fields || {});
  if (t.type === 'node' && e.action === 'add') {
    const p = t.c.produced || {};
    s.fields = Object.assign({title: p.title || '', owner: p.owner || '', due: p.due || ''}, e.fields || {});
    s.kind = e.kind || p.kind;
  }
  if (s.dup_of && !isItem(t.u, s.dup_of) && !(t.u.nodes || []).some(n => n.id === s.dup_of)) delete s.dup_of;
  if (typeof e.seen_judge === 'boolean') s.seen_judge = e.seen_judge;
  return s;
}
// LABELS-END
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const SYM = {ok: '✓', no: '✗', check: '?'};
const pair = p => p && p[1] ? `${p[0]}/${p[1]}` : '—';
const fmt = v => v == null ? '—' : typeof v === 'number' && v <= 1 && !Number.isInteger(v) ? Math.round(100 * v) + '%' : esc(v);
let D = {}, C = {}, S = {}, R = {}, I = 'independent', T = false;
const draftKey = () => 'eval-labels-draft:' + (D.stamp || '');
function loadDraft() { try { const x = JSON.parse(localStorage.getItem(draftKey()) || 'null'); if (x && x.S) { S = x.S; R = x.R || {}; return true; } } catch (e) { /* no storage: the page works without a draft */ } return false; }
function keepDraft() { try { localStorage.setItem(draftKey(), JSON.stringify({S, R})); } catch (e) { /* no storage */ } }
function dropDraft() { try { localStorage.removeItem(draftKey()); } catch (e) { /* no storage */ } }
function side(title, d, keys) {
  if (!d) return `<div class=col><h4>${esc(title)}</h4><p class=m>nothing</p></div>`;
  if (d.node === null) return `<div class=col><h4>${esc(title)}</h4><p class=miss>nothing · ${esc(d.stage)}</p></div>`;
  return `<div class=col><h4>${esc(title)}</h4><table class=kv>${keys.filter(k => k in d).map(k => `<tr><th>${k}</th><td>${esc(d[k] ?? '—')}</td></tr>`).join('')}</table></div>`;
}
const JUDGED_ROWS = ['Work found, correctly', 'Nothing two readers agree on is missed', 'Inventions', 'Key gap candidates', 'Precision (judged)', 'Judge agrees with you'];
const perUnit = (p, units) => { const k = Object.keys(p).find(x => /^per_.+_to_delete$/.test(x));   // the doc's own burden; computed only when it carries none
  return k ? p[k] : units ? Math.round(100 * (p.wrong.not_work + p.wrong.duplicate) / units) / 100 : null; };
const precisionLine = (p, U, units) => !p ? '' : p.rate == null ? `precision ${esc(p.flag || 'not judged yet')}` :   // the burden from the unit count: the page names no unit
  `precision <b>${Math.round(100 * p.rate)}%</b> (lower bound ${p.lb95 == null ? '—' : Math.round(100 * p.lb95) + '%'}), ${p.n} judged, ${perUnit(p, units) ?? '—'} to delete per ${esc(U)}${p.flag ? ` <span class=m>(${esc(p.flag)})</span>` : ''}`;
const totalsHidden = d => (d.judged || 0) > 0 && !T && Object.keys(C).some(q => C[q].c.judge && answered(q) && !R[q]);
const hidden = q => !!((C[q] || {}).c || {}).judge && !R[q];
const masked = (cs, hide) => (cs || []).map(c => hide && c.by === 'judge' ? Object.assign({}, c, {verdict: 'hidden', why: 'hidden until you answer'}) : c);
const checks = (cs, hide) => `<div class=wrap><table class=checks><tr><th></th><th>Check</th><th>Decided by</th><th>Why</th></tr>${masked(cs, hide).map(c =>
  `<tr><td><span class='v v-${esc(c.verdict)}'>${SYM[c.verdict] || '·'}</span></td><td>${esc(c.name)}</td><td><span class='src s-${esc(c.by)}'>${esc(c.by)}</span></td><td class=m>${esc(c.why)}</td></tr>`).join('')}</table></div>`;
const view = (c, q) => hidden(q) && c.before_judge ? {checks: c.before_judge.checks, agreement: c.before_judge.agreement ?? c.agreement, hide: false}
  : {checks: c.checks, agreement: hidden(q) && c.found_by_judge ? '—' : c.agreement, hide: hidden(q)};
const extrasOf = u => {
  const at = {};
  (u.nodes || []).forEach((n, k) => { at[n.id] = k; });
  return (u.extras || []).concat((u.extras_taken || []).filter(x => !R[x.taken_by_judge])).sort((a, b) => (at[a.produced.node] ?? 1e9) - (at[b.produced.node] ?? 1e9));
};
const tone = (cs, hide) => { const v = masked(cs, hide); return v.some(c => c.verdict === 'no') ? 'bad' : v.some(c => c.verdict === 'check') ? 'warn' : 'good'; };
const opts = (vals, cur, blank) => {
  const list = vals.map(v => Array.isArray(v) ? v : [v, v]);
  if (cur != null && cur !== '' && !list.some(([v]) => v === cur)) list.push([cur, cur]);
  return (blank ? `<option value=''>${esc(blank)}</option>` : '') + list.map(([val, label]) => `<option value='${esc(val)}'${val === cur ? ' selected' : ''}>${esc(label)}</option>`).join('');
};
const nodeLabel = (u, id) => { const n = (u.nodes || []).find(x => x.id === id); return n ? `${id} · ${String(n.title || '').slice(0, 60)}` : id; };
function answered(q) { return !!(S[q] && S[q].action) || ((C[q].c.judge) || []).some(j => (S[`judge:${q}:${j.ask}`] || {}).value); }
function acts(q) {
  const t = C[q], s = S[q] || {}, c = t.c, u = t.u, y = c.yours, f = s.fields || {};
  let h = y ? `<p class=m>answered before (${esc(y.source)}): <b>${esc(y.answer)}</b></p>` : '';
  const btn = (a, label) => `<button type=button class='${s.action === a ? 'on' : ''}' data-act=set data-v=${a}>${label}</button>`;
  const reason = () => `<div class=row><select data-f=reason>${opts(REASONS, s.reason, 'reason…')}</select><input data-f=note placeholder=note value='${esc(s.note || '')}'></div>`;
  const text = (k, label, type) => `<input data-f='fields.${k}' type=${type || 'text'} placeholder=${label} aria-label=${label} value='${esc(f[k] ?? '')}'>`;
  if (t.type === 'item') {
    h += `<div class=row>${btn('keep', 'Keep')}${btn('drop', 'Drop')}${btn('modify', 'Modify')}</div>`;
    if (s.action === 'drop') h += reason();
    if (s.action === 'modify') h += `<div class=row>${text('title', 'title')}<select data-f='fields.kind'>${opts(KINDS, f.kind)}</select><select data-f='fields.commitment'>${opts(COMMITS, f.commitment)}</select>${text('owner', 'owner')}${text('due', 'due', 'date')}</div>`;
  } else if (t.type === 'node') {
    h += `<div class=row>${btn('add', 'Add')}${btn('not_work', 'Not work')}${btn('duplicate', 'Duplicate of')}</div>`;
    if (s.action === 'add') h += `<div class=row><select data-f=kind>${opts(KINDS, s.kind)}</select><select data-f=commitment>${opts(COMMITS, s.commitment, 'commitment…')}</select>${text('title', 'title')}${text('owner', 'owner')}${text('due', 'due', 'date')}</div>`;
    if (s.action === 'not_work') h += reason();
    if (s.action === 'duplicate') h += `<div class=row><select data-f=dup_of>${opts((u.items || []).map(i => [i.id, `${i.id} · ${String((i.expected || {}).title || '').slice(0, 60)}`]).concat((u.nodes || []).filter(n => n.id !== c.produced.node).map(n => [n.id, nodeLabel(u, n.id)])), s.dup_of, 'which one…')}</select></div>`;
  }
  const js = c.judge || [];
  if (js.length) {
    h += js.map(j => {
      const js2 = S[`judge:${q}:${j.ask}`] || {}, v = js2.value || '', y2 = j.yours;
      return `<div class=jr><span>${esc(j.name)}</span><select data-j='${esc(j.ask)}'>${opts(j.options.map(o => [o, j.ask === 'which_node' || j.ask === 'duplicate_of' ? nodeLabel(u, o) : o]), v, 'your answer…')}</select>`
        + (y2 ? `<span class=m>answered before (${esc(y2.source)})</span>` : '')
        + (R[q] ? `<span class=m>judge: <b>${esc(j.value)}</b> · ${esc(j.why)}</span><button type=button data-act=agree data-j='${esc(j.ask)}'>Agree</button><button type=button data-act=disagree data-j='${esc(j.ask)}'>Disagree</button>${js2.disagree && (!v || v === j.value) ? '<span class=down>choose the right answer</span>' : v ? (v === j.value ? '<span class=up>you agree</span>' : '<span class=down>you disagree</span>') : ''}` : '')
        + '</div>';
    }).join('');
    h += R[q] ? '' : `<div class=row><button type=button data-act=reveal${answered(q) ? '' : ' disabled'}>Show the judge's answer</button>${answered(q) ? '' : '<span class=m>answer first</span>'}</div>`;
  }
  return h ? `<div class=acts>${h}</div>` : '';
}
function itemCard(i, u) {
  const q = i.qid || i.id, v = view(i, q);
  return `<section class='card ${tone(v.checks, v.hide)}' data-q='${esc(q)}'><div class=top><code>${esc(i.id)}</code> <span class=m>${esc(i.kind)}</span> <span class=ag>${esc(v.agreement)}</span></div>
    <blockquote>[${esc(i.ts)}] <b>${esc(i.speaker)}:</b> “${esc(i.quote)}”</blockquote>
    <div class=cols>${side('Expected', i.expected, ['title', 'owner', 'due', 'status'])}${side('Produced' + (i.produced && i.produced.node ? ' (' + i.produced.node + ')' : ''), i.produced, ['title', 'owner', 'due', 'status', 'parent', 'quote', 'matched_by'])}${side(I + (i.independent ? ` (${i.independent.kind}, ${i.independent.at})` : ''), i.independent, ['what', 'owner', 'due', 'matched_by'])}</div>${checks(v.checks, v.hide)}${acts(q)}</section>`;
}
function nodeCard(x, u) {
  const q = x.qid, p = x.produced, v = view(x, q);
  return `<section class='card ${tone(v.checks, v.hide)}' data-q='${esc(q)}'><div class=top><code>${esc(p.node)}</code> <span class=m>${esc(p.kind)}</span> <span class=ag>${esc(x.group)}</span></div>
    <blockquote>“${esc(p.quote)}”</blockquote><div class=cols>${side('Produced', p, ['title', 'owner', 'due', 'status', 'parent'])}${side(I + (x.independent ? ` (${x.independent.at})` : ''), x.independent, ['what', 'owner', 'due'])}</div>${checks(v.checks, v.hide)}${acts(q)}</section>`;
}
function onlyCard(x, u) {
  const q = x.qid, v = view(x, q);
  return `<section class='card ${tone(v.checks, v.hide)}' data-q='${esc(q)}'><div class=top><b>${esc(x.what)}</b> <span class=m>(${esc(x.kind)}, ${esc(x.at)}${x.owner ? ', ' + esc(x.owner) : ''})</span></div>${checks(v.checks, v.hide)}${acts(q)}</section>`;
}
const card = q => { const t = C[q]; return t.type === 'item' ? itemCard(t.c, t.u) : t.type === 'node' ? nodeCard(t.c, t.u) : onlyCard(t.c, t.u); };
function count() { const {file, incomplete} = labelsFile(D, S, ''); return `${Object.keys(file.answers).length} answers` + (incomplete ? `, ${incomplete} incomplete` : '') + ' · not saved yet'; }
function changed(q, redraw) {
  keepDraft();
  if (redraw) for (const el of document.querySelectorAll('section[data-q]')) if (el.dataset.q === q) el.outerHTML = card(q);
  document.getElementById('st').textContent = count();
}
function show(d) {
  const U = d.unit_label || 'unit', s = d.summary || {}, r = d.run || {};
  I = (d.independent || {}).name || 'independent';
  document.getElementById('title').textContent = (d.title || 'Quality eval');
  document.getElementById('head').innerHTML = `record <code>${esc(r.record)}</code> · code <code>${esc(r.code)}</code> · reader tier ${esc(r.tier)} · judge tier ${esc((r.judge_tiers || []).join(', ') || 'none yet')} · key ${esc(d.key || '?')} · ${esc(I)}: notes for ${(d.independent || {}).with_notes ?? 0} of ${(d.independent || {}).units ?? 0} ${esc(U)}s, a recording only for ${(d.independent || {}).recording_only ?? 0}`;
  const veil = totalsHidden(d), judgedScope = s.fields_scope === 'judged same work';
  const owners = `owner right ${pair(s.owner)}, status right ${pair(s.status)}${s.fields_scope ? ` (on ${esc(s.fields_scope)})` : ''}`;
  document.getElementById('totals').innerHTML = veil ? `<button type=button id=showtotals>Show the judge's totals</button> <span class=m>The totals below leave out everything the judge decided. Showing them also shows the judge's answer on every card, so labels you set afterwards are saved as made after seeing it.</span>` : '';
  if (veil) document.getElementById('lead').innerHTML = `Found <b>${pair(s.found)}</b> by a plain count${judgedScope ? '' : ' · ' + owners}`;
  else
  document.getElementById('lead').innerHTML = (d.judged ? `Found correctly <b>${pair(s.found_correct)}</b> (a plain count says ${pair(s.found)}) · ` : `Found <b>${pair(s.found)}</b>, <b>not yet checked by the judge</b>: a found item may still be different work · `) + `<b>${s.agreed_missed ?? 0}</b> missed that two readers agree on${s.independent_coverage ? ` (over ${s.independent_coverage[0]} of ${s.independent_coverage[1]} ${esc(U)}s with ${esc(I)} notes)` : ''} · owner right ${pair(s.owner)}, status right ${pair(s.status)}${s.fields_scope ? ` (on ${esc(s.fields_scope)})` : ''} · ${[s.inventions == null ? '' : `${s.key_gap_candidates ?? 0} key gap candidates · ${s.inventions} inventions`, s.not_in_key_unjudged ? `${s.not_in_key_unjudged} not in the key, not yet judged` : '', (s.kind_errors || []).length ? `${s.kind_errors.length} found as another kind` : '', s.judged_items ? `${pair(s.judged_items)} expected items judged` : '', precisionLine(s.precision, U, (d.units || []).length)].filter(Boolean).join(' · ')}`
    + (s.judge_agreement_overall && s.judge_agreement_overall.n ? ' · judge agreement: ' + Object.entries(s.judge_agreement || {}).map(([k, v]) => `${esc(k)} ${v.agree}/${v.n}`).join(', ') : ' · judge not validated')
    + (s.yours_unmatched ? ` · ${s.yours_unmatched} earlier answers match no card` : '');
  document.getElementById('rule').innerHTML = '<tr><th>Measure</th><th>This run</th><th>Your bar</th><th>Pass</th></tr>' + (d.rule || []).filter(x => !veil || !JUDGED_ROWS.includes(x.measure) && !(judgedScope && /^(Owner|Status) right$/.test(x.measure))).map(x =>
    `<tr><td>${esc(x.measure)}${x.note ? ` <span class=m>(${esc(x.note)})</span>` : ''}</td><td>${fmt(x.value)}</td><td>${x.bar == null ? '<span class=m>not set</span>' : fmt(x.bar)}</td><td>${x.flag ? `<span class=m>${esc(x.flag)}</span>` : x.pass == null ? '—' : x.pass ? '<span class=up>yes</span>' : '<span class=down>no</span>'}</td></tr>`).join('');
  document.getElementById('units').innerHTML = (d.units || []).map(u => {
    const cards = u.items.map(i => itemCard(i, u)).join('');
    const ex = extrasOf(u), extras = ex.length ? `<h3>Created, not expected</h3>${ex.map(x => x.qid ? nodeCard(x, u) : '').join('')}` : '';
    const held = (u.containers || []).length ? `<h3>Holds found work</h3>${u.containers.map(x => x.qid ? nodeCard(x, u) : '').join('')}` : '';
    const only = u.independent_only.length ? `<h3>Only ${esc(I)} has it</h3>${u.independent_only.map(x => x.qid ? onlyCard(x, u) : '').join('')}` : '';
    const src = u.independent ? (u.independent_processed ? `${esc(I)}: <code>${esc(u.independent)}</code>` : `${esc(I)} has a recording (<code>${esc(u.independent)}</code>) but no notes yet`) : `no ${esc(I)} call`;
    return `<h2>${esc(u.title || u.id)}</h2><p class=m><code>${esc(u.id)}</code> · ${src}</p>${cards}${held}${extras}${only}`; }).join('');
}
function start(d) {
  D = d;
  C = cardIndex(d);
  if (!loadDraft()) {
    for (const [q, t] of Object.entries(C)) {
      if (t.c.yours && t.c.yours.entry) S[q] = stateFrom(t, t.c.yours.entry, t.c.yours.source);
      for (const j of t.c.judge || []) {
        const e = (j.yours || {}).entry;
        const v = e && (e.expected ?? (e.action === 'agree' ? j.value : ''));
        if (v) S[`judge:${q}:${j.ask}`] = Object.assign({value: v, carried_from: j.yours.source}, typeof e.seen_judge === 'boolean' ? {seen_judge: e.seen_judge} : {});
      }
    }
  }
  show(d);
  document.getElementById('st').textContent = d.stamp ? count() : 'This page was built without a stamp: rebuild it to save answers.';
}
document.getElementById('totals').addEventListener('click', ev => {
  if (!ev.target || ev.target.id !== 'showtotals') return;
  T = true;
  for (const q of Object.keys(C)) if (C[q].c.judge) R[q] = true;
  keepDraft(); show(D); document.getElementById('st').textContent = count();
});
const units = document.getElementById('units');
const qOf = el => (el.closest('section[data-q]') || {dataset: {}}).dataset.q;
units.addEventListener('click', ev => {
  const b = ev.target.closest('button[data-act]'), q = b && qOf(b);
  if (!q || !C[q]) return;
  const t = C[q], act = b.dataset.act;
  if (act === 'reveal') R[q] = true;
  else if (act === 'set') {
    const v = b.dataset.v, s = S[q] || {};
    if (s.action === v) delete S[q];
    else {
      const p = t.c.produced || {};
      delete s.carried_from;
      S[q] = Object.assign(s, {action: v, seen_judge: !!R[q]});
      if (v === 'modify' && !s.fields) s.fields = Object.assign({}, t.c.edit || {});
      if (v === 'add' && !s.fields) { s.fields = {title: p.title || '', owner: p.owner || '', due: p.due || ''}; s.kind = s.kind || p.kind; }
    }
  } else {
    const j = (t.c.judge || []).find(x => x.ask === b.dataset.j);
    if (!j) return;
    S[`judge:${q}:${j.ask}`] = act === 'agree' ? {value: j.value, seen_judge: !!R[q]} : {value: '', disagree: true, seen_judge: !!R[q]};
  }
  if (act === 'reveal') { keepDraft(); show(D); document.getElementById('st').textContent = count(); return; }
  changed(q, true);
});
function edited(ev) {
  const el = ev.target, q = qOf(el);
  if (!q || !C[q]) return;
  if (el.dataset.j) {
    const k = `judge:${q}:${el.dataset.j}`;
    S[k] = Object.assign({value: el.value, seen_judge: !!R[q]}, (S[k] || {}).disagree ? {disagree: true} : {});
    changed(q, ev.type === 'change');
    return;
  }
  const f = el.dataset.f, s = S[q];
  if (!f || !s) return;
  if (f.startsWith('fields.')) (s.fields = s.fields || {})[f.slice(7)] = el.value; else s[f] = el.value;
  s.seen_judge = !!R[q];
  delete s.carried_from;
  changed(q, false);
}
units.addEventListener('change', edited);
units.addEventListener('input', edited);
document.getElementById('save').addEventListener('click', () => {
  if (!D.stamp) return;
  const {file, incomplete} = labelsFile(D, S, new Date().toISOString());
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(file, null, 1)], {type: 'application/json'}));
  a.download = 'labels-' + D.stamp + '.json';
  document.body.appendChild(a); a.click(); a.remove();
  dropDraft();
  document.getElementById('st').textContent = `Saved ${Object.keys(file.answers).length} answers` + (incomplete ? ` · ${incomplete} incomplete, not saved` : '');
});
fetch('quality.json', {cache: 'no-store'}).then(r => r.json()).then(start).catch(e => { document.getElementById('head').textContent = 'quality.json not readable: ' + e; });
</script></body></html>"""


def write_quality(out_dir: Path, doc: dict) -> Path:
    """The quality page (replaced when QUALITY_PAGE changed) and quality.json (written atomically)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    page = out_dir / "index.html"
    if not page.exists() or page.read_text(encoding="utf-8") != QUALITY_PAGE:
        page.write_text(QUALITY_PAGE, encoding="utf-8")
    tmp = out_dir / "quality.json.tmp"
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out_dir / "quality.json")
    return out_dir / "quality.json"
