#!/usr/bin/env python3
"""Emit a navigable Obsidian vault FROM the knowledge SQLite.

The vault is a *view of the store*: one note per `chunks` row (= a retrieval chunk
= a section a human reads), tagged with its REAL per-section taxonomy categories
from `chunk_topics`, and linked to taxonomy topic notes that correspond 1:1 to the
graph vertices (`graph_nodes`).

**Folder layout (browsable, not a flat dump):** the `source` string encodes the
original path with `__` between folders, so we rebuild that hierarchy — parent
folders, then one folder per document, and the section notes inside it:

  <parent>/<...>/<document>/
      <document>.md          ← the doc index (MOC): topics + section list
      01 <section>.md        ← one note per section (a retrieval chunk)
      02 <section>.md
  _topics/
      <L1>.md                ← taxonomy vertex (graph_nodes) + its L2 children
      <L2>.md

Links are **path-qualified** (`[[folder/note|alias]]`) so they resolve regardless
of nesting and the note filenames can stay short.

Usage: to_obsidian.py --db knowledge.sqlite --out <vault>
"""
import argparse, os, re, shutil, sqlite3

def kebab(s): return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x"

def slug(s):
    """Filesystem/Obsidian-safe single path segment."""
    s = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", str(s))
    return re.sub(r"\s+", " ", s).strip()[:80] or "note"

def uniq(path, seen):
    cand, n = path, 2
    while cand.lower() in seen:
        cand = f"{path} {n}"; n += 1
    seen.add(cand.lower()); return cand

def fm(tags, **kv):
    return "---\ntags:\n" + "".join(f"  - {t}\n" for t in tags) + \
           "".join(f'{k}: "{v}"\n' for k, v in kv.items()) + "---\n\n"

def write(out, relpath, text):
    """relpath is a vault-relative path WITHOUT .md; returns the same relpath."""
    full = os.path.join(out, *relpath.split("/")) + ".md"
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f: f.write(text)
    return relpath

def link(relpath, alias=None):
    return f"[[{relpath}|{alias}]]" if alias else f"[[{relpath}]]"

def doc_relpath(src):
    """source 'A__B__file.pptx' -> vault path 'A/B/file' (parent folders + doc)."""
    rel = src[:-3] if src.endswith(".md") else src
    parts = [p for p in rel.split("__") if p.strip()] or [rel]
    parts = [slug(re.sub(r"[_]+", " ", p)) for p in parts]
    parts[-1] = slug(re.sub(r"\.(pdf|pptx|ppt|docx|doc|xlsx|xlsm|xls|csv)$", "", parts[-1], flags=re.I)) or "doc"
    return parts  # list of path segments; last is the doc name

