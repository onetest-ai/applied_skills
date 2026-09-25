#!/usr/bin/env python3
"""Prepare visual-page transcription for SONNET VISION agents (map stage).

Collects the FLAGGED, UNCACHED pages across one or more rendered docs and writes
batches (image path + any deterministically-extracted table + page context) plus
instructions. Vision subagents read each page image and emit faithful structured
Markdown; vision_assemble.py merges the results back in.

Cache: a page whose rendered image sha is already in the store's `page_render`
table, transcribed under the current PROMPT_VERSION for its medium, is skipped (never
re-transcribed) — this is what makes updates cheap. A prompt change bumps its medium's
version so cached pages of that medium are transcribed again.

Reads <render-dir>/pages.json (+ p*.tables.md). Writes:
  <out>/instructions.md
  <out>/batch_<k>.json   [{img_sha, image, page, n_tables, tables_md, hint, medium}]
  (`medium` is the render dir's pages.json medium — "video" for meeting-recording frames,
   else "document"; the no-content gate in instructions.md applies per item, by medium)

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
    "This section applies ONLY to items whose `medium` is `video` (key frames from a meeting "
    "recording). For such an item, if the frame shows ONLY people, a speaker grid, a webcam "
    "view, a blank screen or a transition, output exactly `<!-- no-content -->` for it and "
    "nothing else. Otherwise transcribe only what is on screen, never what might have been "
    "said, and follow these rules:\n"
    "- Copy identifiers, codes, IDs, hashes, file names, URLs, numbers and names shown in the shared content "
    "character for character. Never normalise, complete or \"correct\" them: two similar "
    "strings on screen are two different strings, even when one looks like a typo of the other.\n"
    "- If text is too small or blurred to read with certainty, write `[illegible]` in its place. "
    "Never guess a character, a name or a number.\n"
    "- Ignore the meeting application itself: participant tiles and names, the participants "
    "panel, the call toolbar and its buttons, the meeting timer, \"is presenting\" banners, the "
    "OS taskbar, and live-caption or subtitle overlays (burned-in captions repeat what was said; "
    "the transcript already has it). Transcribe only the shared content. Do not mention, "
    "describe or quote anything you ignore — no notes about the meeting window, and never the "
    "caption text itself.\n"
    "- Describe each frame on its own, as if it were the only one. Never refer to other frames "
    "(no \"same as previous\", \"continued\", \"as before\").\n"
    "- Start each transcription with the line `<!-- frame: pNN -->`, where NN is the item's `page` "
    "as two digits (e.g. `<!-- frame: p07 -->`), so each answer stays tied to its image.\n"
    "- Title: the slide title; for an application screen use `<app> — <window or document title>`.\n"
    "\nNever answer `<!-- no-content -->` for an item whose `medium` is `document` (a slide or "
    "page): transcribe it as above, even when it shows only photos of people.\n"
)

# Bump a medium's version whenever its prompt changes: page_render rows written under an
# older version are stale for that medium and get re-transcribed. Rows written before
# versioning existed (prompt_v NULL) count as version 1.
PROMPT_VERSION = {"document": 1, "video": 2}


def cached_versions(db):
    """{img_sha: prompt version it was transcribed under} from the page_render cache."""
    if not db or not os.path.exists(db):
        return {}
    c = sqlite3.connect(db)
    if not c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='page_render'").fetchone():
        return {}
    cols = {r[1] for r in c.execute("PRAGMA table_info(page_render)")}
    v = "COALESCE(prompt_v, 1)" if "prompt_v" in cols else "1"
    return {sha: ver for sha, ver in c.execute(f"SELECT img_sha, {v} FROM page_render")}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render-dir", action="append", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--db")
    ap.add_argument("--batches", type=int, default=4)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    done = cached_versions(a.db)

    items, has_video, skipped = [], False, 0
    for rd in a.render_dir:
        pages = json.load(open(os.path.join(rd, "pages.json")))
        has_video = has_video or pages.get("medium") == "video"
        medium = pages.get("medium", "document")
        assets_root = os.path.dirname(rd.rstrip("/"))
        for p in pages["pages"]:
            # duplicates are re-decided on every assemble, so they still need a transcription
            gone = p.get("dropped") and p.get("dropped") != "duplicate"
            if not p["flagged"] or gone:
                continue
            if done.get(p["img_sha"]) == PROMPT_VERSION[medium]:
                skipped += 1
                continue
            tb = os.path.join(rd, f"p{p['page']:02d}.tables.md")
            items.append({"img_sha": p["img_sha"],
                          "image": os.path.join(assets_root, p["image"]),
                          "page": p["page"], "n_tables": p.get("n_tables", 0),
                          "tables_md": open(tb).read() if os.path.exists(tb) else "",
                          "hint": f"{pages.get('doc','')} p{p['page']}",
                          "medium": medium})

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
          f"({skipped} already cached, skipped)")

if __name__ == "__main__":
    main()
