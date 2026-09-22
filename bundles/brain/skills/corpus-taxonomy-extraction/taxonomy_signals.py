#!/usr/bin/env python3
"""Semantic taxonomy signals for the health / first-build review (venv only).

Two numbers the stdlib review scripts cannot compute:
  - label_pairs / label_clusters: labels whose embeddings (label text only — descriptions blur
    them) are >= --threshold similar, within one level. String similarity misses reworded
    duplicates ("Payment Processing" / "Payment Transactions and Processing").
  - misplaced: L2s whose tagged chunks sit closer to another L1's chunks than to their parent's.
    Candidates for an agent to confirm, never a decision.

Writes taxonomy/work/signals.json; `health.detect` reads it when present and falls back to
`flags.near_duplicate_labels` when absent. Needs numpy + fastembed (+ sqlite_vec to read a
vec0 chunks_vec table); nothing stdlib imports this module.

Usage: taxonomy_signals.py --taxonomy taxonomy/current.json --db K.sqlite --out taxonomy/work/signals.json
                           [--threshold 0.88] [--margin 0.03] [--min-chunks 3] [--model BAAI/bge-small-en-v1.5]
"""
import argparse, hashlib, itertools, json, os, sqlite3, sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from taxo_io import atomic_write_bytes, intent, nid  # noqa: E402

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


def _unit(m):
    m = np.asarray(m, dtype=np.float32)
    n = np.linalg.norm(m, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return m / n


def _levels(tax):
    """[(label, level, parent)] for L1s and L2s (entities excluded: they cannot be merged)."""
    it = intent(tax)
    rows = [(l1, "L1", None) for l1 in it["tree"]]
    rows += [(k, "L2", l1) for l1, kids in it["tree"].items() for k in kids]
    rows += [(k, "L2", None) for k in it.get("unassigned_l2") or []]
    return rows


def label_signals(tax, embed, threshold):
    rows = _levels(tax)
    if not rows:
        return [], []
    V = _unit(embed([r[0] for r in rows]))
    S = V @ V.T
    pairs = []
    for i, j in itertools.combinations(range(len(rows)), 2):
        (a, la, pa), (b, lb, pb) = rows[i], rows[j]
        if la != lb or S[i, j] < threshold:
            continue
        a, b, pa, pb = (a, b, pa, pb) if a <= b else (b, a, pb, pa)
        pairs.append({"a": a, "b": b, "score": round(float(S[i, j]), 3), "level": la,
                      "parent_a": pa, "parent_b": pb})
    pairs.sort(key=lambda p: (-p["score"], p["a"], p["b"]))
    parent = {r[0]: r[0] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for p in pairs:
        parent[find(p["b"])] = find(p["a"])
    groups = {}
    for label, level, par in rows:
        groups.setdefault(find(label), []).append((label, level, par))
    clusters = []
    for g in groups.values():
        if len(g) < 2:
            continue
        parents = {x[2] for x in g}
        clusters.append({"members": sorted(x[0] for x in g), "level": g[0][1],
                         "parent": parents.pop() if len(parents) == 1 else None})
    clusters.sort(key=lambda c: c["members"])
    return pairs, clusters


def _vectors(conn):
    try:
        rows = conn.execute("SELECT rowid, embedding FROM chunks_vec").fetchall()
    except sqlite3.Error:
        return None
    return {int(r): np.frombuffer(b, dtype=np.float32) for r, b in rows}


def misplaced(tax, conn, margin, min_chunks, warnings):
    vecs = _vectors(conn)
    if vecs is None:
        warnings.append("no readable chunks_vec table — misplacement not computed")
        return []
    members = {}
    for cid, cat in conn.execute("SELECT chunk_id, category_id FROM chunk_topics"):
        if int(cid) in vecs:
            members.setdefault(cat, []).append(vecs[int(cid)])
    cent = {cat: _unit(np.mean(v, axis=0)) for cat, v in members.items()}
    it = intent(tax)
    l1c = {l1: cent[nid(l1)] for l1 in it["tree"] if nid(l1) in cent}
    out = []
    for l1, kids in it["tree"].items():
        if l1 not in l1c:
            continue
        for k in kids:
            n = len(members.get(nid(k), []))
            if n < min_chunks:
                continue
            scores = {p: float(cent[nid(k)] @ v) for p, v in l1c.items()}
            best = max(sorted(scores), key=lambda p: scores[p])
            gain = scores[best] - scores[l1]
            if best != l1 and gain >= margin:
                out.append({"node": k, "parent": l1, "better_parent": best, "margin": round(gain, 3), "chunks": n})
    out.sort(key=lambda m: (-m["margin"], m["node"]))
    return out


def compute(tax, conn, embed, threshold=0.88, margin=0.03, min_chunks=3):
    warnings = []
    pairs, clusters = label_signals(tax, embed, threshold)
    out = {"schema": 1, "threshold": threshold, "label_pairs": pairs, "label_clusters": clusters,
           "misplaced": misplaced(tax, conn, margin, min_chunks, warnings)}
    if warnings:
        out["warnings"] = warnings
    return out


def _connect(db):
    c = sqlite3.connect(f"file:{os.path.abspath(db)}?mode=ro", uri=True)
    try:
        import sqlite_vec
        c.enable_load_extension(True)
        sqlite_vec.load(c)
    except Exception:
        pass   # a plain chunks_vec table still reads; a vec0 one then yields the warning above
    return c


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--taxonomy", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threshold", type=float, default=0.88)
    ap.add_argument("--margin", type=float, default=0.03)
    ap.add_argument("--min-chunks", type=int, default=3)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    a = ap.parse_args(argv)
    raw = open(a.taxonomy, "rb").read()
    from fastembed import TextEmbedding
    model = TextEmbedding(a.model)
    res = compute(json.loads(raw), _connect(a.db), lambda texts: np.array(list(model.embed(texts))),
                  a.threshold, a.margin, a.min_chunks)
    res["model"] = a.model
    res["taxonomy_sha256"] = hashlib.sha256(raw).hexdigest()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    atomic_write_bytes(a.out, (json.dumps(res, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    print(json.dumps({"status": "written", "out": a.out, "pairs": len(res["label_pairs"]),
                      "clusters": len(res["label_clusters"]), "misplaced": len(res["misplaced"]),
                      **({"warnings": res["warnings"]} if res.get("warnings") else {})}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
