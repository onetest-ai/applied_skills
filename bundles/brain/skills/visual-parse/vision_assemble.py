#!/usr/bin/env python3
"""Assemble ONE document's parsed Markdown from rendered pages + VLM transcriptions.

For each page in order:
  • text page  → its PyMuPDF text-layer sidecar (p<NN>.txt)  — no torch, no docling
  • visual page → the vision model's faithful Markdown (from --results or the
                  page_render cache), keyed by the page image's sha
Every page becomes one section `## p<NN> · <title>` carrying an image marker
`<!-- image: <assets-rel>/p<NN>.png -->`, so downstream: a slide = a section =
a chunk = a note that shows its own image and can be re-sent to a vision agent.
Internal `#`/`##` in the VLM markdown are demoted so a page stays one section.

Usage:
  vision_assemble.py --render-dir <assets>/<slug> --out <parsed>/<doc>.md
                     [--results <vlm results dir>] [--db K.sqlite]  # cache source(s)
                     [--assets-rel <slug>]   # prefix used in the image marker
"""
import argparse, glob, json, os, re, sqlite3

def load_results(results_dir):
    md = {}
    for rf in sorted(glob.glob(os.path.join(results_dir or "", "result_*.json"))):
        try:
            md.update(json.load(open(rf)))
        except Exception as e:
            print("skip", rf, e)
    return md   # {img_sha: markdown}

def load_cache(db):
    if not db or not os.path.exists(db):
        return {}
    c = sqlite3.connect(db)
    if not c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='page_render'").fetchone():
        return {}
    return {sha: md for sha, md in c.execute("SELECT img_sha, md FROM page_render")}

def persist_page_render(db, pages_doc, results):
    """Persist fresh VLM transcriptions into the page_render cache, keyed by img_sha,
    so unchanged pages (same sha) are never re-transcribed on the next build/update."""
    if not (db and results and os.path.exists(db)):
        return
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS page_render(img_sha TEXT PRIMARY KEY, doc TEXT, page INT, md TEXT)")
    by_sha = {p["img_sha"]: p["page"] for p in pages_doc["pages"]}
    for sha, md in results.items():
        c.execute("INSERT OR REPLACE INTO page_render VALUES(?,?,?,?)",
                  (sha, pages_doc.get("doc", ""), by_sha.get(sha), md))
    c.commit(); c.close()

def demote(md):
    """Keep the page as a single top-level section: push VLM '#'/'##' to '###'."""
    out = []
    for ln in md.splitlines():
        m = re.match(r"^(#{1,2})\s+(.*)$", ln)
        out.append(f"###{'' if len(m.group(1)) == 2 else '#'} {m.group(2)}" if m else ln)
    return "\n".join(out)

def title_of(md, page):
    for ln in md.splitlines():
        s = re.sub(r"^#+\s*", "", ln).strip()
        if len(s) > 3:
            return s[:80]
    return f"Page {page}"

def group_pages(rows):
    """Consecutive rows sharing a `segment` are tiles of one logical unit.

    A row without `segment` — everything render_pages.py produces — is its own
    group, so existing documents assemble exactly as before.
    """
    groups = []
    for r in rows:
        seg = r.get("segment")
        if seg is not None and groups and groups[-1][0].get("segment") == seg:
            groups[-1].append(r)
        else:
            groups.append([r])
    return groups

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render-dir", required=True, help="<assets>/<slug> (has pages.json, p*.png, p*.txt)")
    ap.add_argument("--out", required=True, help="output parsed .md path")
    ap.add_argument("--results"); ap.add_argument("--db")
    ap.add_argument("--assets-rel", help="prefix for the image marker (default: the render-dir basename)")
    a = ap.parse_args()
    pages = json.load(open(os.path.join(a.render_dir, "pages.json")))
    assets_rel = a.assets_rel or pages.get("slug") or os.path.basename(a.render_dir.rstrip("/"))
    results = load_results(a.results)
    vlm = {**load_cache(a.db), **results}                   # results override cache
    persist_page_render(a.db, pages, results)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)

    def body_and_title(p):
        """One row's body and title. Returns (body, title_or_None, missing_delta).

        title is None when this row has no real title of its own, so a later tile
        of the same segment can supply one — a blank lead-in tile must not pin the
        whole section to "Page N".
        """
        n = p["page"]
        tp = os.path.join(a.render_dir, f"p{n:02d}.txt")
        txt = open(tp).read().strip() if os.path.exists(tp) else ""
        if p["flagged"]:
            md = vlm.get(p["img_sha"])
            if md is None:
                return f"_[visual page — awaiting VLM transcription]_\n\n{txt}", None, 1
            return demote(md.strip()), title_of(md, n), 0
        return txt, (title_of(txt, n) if txt else None), 0

    out, missing = [], 0
    for group in group_pages(pages["pages"]):
        tiles = sorted(group, key=lambda r: r.get("tile", 1))
        n = tiles[0]["page"]
        img_marker = f"<!-- image: {assets_rel}/p{n:02d}.png -->"
        bodies, title, shown_txt = [], None, False
        for t in tiles:
            b, ti, miss = body_and_title(t)
            if miss and shown_txt:
                b = "_[visual page — awaiting VLM transcription]_"
            elif miss:
                shown_txt = True
            missing += miss
            bodies.append(b)
            if title is None:
                title = ti
        title = title or f"Page {n}"
        out.append(f"## p{n:02d} · {title}\n{img_marker}\n\n" + "\n\n".join(bodies) + "\n")

    open(a.out, "w").write("\n".join(out))
    nflag = sum(p["flagged"] for p in pages["pages"])
    print(f"assembled {len(pages['pages'])} pages ({nflag} visual) -> {a.out}"
          + (f"  [{missing} visual pages still need VLM transcription]" if missing else ""))

if __name__ == "__main__":
    main()
