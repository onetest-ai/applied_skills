#!/usr/bin/env python3
"""Prepare visual-page transcription for LOW-TIER VISION agents (map stage).

Collects the FLAGGED, UNCACHED pages across one or more rendered docs and writes
batches (image path + any deterministically-extracted table + page context) plus
instructions. Vision subagents read each page image and emit faithful structured
Markdown; vision_assemble.py merges the results back in.

Cache: a page whose rendered image sha is already in the store's `page_render`
table is skipped (never re-transcribed) — this is what makes updates cheap.

Reads <render-dir>/pages.json (+ p*.tables.md). Writes:
  <out>/instructions.md
  <out>/batch_<k>.json   [{img_sha, image, page, n_tables, tables_md, hint}]

Usage:
  vision_prep.py --render-dir <assets>/<slug> [--render-dir ...] --out <dir>
                 [--db K.sqlite] [--batches 4]
"""
import argparse, glob, json, os, sqlite3

INSTRUCTIONS = (
    "# Transcribe each slide/page image to FAITHFUL structured Markdown\n\n"
    "For every page below, open its `image` and transcribe what is actually there — "
    "preserve STRUCTURE, do not summarize away detail and do not invent:\n"
    "- a process/flow → an ORDERED list of stages with their sub-items, and the sequence (X → Y)\n"
    "- a timeline/Gantt → a TABLE (rows = activities, columns = the periods/dates shown)\n"
    "- a diagram → spell out the relationships (A contains B; C precedes D)\n"
    "- if the page has an extracted table (`tables_md` provided), TRUST those cells for numbers; "
    "your job is the surrounding meaning/labels, not re-reading the grid\n"
    "- start with a `# <slide title>` line.\n\n"
    "Output ONE JSON file `result_<k>.json` mapping each page's `img_sha` -> its Markdown string.\n"
)

VIDEO_GATE = (
    "\n## Meeting-recording frames (`medium: video`)\n"
    "These pages are key frames from a meeting recording. If a frame shows ONLY people, a "
    "speaker grid, a webcam view, a blank screen or a transition, output exactly "
    "`<!-- no-content -->` for it and nothing else. Otherwise transcribe only what is on "
    "screen, never what might have been said.\n"
)

def cached_shas(db):
    if not db or not os.path.exists(db):
        return set()
    c = sqlite3.connect(db)
    if not c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='page_render'").fetchone():
        return set()
    return {r[0] for r in c.execute("SELECT img_sha FROM page_render")}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render-dir", action="append", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--db")
    ap.add_argument("--batches", type=int, default=4)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    done = cached_shas(a.db)

    items, has_video = [], False
    for rd in a.render_dir:
        pages = json.load(open(os.path.join(rd, "pages.json")))
        has_video = has_video or pages.get("medium") == "video"
        assets_root = os.path.dirname(rd.rstrip("/"))
        for p in pages["pages"]:
            if not p["flagged"] or p.get("dropped") or p["img_sha"] in done:
                continue
            tb = os.path.join(rd, f"p{p['page']:02d}.tables.md")
            items.append({"img_sha": p["img_sha"],
                          "image": os.path.join(assets_root, p["image"]),
                          "page": p["page"], "n_tables": p.get("n_tables", 0),
                          "tables_md": open(tb).read() if os.path.exists(tb) else "",
                          "hint": f"{pages.get('doc','')} p{p['page']}"})

    open(os.path.join(a.out, "instructions.md"), "w").write(INSTRUCTIONS + (VIDEO_GATE if has_video else ""))
    n = max(1, a.batches)
    if not items:
        print(f"no flagged/uncached pages -> {a.out}"); return
    size = (len(items) + n - 1) // n
    made = 0
    for k in range(n):
        b = items[k*size:(k+1)*size]
        if not b: break
        json.dump(b, open(os.path.join(a.out, f"batch_{k}.json"), "w"), indent=1); made += 1
    print(f"prepared {len(items)} visual pages into {made} batch(es) -> {a.out} "
          f"({len(done)} already cached, skipped)")

if __name__ == "__main__":
    main()
