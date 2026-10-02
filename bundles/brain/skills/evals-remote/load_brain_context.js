/**
 * promptfoo context provider for a REMOTE, key-authenticated MCP brain (any brain).
 *
 * Drop-in for the existing evals pipeline: it reads the SAME vars the existing
 * generate_promptfoo.py already emits, so that generator drives any brain unchanged:
 *   BRAIN_URL     — the MCP endpoint. Falls back to the BRAIN_MCP_URL env var.
 *   query_suffix  — corpus terms appended to a secondary query (dual-query recall).
 * The API key is read from the BRAIN_API_KEY environment variable (run_eval.sh keeps it out
 * of the generated config) and handed to the Python client through its environment, never
 * on its command line.
 *
 * Searches state latest_only=true explicitly — what an agent sees by default — so a result
 * does not move when a server's default changes.
 *
 * Numbers are computed, not retrieved: when a question is metric-relevant, this also calls
 * list_metrics + get_metric for the most recent months and prepends a GOVERNED METRICS block,
 * marking any value another report restated or contradicts. Metric synonyms (e.g. an acronym
 * for a metric's name) come from the evals config `context.metric_synonyms`, never from code.
 *
 * MCP is implemented once in Python; this shells out to brain_mcp_client.py.
 * Set EVALS_PY to a venv python that has fastmcp installed.
 */
const { execFile } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const RECENT_MONTHS = 6;

function clientSpawn(tool, args, mcpUrl, apiKey) {
  const argv = [path.join(__dirname, 'brain_mcp_client.py'), 'call', tool, '--json', JSON.stringify(args)];
  if (mcpUrl) { argv.push('--url', mcpUrl); }
  const env = { ...process.env };
  if (apiKey) { env.BRAIN_API_KEY = apiKey; }
  return { argv, env };
}

function callTool(tool, args, mcpUrl, apiKey) {
  return new Promise((resolve, reject) => {
    const { argv, env } = clientSpawn(tool, args, mcpUrl, apiKey);
    const py = process.env.EVALS_PY || 'python3';
    execFile(py, argv, { env, maxBuffer: 10 * 1024 * 1024 }, (err, stdout, stderr) => {
      if (err) { reject(new Error(stderr || err.message)); return; }
      try { resolve(JSON.parse(stdout)); } catch (e) { reject(e); }
    });
  });
}

function configSynonyms() {
  try {
    const cfg = JSON.parse(fs.readFileSync(process.env.EVALS_CONFIG, 'utf8'));
    return (cfg.context && cfg.context.metric_synonyms) || {};
  } catch (e) {
    return {};
  }
}

function hitsToChunks(res) {
  const hits = (res && res.hits) || [];
  return hits.map((h) => `[${h.source}] ${h.text || ''}`).filter(Boolean);
}

function relevantMetrics(question, metrics, synonyms) {
  // Punctuation becomes spaces, so a term ending a sentence ("what is our HT?") still matches.
  const q = ` ${question.toLowerCase().replace(/[^a-z0-9]+/g, ' ')} `;
  const out = [];
  for (const m of metrics) {
    const name = (m.name || '').toLowerCase();
    const desc = (m.description || '').toLowerCase();
    const tokens = new Set([...name.split('_'), ...desc.split(/[^a-z0-9]+/)].filter((t) => t.length > 3));
    let hit = [...tokens].some((t) => q.includes(t));
    if (!hit) {
      for (const [term, syn] of Object.entries(synonyms || {})) {
        const s = String(syn).toLowerCase();
        if (q.includes(` ${term.toLowerCase()} `) && (name.includes(s) || desc.includes(s))) { hit = true; break; }
      }
    }
    if (hit) out.push(m);
  }
  return out.slice(0, 3);
}

function monthsBefore(month, n) {
  const m = /^(\d{4})-(\d{2})$/.exec(String(month || ''));
  if (!m) return null;
  const idx = Number(m[1]) * 12 + (Number(m[2]) - 1) - n;
  return `${Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, '0')}`;
}

function rowLine(m, row) {
  let line = `  ${m.name} (${m.unit}) ${row.grain}/${row.entity} ${row.month} = ${row.value}  [${row.source_file}]`;
  const others = row.other_reported_values || [];
  if (row.restated || row.conflicting || others.length) {
    const kind = row.conflicting ? 'conflicting' : 'restated';
    line += ` (${kind}; also reported: ${others.map((o) => `${o.value} [${o.source_file}]`).join(', ')})`;
  }
  return line;
}

async function metricBlock(question, synonyms, call) {
  try {
    const cat = await call('list_metrics', {});
    const relevant = relevantMetrics(question, (cat && cat.metrics) || [], synonyms);
    const lines = [];
    for (const m of relevant) {
      try {
        // Rows come back oldest first, so anchor the window on the catalog's last month:
        // the most recent RECENT_MONTHS months, not the head of the whole history.
        const args = { name: m.name, limit: 100 };
        const start = monthsBefore(m.last_month, RECENT_MONTHS - 1);
        if (start) { args.start_month = start; }
        const r = await call('get_metric', args);
        for (const row of (r.rows || []).slice(-8)) { lines.push(rowLine(m, row)); }
      } catch (e) { /* skip this metric */ }
    }
    if (!lines.length) return '';
    return `GOVERNED METRICS (authoritative — computed from marts):\n${lines.join('\n')}\n\n---\n\n`;
  } catch (e) {
    return '';
  }
}

async function buildContext(question, opts, call) {
  const querySuffix = String(opts.querySuffix || 'specific details findings decisions evidence');
  const [primary, secondary, metrics] = await Promise.all([
    call('search_knowledge', { query: question, limit: 50, latest_only: true }).then(hitsToChunks),
    call('search_knowledge', { query: `${question} ${querySuffix}`, limit: 30, latest_only: true }).then(hitsToChunks),
    metricBlock(question, opts.synonyms, call),
  ]);
  const seen = new Set();
  const merged = [];
  for (const chunk of [...primary, ...secondary]) {
    const key = chunk.split('\n')[0].trim();
    if (!seen.has(key)) { seen.add(key); merged.push(chunk); }
  }
  return metrics + (merged.join('\n\n---\n\n') || 'No context retrieved from brain.');
}

module.exports = async function (varName, prompt, otherVars) {
  const question = String(otherVars.question || '').trim();
  if (!question) return { error: 'question must be resolved before brain context' };

  const mcpUrl = otherVars.BRAIN_URL || process.env.BRAIN_MCP_URL || process.env.BRAIN_URL;
  const apiKey = process.env.BRAIN_API_KEY;
  const call = (tool, args) => callTool(tool, args, mcpUrl, apiKey);
  try {
    const output = await buildContext(question,
      { querySuffix: otherVars.query_suffix, synonyms: configSynonyms() }, call);
    return { output };
  } catch (err) {
    return { error: `Brain retrieval error: ${err.message}` };
  }
};

module.exports._internal = { buildContext, clientSpawn, relevantMetrics, monthsBefore };
