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

  // Content before the first boundary belongs to no boundary's run, but it is
  // usually the document's lede — the paragraph a reader would cite. Emit it as
  // its own leading segment when it carries text.
  const leadInFor = (first) => {
    const els = [];
    for (let n = first.parentElement && first.parentElement.firstElementChild;
         n && n !== first; n = n.nextElementSibling) {
      els.push(n);
    }
    return els.some(e => (e.innerText || '').trim()) ? els : [];
  };

  const groupsFor = (bounds) => {
    const wrapped = bounds.map(b => b.closest('section, article'));
    const distinct = new Set(wrapped.filter(Boolean));
    const eachHasItsOwn = wrapped.every(Boolean) && distinct.size === bounds.length;
    if (eachHasItsOwn) {
      return wrapped.map(w => [w]);
    } else {
      // Sibling-walk path: include lead-in content if it has text
      const groups = bounds.map(runFrom);
      const leadIn = leadInFor(bounds[0]);
      return leadIn.length ? [leadIn, ...groups] : groups;
    }
  };

  const pick = () => {
    const slides = [...document.querySelectorAll(SLIDE)];
    if (slides.length > 1) return slides.map(s => [s]);
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

  const headingOf = (group) => {
    for (const el of group) {
      if (el.matches && el.matches('h1, h2, h3')) return el.innerText.trim();
      const q = el.querySelector && el.querySelector('h1, h2, h3');
      if (q) return q.innerText.trim();
    }
    return '';
  };

  const seen = new Set();
  const segments = [];
  for (const els of pick()) {
    if (seen.has(els[0])) continue;
    seen.add(els[0]);

    const bbox = bboxUnion(els);
    const heading = headingOf(els);
    const text = els.map(el => el.innerText.trim()).join('\n').trim();
    const tables = [];
    for (const el of els) {
      tables.push(...[...el.querySelectorAll('table')].map(tableOf));
    }

    segments.push({
      index: segments.length + 1,
      heading: heading,
      text: text,
      bbox: bbox,
      height: bbox.height,
      tables: tables,
    });
  }
  return {source: location.href, title: document.title,
          viewport: {width: innerWidth, height: innerHeight}, segments};
})()
