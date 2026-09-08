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
import argparse, glob, hashlib, json, os, re, sqlite3, sys
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

def chunk_id(source, ordv):
    """Stable, content-addressed chunk id = f(source, section-ordinal). An unchanged
    document keeps the SAME chunk ids across rebuilds, so chunk_topics / graph 'about'
    edges (keyed by chunk_id) survive. Positive int64 (valid vec0 rowid)."""
    h = hashlib.sha256(f"{source}\x00{ordv}".encode()).digest()
    return int.from_bytes(h[:8], "big") >> 1

def _ensure_schema(c, dim):
    c.executescript("""
      CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, source TEXT, ord INT, title TEXT, text TEXT, sha TEXT);
      CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text);
    """)
    c.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{dim}])")
    # tolerate an older store that predates the `sha` column
    if "sha" not in {r[1] for r in c.execute("PRAGMA table_info(chunks)")}:
        c.execute("ALTER TABLE chunks ADD COLUMN sha TEXT")

def delete_docs(c, sources):
    """Remove every trace of the given source docs from the RAG lane AND the
    dependent tag/graph rows keyed by their chunk ids (so nothing is orphaned)."""
    has_topics = bool(c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_topics'").fetchone())
    has_edges  = bool(c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='graph_edges'").fetchone())
    for src in sources:
        ids = [r[0] for r in c.execute("SELECT id FROM chunks WHERE source=?", (src,))]
        for cid in ids:
            c.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
            c.execute("DELETE FROM chunks_vec WHERE rowid=?", (cid,))
            if has_topics:
                c.execute("DELETE FROM chunk_topics WHERE chunk_id=?", (cid,))
            if has_edges:
                c.execute("DELETE FROM graph_edges WHERE rel='about' AND source=?", (f"chunk:{cid}",))
        c.execute("DELETE FROM chunks WHERE source=?", (src,))
    return len(sources)

def index_docs(c, model, corpus, sources, dim, max_chars):
    """(Re)index a specific set of source docs idempotently: delete-then-insert with
    stable ids. Embeds only these docs. Returns (n_chunks, n_docs)."""
    delete_docs(c, sources)
    rows = []
    for src in sources:
        f = os.path.join(corpus, src)
        if not os.path.exists(f):
            continue
        for i, (title, body) in enumerate(sections(open(f).read(), max_chars)):
            rows.append((src, i, title, body))
    if not rows:
        return 0, 0
    print(f"embedding {len(rows)} sections from {len(sources)} doc(s) ({model})…", file=sys.stderr)
    vecs = embed(model, [f"{r[2]}\n\n{r[3]}" for r in rows])
    for (src, i, title, body), v in zip(rows, vecs):
        cid = chunk_id(src, i)
        sha = hashlib.sha256(body.encode()).hexdigest()
        c.execute("INSERT OR REPLACE INTO chunks(id,source,ord,title,text,sha) VALUES(?,?,?,?,?,?)",
                  (cid, src, i, title, body, sha))
        c.execute("INSERT INTO chunks_fts(rowid,text) VALUES(?,?)", (cid, f"{title}\n{body}"))
        c.execute("INSERT INTO chunks_vec(rowid,embedding) VALUES(?,?)", (cid, sqlite_vec.serialize_float32(v)))
    return len(rows), len(sources)

def corpus_docs(corpus):
    return sorted(os.path.relpath(f, corpus)
                  for f in glob.glob(os.path.join(corpus, "**", "*.md"), recursive=True))

def cmd_index(a):
    c = connect(a.db)
    if a.reset:
        c.executescript("DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS chunks_fts; DROP TABLE IF EXISTS chunks_vec;")
    _ensure_schema(c, a.dim)
    deletes = [s.strip() for s in (a.delete or "").split(",") if s.strip()]
    if deletes:
        delete_docs(c, deletes)
        print(f"deleted {len(deletes)} doc(s) from the index", file=sys.stderr)
    # which docs to (re)index: an explicit --docs subset, else the whole corpus (idempotent)
    sources = ([s.strip() for s in a.docs.split(",") if s.strip()] if a.docs
               else corpus_docs(a.corpus)) if a.corpus else []
    n_chunks, n_docs = index_docs(c, a.model, a.corpus, sources, a.dim, a.max_chars) if sources else (0, 0)
    c.commit()
    print(f"indexed {n_chunks} chunks from {n_docs} doc(s) -> {a.db}", file=sys.stderr)

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
            p.add_argument("--corpus"); p.add_argument("--max-chars", type=int, default=1200); p.add_argument("--reset", action="store_true")
            p.add_argument("--docs", help="comma list of source relpaths to (re)index only (incremental)")
            p.add_argument("--delete", help="comma list of source relpaths to remove from the index")
        else:
            p.add_argument("--query", required=True); p.add_argument("--k", type=int, default=8); p.add_argument("--json", action="store_true")
    a = ap.parse_args()
    {"index": cmd_index, "search": cmd_search}[a.cmd](a)
