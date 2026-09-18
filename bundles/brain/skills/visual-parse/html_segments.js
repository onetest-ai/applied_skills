// Returns one entry per LOGICAL segment of an HTML document.
// HTML is continuous: print pages cut at offsets a reader never saw, so the unit
// comes from the DOM. Explicit slide containers win; otherwise heading-led sections.
// Evaluated by the capture provider (Playwright MCP / Claude in Chrome).
(() => {
  const SLIDE = '.slide, [data-slide], .reveal .slides > section, section.slide';
  const BOUNDARY = 'h1, h2, hr';

  // A heading-led section is the boundary PLUS everything up to the next boundary.
  // Prefer a wrapping section/article when each boundary has its own; otherwise walk
  // siblings, because a flat document has no container to close() onto and the
  // heading alone would drop the body text beneath it.
  // Note: this is a heuristic over markup the script has never seen, and it is not
  // unit-tested here because there is no JS runner and segmentation needs a live DOM.
  const runFrom = (b) => {
    const els = [b];
    for (let n = b.nextElementSibling; n && !n.matches(BOUNDARY); n = n.nextElementSibling) {
      els.push(n);
    }
    return els;
  };
  const groupsFor = (bounds) => {
    const wrapped = bounds.map(b => b.closest('section, article'));
    const distinct = new Set(wrapped.filter(Boolean));
    const eachHasItsOwn = wrapped.every(Boolean) && distinct.size === bounds.length;
    return eachHasItsOwn ? wrapped.map(w => [w]) : bounds.map(runFrom);
  };

  const pick = () => {
    const slides = [...document.querySelectorAll(SLIDE)];
    if (slides.length > 1) return slides;
    const tops = [...document.querySelectorAll(BOUNDARY)];
    if (tops.length) return groupsFor(tops);
    return [[document.body]];
  };

  const tableOf = (t) => ({
    caption: (t.caption && t.caption.innerText.trim()) || '',
    rows: [...t.rows].map(r => [...r.cells].map(c => c.innerText.trim())),
  });

  const bboxUnion = (els) => {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const el of els) {
      const r = el.getBoundingClientRect();
      minX = Math.min(minX, r.x);
      minY = Math.min(minY, r.y);
      maxX = Math.max(maxX, r.x + r.width);
      maxY = Math.max(maxY, r.y + r.height);
    }
    return {
      x: minX + scrollX,
      y: minY + scrollY,
      width: maxX - minX,
      height: maxY - minY
    };
  };

  const seen = new Set();
  const segments = [];
  for (const els of pick()) {
    // Skip duplicate slide containers
    if (seen.has(els[0])) continue;
    seen.add(els[0]);

    // Handle both single-element slides and multi-element groups
    const group = Array.isArray(els) ? els : [els];

    const bbox = bboxUnion(group);
    const head = group[0].querySelector('h1, h2, h3');
    const text = group.map(el => el.innerText.trim()).join('\n').trim();
    const tables = [];
    for (const el of group) {
      tables.push(...[...el.querySelectorAll('table')].map(tableOf));
    }

    segments.push({
      index: segments.length + 1,
      heading: (head && head.innerText.trim()) || '',
      text: text,
      bbox: bbox,
      height: bbox.height,
      tables: tables,
    });
  }
  return {source: location.href, title: document.title,
          viewport: {width: innerWidth, height: innerHeight}, segments};
})()
