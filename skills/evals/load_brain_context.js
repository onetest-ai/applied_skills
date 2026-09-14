/**
 * Generic dynamic context fetcher for promptfoo vars.
 * Fetches from any brain REST shim at BRAIN_URL (default: http://localhost:8002).
 * Two complementary queries per question to improve recall.
 * promptfoo calls this with (varName, prompt, otherVars) and expects { output: string }.
 */
module.exports = async function (varName, prompt, otherVars) {
  const question = String(otherVars.question || '').trim();
  if (!question) return { error: 'question must be resolved before brain context' };

  const brainUrl = (otherVars.BRAIN_URL || process.env.BRAIN_URL || 'http://localhost:8002')
    .replace(/\/$/, '');

  async function fetchChunks(query, limit) {
    const res = await fetch(`${brainUrl}/api/v1/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, limit }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const text = (data[0] && data[0].text) ? data[0].text : '';
    return text.split('\n\n---\n\n').filter(Boolean);
  }

  function specificsQuery(q) {
    const lower = q.toLowerCase();
    if (lower.includes('action item'))   return q + ' owners decisions commitments requests';
    if (lower.includes('knowledge gap')) return q + ' unknown unresolved missing information';
    if (lower.includes('quality risk'))  return q + ' defect error failure blocker';
    if (lower.includes('test strategy')) return q + ' approach tooling scripts plan baseline';
    if (lower.includes('integration'))   return q + ' system dependency API connection';
    if (lower.includes('transition'))    return q + ' handover migration cutover dependency';
    if (lower.includes('process'))       return q + ' workflow pipeline intake steps';
    return q + ' specific details findings decisions';
  }

  try {
    const results = await Promise.all([
      fetchChunks(question, 50),
      fetchChunks(specificsQuery(question), 30),
    ]);
    const [primary, secondary] = results;

    const seen = new Set();
    const merged = [];
    for (const chunk of [...primary, ...secondary]) {
      const key = chunk.split('\n')[0].trim();
      if (!seen.has(key)) { seen.add(key); merged.push(chunk); }
    }
    return { output: merged.join('\n\n---\n\n') || 'No context retrieved from brain.' };
  } catch (err) {
    return { error: `Brain retrieval error: ${err.message}` };
  }
};
