"""The task bank results page: one page (evalview's shared style) that renders bank.json, either a run (kind trials or
replay) or a model comparison (kind models). Standard library only. bank.page_doc / bank.compare_models fill the data."""
import html as _html
import json
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parent
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import evalview  # noqa: E402
from evaltrace import event_words  # noqa: E402,F401  (one view of an event, shared with the story)

EXTRA = """
.dl{color:var(--mut)}td.d{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}.fail{border-left:3px solid var(--bad);padding-left:10px;margin:8px 0}
ol.why{margin:4px 0 4px 18px;padding:0}.why li{font-size:.86rem}"""

PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Task Bank</title><style>""" + evalview.STYLE + EXTRA + """</style></head>
<body><main><h1 id=title>Task bank</h1><p class=m id=head>Waiting for bank.json…</p><div id=body></div></main>
<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const pct = (ok, n) => n ? Math.round(100 * ok / n) + '%' : '—';
const num = (v, d = 1) => v == null ? '—' : (Math.round(v * 10 ** d) / 10 ** d).toString();
const sgn = (v, d = 2) => v == null ? '—' : (v > 0 ? '+' : '') + num(v, d);
const yn = v => v == null ? '—' : v ? 'yes' : 'no';
const stat = (s, d = 1) => s && s.n ? `${num(s.median, d)} <span class=m>(n=${s.n})</span>` : '—';
const pq = (s, d = 1) => s && s.n ? `${num(s.p50, d)} · ${num(s.p95, d)} <span class=m>(n=${s.n})</span>` : '<span class=m>no data (n=0)</span>';
const KINDS = [['input', 'input'], ['output', 'output'], ['cache_read', 'cache read'], ['cache_write', 'cache write']];
const usageCells = u => KINDS.map(k => `<td class=n>${pq((u.tokens || {})[k[0]], 0)}</td>`).join('') + `<td class=n>${pq(u.turns, 0)}</td><td class=n>${pq(u.minutes, 1)}</td>`;
const usageHead = first => `<tr><th>${first}</th>${KINDS.map(k => `<th>Tokens ${k[1]}, p50 · p95</th>`).join('')}<th>Turns, p50 · p95</th><th>Minutes, p50 · p95</th></tr>`;
const tile = (name, val, sub) => `<div class=tile><span class=m>${esc(name)}</span><b>${val}</b>${sub ? `<span class=m>${sub}</span>` : ''}</div>`;

