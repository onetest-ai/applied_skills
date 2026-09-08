#!/usr/bin/env python3
"""Local hybrid retrieval over Markdown — SQLite FTS5 + sqlite-vec, fused by RRF.

One portable file, no server (pattern lifted from github.com/arozumenko/wikis:
BM25 + vector, Reciprocal Rank Fusion). Torch-free embeddings via fastembed/onnx.
Feed it parser/plain Markdown; it chunks, embeds, and serves hybrid recall.

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
      CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, source TEXT, ord INT, title TEXT, text TEXT, sha TEXT, image TEXT);
      CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text);
    """)
    c.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{dim}])")
    cols = {r[1] for r in c.execute("PRAGMA table_info(chunks)")}
    for col in ("sha", "image"):   # tolerate an older store that predates these columns
        if col not in cols:
            c.execute(f"ALTER TABLE chunks ADD COLUMN {col} TEXT")

_IMG_MARKER = re.compile(r"^\s*<!--\s*image:\s*(.+?)\s*-->\s*$", re.M)

def _split_image(body):
    """Pull a `<!-- image: PATH -->` marker out of a section body (from the visual
    parser). Returns (image_path_or_None, body_without_marker)."""
    m = _IMG_MARKER.search(body)
    if not m:
        return None, body
    return m.group(1), _IMG_MARKER.sub("", body, count=1).strip()

def _has(c, table):
    return bool(c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())

def delete_docs(c, sources):
    """Remove every trace of the given source docs from the RAG lane AND the
    dependent tag/graph/related rows keyed by their chunk ids (so nothing is orphaned)."""
    has_topics = _has(c, "chunk_topics")
    has_edges  = _has(c, "graph_edges")
    has_rel    = _has(c, "related")
    for src in sources:
        ids = [r[0] for r in c.execute("SELECT id FROM chunks WHERE source=?", (src,))]
        for cid in ids:
            c.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
            c.execute("DELETE FROM chunks_vec WHERE rowid=?", (cid,))
            if has_topics:
                c.execute("DELETE FROM chunk_topics WHERE chunk_id=?", (cid,))
            if has_edges:
                c.execute("DELETE FROM graph_edges WHERE rel='about' AND source=?", (f"chunk:{cid}",))
            if has_rel:
                c.execute("DELETE FROM related WHERE chunk_id=? OR related_id=?", (cid, cid))
        c.execute("DELETE FROM chunks WHERE source=?", (src,))
    return len(sources)

