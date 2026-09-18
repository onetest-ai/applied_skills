// Returns one entry per LOGICAL segment of an HTML document.
// HTML is continuous: print pages cut at offsets a reader never saw, so the unit
// comes from the DOM. Explicit slide containers win; otherwise heading-led sections.
// Evaluated by the capture provider (Playwright MCP / Claude in Chrome).
(() => {
  const SLIDE = '.slide, [data-slide], .reveal .slides > section, section.slide';
  const pick = () => {
    const slides = [...document.querySelectorAll(SLIDE)];
    if (slides.length > 1) return slides;
    const tops = [...document.querySelectorAll('h1, h2, hr')];
    if (tops.length) return tops.map(h => h.closest('section, article, div') || h);
    return [document.body];
  };
  const tableOf = (t) => ({
    caption: (t.caption && t.caption.innerText.trim()) || '',
    rows: [...t.rows].map(r => [...r.cells].map(c => c.innerText.trim())),
  });
  const seen = new Set();
  const segments = [];
  for (const el of pick()) {
    if (seen.has(el)) continue;
    seen.add(el);
    const r = el.getBoundingClientRect();
    const head = el.querySelector('h1, h2, h3');
    segments.push({
      index: segments.length + 1,
      heading: (head && head.innerText.trim()) || '',
      text: el.innerText.trim(),
      bbox: {x: r.x + scrollX, y: r.y + scrollY, width: r.width, height: r.height},
      height: r.height,
      tables: [...el.querySelectorAll('table')].map(tableOf),
    });
  }
  return {source: location.href, title: document.title,
          viewport: {width: innerWidth, height: innerHeight}, segments};
})()
