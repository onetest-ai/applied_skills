#!/usr/bin/env python3
"""Compare two eval records item by item.

  compare.py A B [--out FILE.html] [--allow-scope-change]

A and B are record folders written by record.py (meta.json + score.json). The comparison refuses when the two
runs used different answer keys, and, unless --allow-scope-change, when they ran on different snapshots. With the flag
it prints the all-meetings table and the like-for-like table (meetings both runs had processed), as the Oct 25
Friday-vs-now comparison should have from the start."""
import argparse
import html
import json
import sys
from pathlib import Path

from evalview import mcnemar_exact  # one definition, shared with the live view

GAPS = ["B7", "B7c", "B8", "B9", "B10", "B11", "B12", "B13", "B14", "B15", "B16"]


def incompatible(a, b, allow_scope_change=False):
    if not a.get("key_sha") or a.get("key_sha") != b.get("key_sha"):
        return f"different answer keys ({a.get('key_sha')} vs {b.get('key_sha')}): no delta can be reported"
    if a.get("covered_by", True) != b.get("covered_by", True):
        return "one record used the key's covered_by and the other did not: score both the same way (record.py --neutral)"
    if a.get("snapshot_of") != b.get("snapshot_of") and not allow_scope_change:
        return ("different snapshots: pass --allow-scope-change to see the all-meetings and the like-for-like table")
    return None


def paired(A, B, key, meetings=None):
    """Per gold item: gained / lost / still missed / same hit. meetings restricts to those meetings (like for like)."""
    a, b = {i["cid"]: i["match"] for i in A}, {i["cid"]: i["match"] for i in B}
    cids = [c for c in a if c in b and (meetings is None or key.get(c, {}).get("evidence") in meetings)]
    out = {"n": len(cids), "gained": [], "lost": [], "still_missed": [], "same_hit": []}
    for c in cids:
        out["gained" if b[c] and not a[c] else "lost" if a[c] and not b[c] else "same_hit" if a[c] else "still_missed"].append(c)
    committed = [c for c in cids if key.get(c, {}).get("commitment") == "committed"]
    lost_c = sum(1 for c in committed if c in out["lost"])
    gained_c = sum(1 for c in committed if c in out["gained"])
    out["mcnemar_committed"] = {"lost": lost_c, "gained": gained_c, "p": round(mcnemar_exact(lost_c, gained_c), 4)}
    return out


def compare(ma, sa, mb, sb, key):
    """All-meetings and like-for-like tables. A run that did not process every golden meeting (incomplete) is compared
    only on the meetings both runs processed: its unprocessed meetings would otherwise read as lost items."""
    shared = set(ma.get("processed") or []) & set(mb.get("processed") or [])
    partial = bool(sa.get("incomplete") or sb.get("incomplete") or ma.get("incomplete") or mb.get("incomplete"))
    scope = ma.get("snapshot_of") != mb.get("snapshot_of")
    return {"all": None if partial else paired(sa["items"], sb["items"], key),
            "like_for_like": paired(sa["items"], sb["items"], key, meetings=shared) if (partial or scope) else None}


def _load(d):
    d = Path(d)
    return json.loads((d / "meta.json").read_text()), json.loads((d / "score.json").read_text())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--out")
    ap.add_argument("--allow-scope-change", action="store_true")
    x = ap.parse_args()
    (ma, sa), (mb, sb) = _load(x.a), _load(x.b)
    why = incompatible(ma, mb, x.allow_scope_change)
    if why:
        print(json.dumps({"status": "refused", "why": why}))
        sys.exit(1)
    key = {json.loads(l)["cid"]: json.loads(l) for l in open(ma["key_path"]) if l.strip() and not l.startswith("#")}
    res = {"a": x.a, "b": x.b, **compare(ma, sa, mb, sb, key),
           "gaps": {g: {"a": sa.get(g), "b": sb.get(g)} for g in GAPS if g in sa}}
    if x.out:
        Path(x.out).write_text(page(res, key), encoding="utf-8")
    print(json.dumps({"status": "ok", "all": {k: (len(v) if isinstance(v, list) else v) for k, v in (res["all"] or {}).items()} or None,
                      "like_for_like": {k: (len(v) if isinstance(v, list) else v) for k, v in (res["like_for_like"] or {}).items()} or None}))


def page(r, key):
    e = lambda s: html.escape(str(s if s is not None else "—"))
    fmt = lambda v: f"{v['ok']}/{v['n']} ({v['rate']:.0%}, LB {v['lb95']:.0%})" if v and v.get("n") else "n/a"
    rows = "".join(f"<tr><td>{g}</td><td>{fmt(v['a'])}</td><td>{fmt(v['b'])}</td></tr>" for g, v in r["gaps"].items())
    def items(cids):
        return "".join(f"<li>{e(key[c].get('title'))} <span style=color:#888>({e(key[c]['evidence'][:10])})</span></li>" for c in cids)
    sec = lambda name, p: "" if not p else (
        f"<h2>{name}: n={p['n']}</h2><p>gained {len(p['gained'])}, lost {len(p['lost'])}, still missed {len(p['still_missed'])},"
        f" same hit {len(p['same_hit'])}; committed McNemar p = {p['mcnemar_committed']['p']}</p>"
        f"<details><summary>Gained</summary><ul>{items(p['gained'])}</ul></details>"
        f"<details><summary>Lost</summary><ul>{items(p['lost'])}</ul></details>")
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Run Comparison</title><style>body{{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:20px 16px;"
            f"background:#f7f6f2;color:#1d1f23}}@media (prefers-color-scheme:dark){{body{{background:#121417;color:#e8e6e1}}}}"
            f"table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #8884;padding:5px 8px;text-align:left}}</style></head>"
            f"<body><h1>Run comparison</h1><p>{e(r['a'])} → {e(r['b'])}</p><table><tr><th>Gap</th><th>A</th><th>B</th></tr>{rows}</table>"
            f"{sec('All meetings', r['all'])}{sec('Like for like (meetings both runs processed)', r['like_for_like'])}</body></html>")


if __name__ == "__main__":
    main()