def build_related(c, k=6, min_score=0.55, cross_doc=True):
    """Native semantic 'related' layer from the vectors we already store: for each
    chunk, its top-k cosine-nearest OTHER sections. Full 384-dim similarity (not a
    lossy 2D/3D projection), deterministic, offline — no external plugin/API.
    Writes `related(chunk_id, related_id, score)` (symmetric pair kept once, higher
    score wins). cross_doc=True favors cross-document links (the useful, non-obvious
    ones); set False to also relate adjacent sections of the same doc."""
    c.executescript("""
      CREATE TABLE IF NOT EXISTS related(chunk_id INT, related_id INT, score REAL,
                                         PRIMARY KEY(chunk_id, related_id));
      CREATE INDEX IF NOT EXISTS idx_rel_chunk ON related(chunk_id);
      DELETE FROM related;
    """)
    src = {r[0]: r[1] for r in c.execute("SELECT id, source FROM chunks")}
    n = 0
    for cid in list(src):
        row = c.execute("SELECT embedding FROM chunks_vec WHERE rowid=?", (cid,)).fetchone()
        if not row:
            continue
        nbrs = c.execute(
            "SELECT rowid, distance FROM chunks_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (row[0], k + 8)).fetchall()
        kept = 0
        for rid, dist in nbrs:
            if rid == cid:
                continue
            if cross_doc and src.get(rid) == src.get(cid):
                continue
            score = round(1.0 - (dist * dist) / 2.0, 4)   # unit vectors: cosine = 1 - L2^2/2
            if score < min_score:
                continue
            a, b = (cid, rid) if cid < rid else (rid, cid)
            cur = c.execute("SELECT score FROM related WHERE chunk_id=? AND related_id=?", (a, b)).fetchone()
            if cur is None or score > cur[0]:
                c.execute("INSERT OR REPLACE INTO related VALUES(?,?,?)", (a, b, score))
            kept += 1
            if kept >= k:
                break
    n = c.execute("SELECT COUNT(*) FROM related").fetchone()[0]
    c.commit()
    return n

def cmd_related(a):
    c = connect(a.db)
    if not _has(c, "chunks_vec"):
        print("no vectors — run index first", file=sys.stderr); return
    n = build_related(c, a.k, a.min_score, not a.within_doc)
    print(f"related: {n} semantic edges (k={a.k}, min_score={a.min_score}, "
          f"{'cross-doc' if not a.within_doc else 'incl. within-doc'}) -> {a.db}", file=sys.stderr)

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
    # pull image markers out BEFORE embedding so the marker never pollutes text/vectors.
    # A visual page may split into several sections (VLM sub-headings) — only the first
    # carries the marker, so sections INHERIT the last page image within the same doc
    # (reset when the source changes, or when a new page marker appears).
    clean = []
    cur_src, cur_img = None, None
    for (src, i, title, body) in rows:
        if src != cur_src:
            cur_src, cur_img = src, None
        img, body = _split_image(body)
        if img is not None:
            cur_img = img
        clean.append((src, i, title, cur_img, body))     # (src,i,title,image,body)
    print(f"embedding {len(clean)} sections from {len(sources)} doc(s) ({model})…", file=sys.stderr)
    vecs = embed(model, [f"{t}\n\n{b}" for (_, _, t, _, b) in clean])
    for (src, i, title, image, body), v in zip(clean, vecs):
        cid = chunk_id(src, i)
        sha = hashlib.sha256(body.encode()).hexdigest()
        c.execute("INSERT OR REPLACE INTO chunks(id,source,ord,title,text,sha,image) VALUES(?,?,?,?,?,?,?)",
                  (cid, src, i, title, body, sha, image))
        c.execute("INSERT INTO chunks_fts(rowid,text) VALUES(?,?)", (cid, f"{title}\n{body}"))
        c.execute("INSERT INTO chunks_vec(rowid,embedding) VALUES(?,?)", (cid, sqlite_vec.serialize_float32(v)))
    return len(clean), len(sources)

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
        src, ordv, title, txt = c.execute("SELECT source,ord,title,text FROM chunks WHERE id=?", (rid,)).fetchone()
        out.append({"id": rid, "score": round(score[rid], 5), "source": src, "ord": ordv, "title": title, "text": txt})
    return {"query": query, "fts_hits": len(fts), "vec_hits": len(vec), "results": out}

def _note_pather():
    """Best-effort vault note-path function (shared naming with to_obsidian)."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "corpus-taxonomy-extraction"))
        from to_obsidian import note_path
        return note_path
    except Exception:
        return None

def cmd_search(a):
    res = search(connect(a.db), a.model, a.query, a.k)
    np = _note_pather()
    if np:                                    # annotate each hit with its vault note path (for transparency)
        for r in res["results"]:
            r["note"] = np(r["source"], r.get("ord", 0), r.get("title"))
    if a.json:
        print(json.dumps(res, indent=2)); return
    print(f"query: {res['query']}  (fts={res['fts_hits']} vec={res['vec_hits']})\n")
    for r in res["results"]:
        print(f"[{r['score']}] {r['source'][:55]}" + (f"\n   vault: {r['note']}.md" if r.get("note") else "")
              + f"\n   {' '.join(r['text'].split())[:240]}\n")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("index", "search", "related"):
        p = sub.add_parser(name)
        p.add_argument("--db", required=True); p.add_argument("--model", default=DEFAULT_MODEL); p.add_argument("--dim", type=int, default=DEFAULT_DIM)
        if name == "index":
            p.add_argument("--corpus"); p.add_argument("--max-chars", type=int, default=1200); p.add_argument("--reset", action="store_true")
            p.add_argument("--docs", help="comma list of source relpaths to (re)index only (incremental)")
            p.add_argument("--delete", help="comma list of source relpaths to remove from the index")
        elif name == "search":
            p.add_argument("--query", required=True); p.add_argument("--k", type=int, default=8); p.add_argument("--json", action="store_true")
        else:  # related
            p.add_argument("--k", type=int, default=6, help="neighbors per chunk")
            p.add_argument("--min-score", type=float, default=0.55, help="cosine cutoff [0..1]")
            p.add_argument("--within-doc", action="store_true", help="also relate sections of the same doc")
    a = ap.parse_args()
    {"index": cmd_index, "search": cmd_search, "related": cmd_related}[a.cmd](a)