function run(doc) {
  const A = doc.aggregate, O = A.overall, P = doc.provenance || {};
  document.getElementById('title').textContent = 'Task bank: ' + (doc.run || '');
  document.getElementById('head').innerHTML = `<b>${esc(doc.kind)}</b> · bank <code>${esc(P.bank_version)}</code> (<code>${esc((P.bank_sha256 || '').slice(0, 10))}</code>)`
    + (P.tier ? ` · tier ${esc(P.tier)} <span class=m>${esc(P.model || '')}</span>` : '') + (P.k ? ` · k = ${esc(P.k)}` : '')
    + (P.code_version ? ` · code <code>${esc(P.code_version)}</code>` : '') + (P.started ? ` · started ${esc(P.started)}` : '');
  let h = doc.label ? `<div class="box warn">${esc(doc.label)}</div>` : '';
  const U = A.usage || {}, R = U.round || {}, RT = R.tokens || {}, RM = R.minutes || {}, D = A.api_equivalent_usd || {};
  h += `<h2>Usage</h2><div class=tiles>${tile('Round minutes', RM.n ? num(RM.value, 1) : '—', RM.n ? 'wall time of the run' : 'no data (n=0)')}`
    + KINDS.map(k => tile(`Tokens ${k[1]}`, RT[k[0]] == null ? '—' : num(RT[k[0]], 0), RT[k[0]] == null ? 'no data (n=0)' : `over ${(R.tokens_n || {})[k[0]] || 0} trials`)).join('') + `</div>`;
  const cls = Object.entries(U.by_class || {}).flatMap(([d, vs]) => Object.entries(vs).map(([v, u]) => [`${d}: ${v}`, u]));
  if (!cls.length) h += `<p class=m>This run has no class data: the bank's tasks carry no class, so nothing is reported per class.</p>`;
  if (cls.length) h += `<div class=wrap><table>${usageHead('Class')}${cls.map(([c, u]) => `<tr><td>${esc(c)} <span class=m>(${u.tasks} tasks)</span></td>${usageCells(u)}</tr>`).join('')}</table></div>`;
  h += `<div class=wrap><table>${usageHead('Task')}${Object.entries(U.tasks || {}).map(([t, u]) => `<tr><td><code>${esc(t)}</code></td>${usageCells(u)}</tr>`).join('')}</table></div>`;
  h += `<p class=m>API-equivalent $ (not a bill): total ${D.n ? num(D.total, 2) : '—'} · per meeting p50 · p95 ${pq(D.per_meeting, 2)} · per passed must_create ${D.usd_per_correct_item == null ? '—' : num(D.usd_per_correct_item, 2)}</p>`;
  h += `<div class=tiles>${tile('Tasks passed at least once (pass@k)', `${O.pass_at_k.ok}/${O.pass_at_k.n} · ${pct(O.pass_at_k.ok, O.pass_at_k.n)}`, `over ${O.pass_at_k.n} tasks with a valid trial`)}`
    + tile('Tasks passed every time (pass^k)', `${O.pass_hat_k.ok}/${O.pass_hat_k.n} · ${pct(O.pass_hat_k.ok, O.pass_hat_k.n)}`, `over ${O.pass_hat_k.n} tasks with a valid trial`)
    + tile('Valid trials', `${O.valid}/${O.trials}`, `${O.tasks} tasks`) + `</div>`;
  if (O.one_block) h += `<div class=box><b>One block (${O.one_block.tasks} tasks, own class, no gate):</b> pass^k ${O.one_block.pass_hat_k.ok}/${O.one_block.pass_hat_k.n} · ${pct(O.one_block.pass_hat_k.ok, O.one_block.pass_hat_k.n)}. <span class=m>Outside the main pass bar above.</span></div>`;
  const B = A.bars;
  if (B) h += `<h2>Bars</h2><div class="box ${B.run === 'fail' || B.run === 'unverified' ? 'warn' : ''}"><b>Hard bars:</b> ${esc(B.run)}${B.source ? ` <span class=m>from ${esc(B.source)}</span>` : ' <span class=m>no bars file: every bar is not set</span>'}</div>`
    + `<div class=wrap><table><tr><th>Bar</th><th>Scope</th><th>Measure</th><th>Value</th><th>Bar</th><th>n</th><th>Result</th></tr>`
    + B.rows.map(r => `<tr><td><code>${esc(r.key)}</code>${r.hard ? ' <b>hard</b>' : ''} <span class=m>${esc(r.kind)}</span></td><td>${esc(r.scope ?? '—')}</td><td class=m>${esc(r.measure)}</td>`
      + `<td class=n>${r.value == null ? '—' : typeof r.value === 'boolean' ? yn(r.value) : num(r.value, 4)}</td><td class=n>${r.bar == null ? 'not set' : esc(typeof r.bar === 'object' ? JSON.stringify(r.bar) : r.bar)}</td><td class=n>${r.n}${r.of != null ? '/' + r.of : ''} ${esc(r.unit || '')}${r.provisional ? ' <span class=m>(n&lt;20: p95 = max)</span>' : ''}</td>`
      + `<td><span class="pill ${r.result === 'pass' ? 'p-done' : r.result === 'fail' || r.result === 'unverified' ? 'p-gate' : ''}">${esc(r.result)}</span></td></tr>`).join('') + `</table></div>`;
  const bad = [['timeout', O.timeout], ['error', O.error], ['invalid', O.invalid], ['missing', O.missing]];
  h += `<div class="box ${bad.some(x => x[1]) ? 'warn' : ''}"><b>Not scored (never counted as a miss):</b> ${bad.map(x => `${x[0]} ${x[1] || 0}`).join(' · ')}. <span class=m>Only valid (done) trials are in a denominator.</span></div>`;
  const caps = Object.entries(A.caps || {});
  h += `<h2>By capability</h2>` + (caps.length ? `<div class=wrap><table><tr><th>Capability</th><th>Checks passed</th><th>Share</th></tr>${caps.map(([c, v]) => `<tr><td>${esc(c)}</td><td class=n>${v.ok}/${v.n} <span class=m>(n=${v.n} checks)</span></td><td class=n>${pct(v.ok, v.n)}</td></tr>`).join('')}</table></div>` : '<p class=m>No scored checks.</p>');
  h += `<h2>By task</h2><div class=wrap><table><tr><th>Task</th><th>pass@k</th><th>pass^k</th><th>Valid / trials</th><th>Timeout · error · invalid</th><th>Latency s, median</th><th>Tokens, median</th><th>API-equivalent $, median</th><th>Errors per trial</th></tr>`
    + doc.tasks.map(t => { const r = {valid: 0, trials: 0, timeout: 0, error: 0, invalid: 0, ...(A.tasks[t.task] || {})}, er = r.error_rates || {}; return `<tr><td><code>${esc(t.task)}</code></td><td>${yn(r.pass_at_k)}</td><td>${yn(r.pass_hat_k)}</td>
      <td class=n>${r.valid} valid of ${r.trials}</td><td class=n>${r.timeout} · ${r.error} · ${r.invalid}</td><td class=n>${stat(r.latency_s)}</td><td class=n>${stat(r.tokens, 0)}</td><td class=n>${stat(r.cost_usd, 2)}</td>
      <td class=m>${Object.entries(er).map(([k, v]) => `${esc(k)} ${num(v, 2)}`).join(' · ') || '—'}${r.valid ? ` (n=${r.valid})` : ''}</td></tr>`; }).join('') + `</table></div>`;
  h += `<h2>Trials and failed checks</h2>`;
  for (const t of doc.tasks) {
    h += `<div class=card><div class=top><b><code>${esc(t.task)}</code></b></div>`;
    for (const x of t.trials) {
      h += `<details ${x.failed.length ? 'open' : ''}><summary><span class="pill ${x.pass ? 'p-done' : x.status !== 'done' ? 'p-gate' : ''}">${esc(x.status)}${x.status === 'done' ? (x.pass ? ' · pass' : ' · fail') : ''}</span> trial ${esc(x.trial)}`
        + ` <span class=m>${x.checks_n} checks · ${x.failed.length} failed${x.unscored ? ` · ${x.unscored} unscored nodes` : ''}${x.latency_s != null ? ` · ${num(x.latency_s, 0)} s` : ''}${x.cost_usd != null ? ` · $${num(x.cost_usd, 2)}` : ''}</span></summary>`;
      if (x.reason) h += `<p class=m>${esc(x.reason)}</p>`;
      for (const f of x.failed) {
        h += `<div class=fail><b>${esc(f.id)}</b> <span class=m>${esc(f.type)} · ${(f.caps || []).map(esc).join(', ')} · node ${esc(f.node || 'none')}</span>`
          + (f.quote ? `<blockquote>${esc(f.quote)}</blockquote>` : '')
          + (f.node_quote ? `<p class=m>The agent's node quoted:</p><blockquote>${esc(f.node_quote)}</blockquote>` : '')
          + (f.why.length ? `<ol class=why>${f.why.map(w => `<li><span class=m>${esc((w.at || '').slice(11, 19))} ${esc(w.agent)}</span> <b>${esc(w.step)}</b> ${esc(w.detail)}</li>`).join('')}</ol>` : '<p class=m>No trace steps are linked to this check.</p>')
          + (f.transcript ? `<a href="${esc(f.transcript)}">Open the transcript</a>` : '') + `</div>`;
      }
      h += `</details>`;
    }
    h += `</div>`;
  }
  document.getElementById('body').innerHTML = h;
}