def note_path(source, ordv, title):
    """The vault-relative path (no .md) where a chunk's note lives — the SAME naming
    to_obsidian writes. Lets tools show WHERE a hit is in the vault for transparency,
    even when the vault hasn't been exported (regenerable with `./brain vault`)."""
    return "/".join(doc_relpath(source)) + "/" + slug(f"{ordv+1:02d} {(title or 'Section')[:60]}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--clean", action="store_true",
                    help="wipe the vault dir first (reconcile: drop notes for docs no longer in the store)")
    ap.add_argument("--assets", help="assets root holding page images (chunks.image is relative to it); "
                                     "referenced images are copied into <vault>/_assets and embedded in notes")
    a = ap.parse_args()
    # the vault is a pure VIEW of the store, so a clean rebuild is the safe way to
    # reconcile deletions — otherwise notes for removed docs would linger as orphans.
    if a.clean and os.path.isdir(a.out):
        shutil.rmtree(a.out)
    os.makedirs(a.out, exist_ok=True)
    c = sqlite3.connect(a.db)
    has_topics = bool(c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_topics'").fetchone())
    has_graph = bool(c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='graph_nodes'").fetchone())
    notes = 0

    # taxonomy topic notes = graph vertices, under _topics/
    topic_rel = {}   # label -> vault relpath
    if has_graph:
        label_by_id = {nid_: lbl for nid_, lbl, _ in c.execute("SELECT id,label,kind FROM graph_nodes")}
        for _, lbl in c.execute("SELECT id,label FROM graph_nodes"):
            topic_rel[lbl] = f"_topics/{slug(lbl)}"
        for nid_, lbl in c.execute("SELECT id,label FROM graph_nodes WHERE kind='intent_l1'"):
            kids = [label_by_id.get(s, s) for (s,) in
                    c.execute("SELECT source FROM graph_edges WHERE rel='subclass_of' AND target=?", (nid_,))]
            body = fm(["taxonomy/l1"]) + f"# {lbl}\n\n## Subcategories\n\n" + \
                   ("".join(f"- {link(topic_rel.get(k, '_topics/'+slug(k)), k)}\n" for k in kids) or "_none_\n") + \
                   "\n> Backlinks below = source notes tagged with this topic.\n"
            write(a.out, f"_topics/{slug(lbl)}", body); notes += 1
            for k in kids:
                write(a.out, f"_topics/{slug(k)}",
                      fm(["taxonomy/l2"]) + f"# {k}\n\nParent: {link('_topics/'+slug(lbl), lbl)}\n"); notes += 1

    # per-chunk topics (real per-section categories)
    topics = {}
    if has_topics:
        for cid, lbl in c.execute("SELECT chunk_id, category_label FROM chunk_topics"):
            topics.setdefault(cid, []).append(lbl)

    # native semantic 'related' edges (chunk↔chunk cosine kNN), both directions
    has_rel = bool(c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='related'").fetchone())
    related = {}
    if has_rel:
        for a1, b1, s in c.execute("SELECT chunk_id, related_id, score FROM related"):
            related.setdefault(a1, []).append((b1, s)); related.setdefault(b1, []).append((a1, s))
        for cid in related:
            related[cid] = sorted(related[cid], key=lambda x: -x[1])[:6]

    # notes = chunks, grouped by source doc, into <parents>/<doc>/
    has_image = "image" in {r[1] for r in c.execute("PRAGMA table_info(chunks)")}
    sel = "SELECT id,source,ord,title,text," + ("image" if has_image else "NULL") + " FROM chunks ORDER BY source,ord"
    docs = {}
    for cid, src, ordv, title, text, image in c.execute(sel):
        docs.setdefault(src, []).append((cid, ordv, title, text, image))

    def embed_image(image):
        """Copy a referenced page image into <vault>/_assets and return an embed line."""
        if not image or not a.assets:
            return ""
        srcpath = image if os.path.isabs(image) else os.path.join(a.assets, image)
        if not os.path.exists(srcpath):
            return ""
        dest_rel = "_assets/" + image.replace("\\", "/").lstrip("/")
        dest = os.path.join(a.out, *dest_rel.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if not os.path.exists(dest):
            shutil.copy2(srcpath, dest)
        return f"![[{dest_rel}]]\n\n"

    # PASS 1 — assign each doc its folder and each chunk its note relpath + title,
    # so PASS 2 can link related sections ACROSS documents by their real paths.
    seen_dirs = set(); doc_meta = {}; note_rel = {}; note_title = {}
    for src, rows in docs.items():
        segs = doc_relpath(src)
        doc_dir = uniq("/".join(segs), seen_dirs)
        doc_meta[src] = (segs, doc_dir)
        for cid, ordv, title, text, image in rows:
            st = (title or "Section")
            note_rel[cid] = f"{doc_dir}/{slug(f'{ordv+1:02d} {st[:60]}')}"
            note_title[cid] = st

    # PASS 2 — write
    for src, rows in docs.items():
        segs, doc_dir = doc_meta[src]
        doc_name = segs[-1]; fam = kebab(segs[0])
        moc_rel = f"{doc_dir}/{doc_name}"
        doc_topics, sec_links = [], []
        for cid, ordv, title, text, image in rows:
            cats = topics.get(cid, [])
            for c2 in cats:
                if c2 not in doc_topics: doc_topics.append(c2)
            tags = [f"source/{fam}"] + [f"intent/{kebab(c2)}" for c2 in cats]
            tl = " ".join(link(topic_rel.get(c2, "_topics/"+slug(c2)), c2) for c2 in cats)
            st = (title or "Section")
            rel_block = ""
            rlist = [(rid, sc) for rid, sc in related.get(cid, []) if rid in note_rel]
            if rlist:
                rel_block = "\n\n## Related sections\n" + "".join(
                    f"- {link(note_rel[rid], note_title[rid])}  ·{sc:.2f}\n" for rid, sc in rlist)
            note = (fm(tags, doc=link(moc_rel, doc_name), section=st.replace('"', "'")) +
                    f"# {st}\n\n{embed_image(image)}{text}{rel_block}\n\n---\n↩ {link(moc_rel, doc_name)}" +
                    (f" · topics: {tl}" if tl else "") + "\n")
            sec_rel = write(a.out, note_rel[cid], note)
            sec_links.append((sec_rel, st)); notes += 1
        moc_tags = [f"source/{fam}"] + [f"intent/{kebab(t)}" for t in doc_topics]
        moc = fm(moc_tags, source_file=src) + f"# {' / '.join(segs)}\n\n" + \
              (("**Topics:** " + " ".join(link(topic_rel.get(t, "_topics/"+slug(t)), t) for t in doc_topics) + "\n\n") if doc_topics else "") + \
              f"**Sections ({len(sec_links)}):**\n\n" + "".join(f"- {link(r, st)}\n" for r, st in sec_links)
        write(a.out, moc_rel, moc); notes += 1

    print(f"wrote {notes} notes -> {a.out} ({len(docs)} docs in a folder tree; "
          f"{'per-section tags' if has_topics else 'NO chunk_topics'}; "
          f"{'+related links' if has_rel else 'no related layer'})")
    c.close()

if __name__ == "__main__":
    main()
