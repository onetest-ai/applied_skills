#!/usr/bin/env python3
"""Emit a *navigable* Obsidian vault from parsed Markdown + taxonomy.

Not a wall of text: each document is split into meaningful section notes,
linked into a per-doc index (MOC) and into taxonomy topic notes — so Obsidian's
graph, backlinks, and tags actually mean something.

Vault layout (flat, unique names so [[wikilinks]] resolve cleanly):
  <doc>.md                 — doc index (MOC): links every section + its topics
  <doc> · NN <section>.md  — one note per section (heading-aware chunk)
  topic · <L1>.md          — taxonomy L1 note (lists L2 children; backlinks show docs)
  topic · <L2>.md          — taxonomy L2 stub (so links resolve)

Frontmatter tags: source/<family>, intent/<L1>. Body cross-links via [[ ]].

Usage:
  to_obsidian.py --parsed <dir> --out <vault> [--map-dir taxonomy/map]
                 [--taxonomy taxonomy_v0.json] [--top 3] [--max-chars 2200]
"""
import argparse, glob, json, os, re

def kebab(s): return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x"
def slug(s):  return re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", str(s))).strip()[:110] or "note"

def short_id(rel, seen):
    """Short, readable, UNIQUE note id from the last path segment of a parsed name."""
    seg = rel.split("__")[-1]
    seg = re.sub(r"\.(pdf|pptx|docx|xlsx|xlsm)$", "", seg, flags=re.I)
    seg = slug(re.sub(r"[_]+", " ", seg))[:48].strip() or "doc"
    cand, n = seg, 2
    while cand in seen: cand = f"{seg} {n}"; n += 1
    seen.add(cand); return cand

def family(rel):                      # generic: top path segment (parser joins with "__")
    seg = rel.split("__", 1)[0] if "__" in rel else rel.split("/", 1)[0]
    return kebab(seg) or "other"

def strip_preamble(md):
    return re.sub(r"\A# SOURCE:.*\n(# method:.*\n)?\n?", "", md)

def sections(md, max_chars):
    """Split at markdown headings / '[page N]' markers into (title, body); split
    oversized sections by paragraphs; drop empties. Falls back to paragraph-merge
    when a doc has no headings."""
    heading = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
    blocks, title, buf = [], None, []
    for ln in md.splitlines():
        m = heading.match(ln)
        if m:
            if title is not None or "\n".join(buf).strip():
                blocks.append((title, "\n".join(buf).strip()))
            title, buf = re.sub(r"\[page (\d+)\]", r"Page \1", m.group(1)).strip(), []
        else:
            buf.append(ln)
    if title is not None or "\n".join(buf).strip():
        blocks.append((title, "\n".join(buf).strip()))
    blocks = [(t, b) for t, b in blocks if b or t]
    if not blocks:
        blocks = [(None, md.strip())]
    # split oversized bodies by paragraph runs
    out = []
    for t, b in blocks:
        if len(b) <= max_chars:
            out.append((t, b)); continue
        paras = [p for p in re.split(r"\n\s*\n", b) if p.strip()]
        cur, part = "", 1
        for p in paras:
            if len(cur) + len(p) + 2 > max_chars and cur:
                out.append((f"{t} (part {part})" if t else f"Part {part}", cur.strip())); cur = p; part += 1
            else:
                cur = (cur + "\n\n" + p) if cur else p
        if cur.strip(): out.append((f"{t} (part {part})" if t and part > 1 else t, cur.strip()))
    return out

def derive_title(title, body):
    """A meaningful note title: the heading if it's real, else the first
    substantive line of the body (page markers / stopword headings are useless)."""
    t = (title or "").strip()
    if t and not re.match(r"(?i)^page \d+$", t) and len(re.sub(r"\W", "", t)) >= 3:
        return t[:70]
    for ln in body.splitlines():
        s = re.sub(r"\s+", " ", re.sub(r"[#>*_`|<>!\[\]()-]+", " ", ln)).strip()
        if len(re.sub(r"\W", "", s)) >= 6:
            return " ".join(s.split()[:10])[:70]
    return t or "Section"

def doc_l1s(map_json, top):
    if not map_json or not os.path.exists(map_json): return []
    from collections import Counter
    c = Counter()
    for ic in json.load(open(map_json)).get("intent_classes", []):
        base = ic["name"] if ic.get("level") == "L1" else (ic.get("parent") or ic.get("name"))
        if base: c[base] += 1
    return [k for k, _ in c.most_common(top)]

def fm(tags, **kv):
    lines = ["---", "tags:"] + [f"  - {t}" for t in tags]
    for k, v in kv.items(): lines.append(f'{k}: "{v}"')
    return "\n".join(lines) + "\n---\n\n"

def write(out, name, text):
    open(os.path.join(out, slug(name) + ".md"), "w").write(text)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsed", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--map-dir"); ap.add_argument("--taxonomy")
    ap.add_argument("--top", type=int, default=3); ap.add_argument("--max-chars", type=int, default=2200)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    notes = 0

    # taxonomy topic notes (L1 with L2 children) — links resolve + hierarchy navigable
    l2_of = {}
    if a.taxonomy and os.path.exists(a.taxonomy):
        tree = json.load(open(a.taxonomy)).get("intent_taxonomy", {}).get("tree", {})
        for l1, kids in tree.items():
            l2_of[l1] = kids
            body = fm(["taxonomy/l1"]) + f"# {l1}\n\n## Subcategories\n\n" + \
                   ("".join(f"- [[topic · {slug(l2)}]]\n" for l2 in kids) or "_none_\n") + \
                   "\n> Backlinks below show source notes tagged with this topic.\n"
            write(a.out, f"topic · {l1}", body); notes += 1
            for l2 in kids:
                write(a.out, f"topic · {l2}", fm(["taxonomy/l2"]) + f"# {l2}\n\nParent: [[topic · {slug(l1)}]]\n"); notes += 1

    seen_ids = set()
    for md in sorted(glob.glob(os.path.join(a.parsed, "*.md"))):
        base = os.path.basename(md)[:-3]
        did = short_id(base, seen_ids)                 # short, unique note id for this doc
        fam = family(base)
        title = base.replace("__", " / ")
        l1s = doc_l1s(os.path.join(a.map_dir, base + ".json") if a.map_dir else None, a.top)
        tags = [f"source/{fam}"] + [f"intent/{kebab(l)}" for l in l1s]
        topic_links = " ".join(f"[[topic · {slug(l)}]]" for l in l1s)

        secs = sections(strip_preamble(open(md).read()), a.max_chars)
        sec_names = []
        for i, (stitle, body) in enumerate(secs, 1):
            st = derive_title(stitle, body)
            nm = f"{did} · {i:02d} {st[:44]}"           # unique: did + index prefix
            sec_names.append((nm, st))
            note = fm(tags, doc=f"[[{did}]]", section=st.replace('"', "'")) + \
                   f"# {st}\n\n{body}\n\n---\n↩ [[{did}]]" + (f" · topics: {topic_links}" if topic_links else "") + "\n"
            write(a.out, nm, note); notes += 1

        moc = fm(tags, source_file=base + ".md") + f"# {title}\n\n" + \
              (f"**Topics:** {topic_links}\n\n" if topic_links else "") + \
              f"**Sections ({len(sec_names)}):**\n\n" + \
              "".join(f"- [[{slug(nm)}|{st}]]\n" for nm, st in sec_names)
        write(a.out, did, moc); notes += 1

    print(f"wrote {notes} notes -> {a.out}  (open as an Obsidian vault: section notes + doc MOCs + taxonomy topics, cross-linked)")

if __name__ == "__main__":
    main()
