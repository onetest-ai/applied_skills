#!/usr/bin/env python3
"""Local hybrid retrieval over Markdown — SQLite FTS5 + sqlite-vec, fused by RRF.

One portable file, no server (pattern lifted from github.com/arozumenko/wikis:
BM25 + vector, Reciprocal Rank Fusion). Torch-free embeddings via fastembed/onnx.
Feed it Docling/plain Markdown; it chunks, embeds, and serves hybrid recall.

  index  --db K.sqlite --corpus DIR [--model M] [--max-chars N] [--reset]
  search --db K.sqlite --query "..." [--k 8] [--json]

Deps: sqlite-vec, fastembed  (pip install sqlite-vec fastembed).
Lives in the SAME sqlite file as the numeric marts + graph tables, so one
`.sqlite` is the whole knowledge store.
"""
import argparse, glob, json, os, re, sqlite3, sys
import sqlite_vec
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chunking import sections

RRF_K = 60; W_FTS = 0.4; W_VEC = 0.6; POOL = 30
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"; DEFAULT_DIM = 384
_emb = {}

def embedder(model):
    if model not in _emb:
        from fastembed import TextEmbedding
        _emb[model] = TextEmbedding(model)
    return _emb[model]

def embed(model, texts):
    return [list(v) for v in embedder(model).embed(list(texts))]

def connect(db):
    c = sqlite3.connect(db)
    c.enable_load_extension(True); sqlite_vec.load(c); c.enable_load_extension(False)
    return c

def fts_query(q):
    toks = [t for t in re.findall(r"[A-Za-z0-9]+", q.lower()) if len(t) > 2]
    return " OR ".join(toks) if toks else '""'

def cmd_index(a):
    c = connect(a.db)
    if a.reset:
        c.executescript("DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS chunks_fts; DROP TABLE IF EXISTS chunks_vec;")
    c.executescript("""
      CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, source TEXT, ord INT, title TEXT, text TEXT);
      CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text);
    """)
    c.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{a.dim}])")
    rows = []
    for f in sorted(glob.glob(os.path.join(a.corpus, "**", "*.md"), recursive=True)):
        src = os.path.relpath(f, a.corpus)
        for i, (title, body) in enumerate(sections(open(f).read(), a.max_chars)):
            rows.append((src, i, title, body))
    print(f"chunks (sections): {len(rows)} from {len(set(r[0] for r in rows))} docs; embedding ({a.model})…", file=sys.stderr)
    vecs = embed(a.model, [f"{r[2]}\n\n{r[3]}" for r in rows])   # title + body embedded together
    for (src, i, title, body), v in zip(rows, vecs):
        rid = c.execute("INSERT INTO chunks(source,ord,title,text) VALUES(?,?,?,?)", (src, i, title, body)).lastrowid
        c.execute("INSERT INTO chunks_fts(rowid,text) VALUES(?,?)", (rid, f"{title}\n{body}"))
        c.execute("INSERT INTO chunks_vec(rowid,embedding) VALUES(?,?)", (rid, sqlite_vec.serialize_float32(v)))
    c.commit()
    print(f"indexed {len(rows)} chunks -> {a.db}", file=sys.stderr)

def search(c, model, query, k):
    qv = embed(model, [query])[0]
    fts = [r[0] for r in c.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
        (fts_query(query), POOL))]
    vec = [r[0] for r in c.execute(
        "SELECT rowid FROM chunks_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (sqlite_vec.serialize_float32(qv), POOL))]
    score = {}
    for w, lst in ((W_FTS, fts), (W_VEC, vec)):
        for rank, rid in enumerate(lst, 1):
            score[rid] = score.get(rid, 0.0) + w / (RRF_K + rank)
    top = sorted(score, key=score.get, reverse=True)[:k]
    out = []
    for rid in top:
        src, title, txt = c.execute("SELECT source,title,text FROM chunks WHERE id=?", (rid,)).fetchone()
        out.append({"score": round(score[rid], 5), "source": src, "title": title, "text": txt})
    return {"query": query, "fts_hits": len(fts), "vec_hits": len(vec), "results": out}

def cmd_search(a):
    res = search(connect(a.db), a.model, a.query, a.k)
    if a.json:
        print(json.dumps(res, indent=2)); return
    print(f"query: {res['query']}  (fts={res['fts_hits']} vec={res['vec_hits']})\n")
    for r in res["results"]:
        print(f"[{r['score']}] {r['source'][:55]}\n   {' '.join(r['text'].split())[:240]}\n")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("index", "search"):
        p = sub.add_parser(name)
        p.add_argument("--db", required=True); p.add_argument("--model", default=DEFAULT_MODEL); p.add_argument("--dim", type=int, default=DEFAULT_DIM)
        if name == "index":
            p.add_argument("--corpus", required=True); p.add_argument("--max-chars", type=int, default=1200); p.add_argument("--reset", action="store_true")
        else:
            p.add_argument("--query", required=True); p.add_argument("--k", type=int, default=8); p.add_argument("--json", action="store_true")
    a = ap.parse_args()
    {"index": cmd_index, "search": cmd_search}[a.cmd](a)
