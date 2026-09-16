/**
 * Generic dynamic context fetcher for promptfoo vars.
 * Fetches from any brain REST shim at BRAIN_URL (default: http://localhost:8003).
 * Two complementary queries per question to improve recall.
 * promptfoo calls this with (varName, prompt, otherVars) and expects { output: string }.
 *
 * otherVars recognised:
 *   BRAIN_URL     — override brain endpoint (env var fallback: BRAIN_URL)
 *   BRAIN_API_KEY — optional API key forwarded as X-API-Key header (matches BRAIN_API_KEY env var)
 *   tag           — tagBoost: RRF bonus for chunks matching tag (does not exclude untagged)
 *   query_suffix  — corpus-specific terms appended to the secondary query
 *                   (default: 'specific details findings decisions evidence')
 */
module.exports = async function (varName, prompt, otherVars) {
  const question = String(otherVars.question || '').trim();
  if (!question) return { error: 'question must be resolved before brain context' };

  const brainUrl = (otherVars.BRAIN_URL || process.env.BRAIN_URL || 'http://localhost:8003')
    .replace(/\/$/, '');

  const tag = otherVars.tag || undefined;
  const querySuffix = String(otherVars.query_suffix || 'specific details findings decisions evidence');

  const headers = { 'Content-Type': 'application/json' };
  const apiKey = otherVars.BRAIN_API_KEY || process.env.BRAIN_API_KEY;
  if (apiKey) headers['X-API-Key'] = apiKey;

  async function fetchChunks(query, limit) {
    const body = { query, limit };
    if (tag) body.tagBoost = tag;
    const res = await fetch(`${brainUrl}/api/v1/search`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data[0] && !data[0].text) console.warn(`load_brain_context: unexpected response shape`, Object.keys(data[0]));
    const text = (data[0] && data[0].text) ? data[0].text : '';
    return text.split('\n\n---\n\n').filter(Boolean);
  }

  try {
    const results = await Promise.all([
      fetchChunks(question, 50),                                 // primary: broad semantic match
      fetchChunks(`${question} ${querySuffix}`, 30),             // secondary: steer toward concrete facts
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
