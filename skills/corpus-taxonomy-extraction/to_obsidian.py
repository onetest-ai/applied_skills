#!/usr/bin/env python3
"""Emit a navigable Obsidian vault FROM the knowledge SQLite.

The vault is a *view of the store*: one note per `chunks` row (= a retrieval chunk
= a section a human reads), tagged with its REAL per-section taxonomy categories
from `chunk_topics`, and linked to taxonomy topic notes that correspond 1:1 to the
graph vertices (`graph_nodes`). So it is explicit how everything relates:

  section note ──[[doc MOC]]──> document
       │  tags: source/<family>, intent/<L1>
       └──[[topic · <L1>]]──> taxonomy vertex (graph_nodes) ──[[topic · <L2>]]──> children

Usage: to_obsidian.py --db knowledge.sqlite --out <vault>
"""
import argparse, os, re, sqlite3

def kebab(s): return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x"
def slug(s):  return re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", str(s))).strip()[:110] or "note"

def short_id(rel, seen):
    seg = re.sub(r"\.(pdf|pptx|docx|xlsx|xlsm)$", "", rel.split("__")[-1], flags=re.I)
    seg = slug(re.sub(r"[_]+", " ", seg))[:48].strip() or "doc"
    cand, n = seg, 2
    while cand in seen: cand = f"{seg} {n}"; n += 1
    seen.add(cand); return cand

def family(rel):
    return kebab(rel.split("__", 1)[0] if "__" in rel else rel.split("/", 1)[0]) or "other"

def fm(tags, **kv):
    return "---\ntags:\n" + "".join(f"  - {t}\n" for t in tags) + \
           "".join(f'{k}: "{v}"\n' for k, v in kv.items()) + "---\n\n"

def write(out, name, text): open(os.path.join(out, slug(name) + ".md"), "w").write(text)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    c = sqlite3.connect(a.db)
    has_topics = bool(c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_topics'").fetchone())
    has_graph = bool(c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='graph_nodes'").fetchone())
    notes = 0

    # taxonomy topic notes = graph vertices (L1 with its L2 children)
    label_by_id = {}
    if has_graph:
        for nid_, lbl, kind in c.execute("SELECT id,label,kind FROM graph_nodes"):
            label_by_id[nid_] = lbl
        for nid_, lbl in c.execute("SELECT id,label FROM graph_nodes WHERE kind='intent_l1'"):
            kids = [label_by_id.get(s, s) for (s,) in
                    c.execute("SELECT source FROM graph_edges WHERE rel='subclass_of' AND target=?", (nid_,))]
            body = fm(["taxonomy/l1"]) + f"# {lbl}\n\n## Subcategories\n\n" + \
                   ("".join(f"- [[topic · {slug(k)}]]\n" for k in kids) or "_none_\n") + \
                   "\n> Backlinks below = source notes tagged with this topic.\n"
            write(a.out, f"topic · {lbl}", body); notes += 1
            for k in kids:
                write(a.out, f"topic · {k}", fm(["taxonomy/l2"]) + f"# {k}\n\nParent: [[topic · {slug(lbl)}]]\n"); notes += 1

    # per-chunk topics (real per-section categories)
    topics = {}
    if has_topics:
        for cid, lbl in c.execute("SELECT chunk_id, category_label FROM chunk_topics"):
            topics.setdefault(cid, []).append(lbl)

    # notes = chunks, grouped by source doc
    seen = set(); doc_ids = {}
    docs = {}
    for cid, src, ordv, title, text in c.execute("SELECT id,source,ord,title,text FROM chunks ORDER BY source,ord"):
        docs.setdefault(src, []).append((cid, ordv, title, text))
    for src, rows in docs.items():
        rel = src[:-3] if src.endswith(".md") else src
        did = short_id(rel, seen); doc_ids[src] = did
        fam = family(rel)
        doc_topics = []
        sec_names = []
        for cid, ordv, title, text in rows:
            cats = topics.get(cid, [])
            for c2 in cats:
                if c2 not in doc_topics: doc_topics.append(c2)
            tags = [f"source/{fam}"] + [f"intent/{kebab(c2)}" for c2 in cats]
            tl = " ".join(f"[[topic · {slug(c2)}]]" for c2 in cats)
            nm = f"{did} · {ordv+1:02d} {(title or 'Section')[:44]}"
            sec_names.append((nm, title or "Section"))
            note = fm(tags, doc=f"[[{did}]]", section=(title or "Section").replace('"', "'")) + \
                   f"# {title or 'Section'}\n\n{text}\n\n---\n↩ [[{did}]]" + (f" · topics: {tl}" if tl else "") + "\n"
            write(a.out, nm, note); notes += 1
        moc_tags = [f"source/{fam}"] + [f"intent/{kebab(t)}" for t in doc_topics]
        moc = fm(moc_tags, source_file=src) + f"# {rel.replace('__',' / ')}\n\n" + \
              (f"**Topics:** " + " ".join(f"[[topic · {slug(t)}]]" for t in doc_topics) + "\n\n" if doc_topics else "") + \
              f"**Sections ({len(sec_names)}):**\n\n" + "".join(f"- [[{slug(nm)}|{st}]]\n" for nm, st in sec_names)
        write(a.out, did, moc); notes += 1

    print(f"wrote {notes} notes -> {a.out} (from SQLite: {len(docs)} docs, "
          f"{'per-section tags' if has_topics else 'NO chunk_topics — run classify first for real tags'})")
    c.close()

if __name__ == "__main__":
    main()