function models(doc) {
  const a = doc.a, b = doc.b, O = doc.overall;
  document.getElementById('title').textContent = `Model comparison: ${a.tier} | ${b.tier}`;
  document.getElementById('head').innerHTML = `${esc(a.tier)} <span class=m>${esc(a.model || '')}</span> against ${esc(b.tier)} <span class=m>${esc(b.model || '')}</span> · delta = ${esc(b.tier)} minus ${esc(a.tier)}`;
  const sh = v => v == null ? '—' : pct(v.ok, v.n) + ` <span class=m>(${v.ok}/${v.n})</span>`;
  let h = `<div class="box ${doc.p != null && doc.p < 0.05 ? 'good' : ''}"><b>Paired on pass^k:</b> ${esc(num(doc.paired, 0))} tasks pair, ${esc(num(O.lost, 0))} lost, ${esc(num(O.gained, 0))} gained · McNemar p = ${esc(num(doc.p, 3))}${doc.p >= 0.05 ? ' <span class=m>(not distinguishable from noise)</span>' : ''} · n = ${esc(num(doc.paired, 0))} paired tasks.</div>`;
  const lists = [['only_in_a', 'Only in ' + a.tier], ['only_in_b', 'Only in ' + b.tier], ['no_valid_a', 'No valid trial in ' + a.tier], ['no_valid_b', 'No valid trial in ' + b.tier]];
  h += lists.filter(l => (doc[l[0]] || []).length).map(l => `<div class="box warn"><b>${esc(l[1])} (${doc[l[0]].length}, not compared):</b> ${doc[l[0]].map(x => `<code>${esc(x)}</code>`).join(' ')}</div>`).join('');
  h += `<h2>Overall</h2><div class=wrap><table><tr><th></th><th>${esc(a.tier)}</th><th>${esc(b.tier)}</th><th>delta</th></tr>`
    + ['pass_at_k', 'pass_hat_k'].map(k => `<tr><td>${k === 'pass_at_k' ? 'pass@k' : 'pass^k'} <span class=m>(tasks)</span></td><td class=n>${sh(O.a[k])}</td><td class=n>${sh(O.b[k])}</td><td class=d>${sgn(O.delta[k], 3)}</td></tr>`).join('')
    + `<tr><td>Valid trials</td><td class=n>${O.a.valid}/${O.a.trials}</td><td class=n>${O.b.valid}/${O.b.trials}</td><td></td></tr></table></div>`;
  const caps = Object.entries(doc.caps || {});
  h += `<h2>By capability</h2><div class=wrap><table><tr><th>Capability</th><th>${esc(a.tier)}</th><th>${esc(b.tier)}</th><th>delta (share)</th></tr>`
    + caps.map(([c, v]) => `<tr><td>${esc(c)}</td><td class=n>${v.a ? `${pct(v.a.ok, v.a.n)} <span class=m>(n=${v.a.n})</span>` : '—'}</td><td class=n>${v.b ? `${pct(v.b.ok, v.b.n)} <span class=m>(n=${v.b.n})</span>` : '—'}</td><td class=d>${sgn(v.delta.share, 3)}</td></tr>`).join('') + `</table></div>`;
  const cell = (s, k, d) => s[k] == null ? '—' : num(s[k], d);
  h += `<h2>By task</h2><div class=wrap><table><tr><th>Task</th><th>Valid ${esc(a.tier)} | ${esc(b.tier)}</th><th>pass@k ${esc(a.tier)} | ${esc(b.tier)} | delta</th><th>pass^k ${esc(a.tier)} | ${esc(b.tier)} | delta</th>`
    + `<th>Latency s, median (n)</th><th>Tokens, median (n)</th><th>Cost $, median (n)</th></tr>` + doc.tasks.map(t => {
      const x = t.a, y = t.b, d = t.delta;
      const tri = (k, kk, dd) => `${yn(x[k])} | ${yn(y[k])} | <b>${sgn(d[k], 0)}</b>`;
      const med = (k, nk, dd) => `${cell(x, k, dd)} (${x[nk]}) | ${cell(y, k, dd)} (${y[nk]}) | <b>${sgn(d[k], dd)}</b>`;
      return `<tr><td><code>${esc(t.task)}</code></td><td class=n>${x.valid}/${x.trials} | ${y.valid}/${y.trials}</td><td>${tri('pass_at_k')}</td><td>${tri('pass_hat_k')}</td>`
        + `<td class=n>${med('latency_median_s', 'latency_n', 1)}</td><td class=n>${med('tokens_median', 'tokens_n', 0)}</td><td class=n>${med('cost_median_usd', 'cost_n', 2)}</td></tr>`; }).join('') + `</table></div>`;
  document.getElementById('body').innerHTML = h;
}

