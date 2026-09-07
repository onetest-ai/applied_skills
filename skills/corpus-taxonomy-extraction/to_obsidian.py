#!/usr/bin/env python3
"""Emit an Obsidian vault from parsed Markdown + taxonomy — the human layer.

Writes one note per source doc with YAML frontmatter tags:
  - source/<family>   (from the filename)
  - intent/<L1>       (from the doc's map JSON, if provided) — nested tags give
                      Obsidian a taxonomy hierarchy + graph clustering for free.
The vault is the human-readable canon; the same Markdown also feeds knowledge-index.

Usage:
  to_obsidian.py --parsed <dir> --out <vault> [--map-dir <taxonomy/map>] [--top 3]
"""
import argparse, glob, json, os, re

def kebab(s): return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x"

def family(name):
    if name.startswith("Bain Documentation__"): return "bain"
    if name.startswith("CEC Decks__"): return "cec-decks"
    if "Net Promoter Score (NPS)__" in name: return "voc"
    if name.startswith("EPAM Decks__"): return "epam"
    if name.startswith("COM&GOV O2C"): return "o2c"
    return "other"

def doc_l1s(map_json, top):
    if not map_json or not os.path.exists(map_json): return []
    j = json.load(open(map_json))
    from collections import Counter
    c = Counter()
    for ic in j.get("intent_classes", []):
        base = ic["name"] if ic.get("level") == "L1" else (ic.get("parent") or ic.get("name"))
        if base: c[base] += 1
    return [k for k, _ in c.most_common(top)]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsed", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--map-dir")
    ap.add_argument("--top", type=int, default=3)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    n = 0
    for md in sorted(glob.glob(os.path.join(a.parsed, "*.md"))):
        base = os.path.basename(md)
        body = open(md).read()
        # drop the parser's "# SOURCE:/# method:" preamble for a clean note
        body = re.sub(r"\A# SOURCE:.*\n(# method:.*\n)?\n?", "", body)
        tags = [f"source/{family(base)}"]
        mj = os.path.join(a.map_dir, base[:-3] + ".json") if a.map_dir else None
        tags += [f"intent/{kebab(l)}" for l in doc_l1s(mj, a.top)]
        title = base[:-3].replace("__", " / ")
        fm = "---\ntags:\n" + "".join(f"  - {t}\n" for t in tags) + f'source_file: "{base}"\n---\n\n# {title}\n\n'
        open(os.path.join(a.out, base), "w").write(fm + body)
        n += 1
    print(f"wrote {n} notes -> {a.out} (open as an Obsidian vault; tags: source/*, intent/*)")

if __name__ == "__main__":
    main()