fetch('bank.json', {cache: 'no-store'}).then(r => r.json()).then(doc => doc.kind === 'models' ? models(doc) : run(doc))
  .catch(e => { document.getElementById('head').textContent = 'bank.json not readable: ' + e; });
</script></body></html>"""


def write(out: Path, doc: dict) -> Path:
    """The page (replaced when this file's PAGE changed) and bank.json (written atomically)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    page = out / "index.html"
    if not page.exists() or page.read_text(encoding="utf-8") != PAGE:
        page.write_text(PAGE, encoding="utf-8")
    tmp = out / "bank.json.tmp"
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out / "bank.json")
    return out / "bank.json"


def transcript_page(trace: dict, title: str) -> str:
    """A readable page of one trial: every tool call and taska event of the trace model in time order, each row anchored
    by its time (`#t=<at>` scrolls to the first row at that time). Everything is escaped."""
    esc = lambda x: _html.escape(str(x if x is not None else ""), quote=True)
    names = {a["id"]: a.get("name") or a["id"] for a in trace.get("agents", [])}
    rows = []
    for sp in trace.get("spans", []):
        rows.append((sp.get("start") or "", 0, {"at": sp.get("start") or "", "agent": names.get(sp.get("agent"), sp.get("agent") or ""),
                                                "step": f"{sp.get('tool') or 'call'}{' (error)' if sp.get('error') else ''}",
                                                "detail": sp.get("step") or ""}))
    for e in trace.get("events", []):
        rows.append((e.get("at") or "", 1, event_words(e, names)))
    rows.sort(key=lambda r: (r[0], r[1]))
    seen, body = set(), []
    for at, _, w in rows:
        anchor = f' id="t={esc(at)}"' if at and at not in seen else ""
        seen.add(at)
        body.append(f"<tr{anchor}><td><code>{esc(at)}</code></td><td>{esc(w['agent'])}</td><td><b>{esc(w['step'])}</b></td><td>{esc(w['detail'])}</td></tr>")
    return ("<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content=\"width=device-width,initial-scale=1\">"
            f"<title>{esc(title)}</title><style>" + evalview.STYLE + "tr:target td{background:var(--warn-bg)}</style></head><body><main>"
            f"<h1>{esc(title)}</h1><p class=m>{len(rows)} steps in time order.</p><div class=wrap><table><tr><th>Time</th><th>Agent</th><th>Step</th><th>Detail</th></tr>"
            + "".join(body) + "</table></div></main></body></html>")
