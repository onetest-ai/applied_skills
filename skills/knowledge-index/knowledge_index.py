#!/usr/bin/env python3
"""Local hybrid retrieval over Markdown — SQLite FTS5 + sqlite-vec, fused by RRF.

One portable file, no server (pattern lifted from github.com/arozumenko/wikis:
BM25 + vector, Reciprocal Rank Fusion). Torch-free embeddings via fastembed/onnx.
Feed it parser/plain Markdown; it chunks, embeds, and serves hybrid recall.

  index  --db K.sqlite --corpus DIR [--model M] [--max-chars N] [--reset]
  search        --db K.sqlite --query "..." [--k 8] [--json]
  temporal-load --db K.sqlite --ledger temporal.json

Deps: sqlite-vec, fastembed  (pip install sqlite-vec fastembed).
Lives in the SAME sqlite file as the numeric marts + graph tables, so one
`.sqlite` is the whole knowledge store.
"""
import argparse, glob, hashlib, json, os, re, sqlite3, sys
import csv
import sqlite_vec
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chunking import section_records, sections

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
    """Stable structural chunk id = f(source, section-ordinal). An unchanged document
    with unchanged section boundaries keeps the SAME chunk ids across rebuilds, so
    chunk_topics / graph 'about' edges survive. Positive int64 (valid vec0 rowid)."""
    h = hashlib.sha256(f"{source}\x00{ordv}".encode()).digest()
    return int.from_bytes(h[:8], "big") >> 1

def _ensure_schema(c, dim):
    c.executescript("""
      CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, source TEXT, ord INT, title TEXT, text TEXT, sha TEXT, image TEXT);
      CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text);
      CREATE TABLE IF NOT EXISTS documents(source TEXT PRIMARY KEY, content_hash TEXT NOT NULL, indexed_at TEXT NOT NULL);
    """)
    c.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{dim}])")
    # Guard: if chunks_vec already existed with a different dimension, the IF NOT
    # EXISTS above was a no-op and all subsequent inserts would silently corrupt
    # data or raise an opaque sqlite-vec error.  Detect this early.
    _vec_row = c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks_vec'"
    ).fetchone()
    if _vec_row and _vec_row[0]:
        _m = re.search(r"float\[(\d+)\]", _vec_row[0])
        if _m:
            _stored = int(_m.group(1))
            if _stored != dim:
                raise ValueError(
                    f"existing index has dim={_stored}; "
                    f"pass --reset to rebuild or use --dim {_stored}"
                )
    cols = {r[1] for r in c.execute("PRAGMA table_info(chunks)")}
    additions = {
        "sha": "TEXT", "image": "TEXT", "embedding_content_hash": "TEXT",
        "created_at": "TEXT", "event_date": "TEXT", "valid_from": "TEXT",
        "valid_to": "TEXT", "status": "TEXT NOT NULL DEFAULT 'ACTIVE'",
        "parent_heading": "TEXT", "breadcrumb_path": "TEXT", "speaker": "TEXT",
    }
    for col, decl in additions.items():
        if col not in cols:
            c.execute(f"ALTER TABLE chunks ADD COLUMN {col} {decl}")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_status ON chunks(status)")

_IMG_MARKER = re.compile(r"^\s*<!--\s*image:\s*(.+?)\s*-->\s*$", re.M)
_SPEAKER_MARKER = re.compile(r"^\s*<!--\s*speaker:\s*(.+?)\s*-->\s*$", re.M)
_EVENT_DATE = re.compile(r"(?im)^(?:event_date:\s*|\*\*Session:\*\*\s*)(\d{4}-\d{2}-\d{2})")

def _split_image(body):
    """Pull a `<!-- image: PATH -->` marker out of a section body (from the visual
    parser). Returns (image_path_or_None, body_without_marker)."""
    m = _IMG_MARKER.search(body)
    if not m:
        return None, body
    return m.group(1), _IMG_MARKER.sub("", body, count=1).strip()

def _split_speaker(body):
    m = _SPEAKER_MARKER.search(body)
    if not m:
        return None, body
    return m.group(1).strip(), _SPEAKER_MARKER.sub("", body, count=1).strip()

def _document_dates(path, raw):
    import datetime
    created = datetime.datetime.fromtimestamp(os.path.getmtime(path), datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    match = _EVENT_DATE.search(raw)
    if not match:
        match = re.search(r"(?<!\d)(20\d{2})[-_](\d{2})[-_](\d{2})(?!\d)", os.path.basename(path))
        event = "-".join(match.groups()) if match else None
    else:
        event = match.group(1)
    return created, event

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
        if _has(c, "documents"):
            c.execute("DELETE FROM documents WHERE source=?", (src,))
    return len(sources)

def _ensure_related_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS related(
        chunk_id INT, related_id INT, score REAL, edge_type TEXT NOT NULL DEFAULT 'SIMILAR',
        directed INT NOT NULL DEFAULT 0, PRIMARY KEY(chunk_id, related_id))""")
    rel_cols = {r[1] for r in c.execute("PRAGMA table_info(related)")}
    if "edge_type" not in rel_cols:
        c.execute("ALTER TABLE related ADD COLUMN edge_type TEXT NOT NULL DEFAULT 'SIMILAR'")
    if "directed" not in rel_cols:
        c.execute("ALTER TABLE related ADD COLUMN directed INT NOT NULL DEFAULT 0")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rel_chunk ON related(chunk_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rel_related ON related(related_id)")

def build_related(c, k=6, min_score=0.55, cross_doc=True, commit=True):
    """Native semantic 'related' layer from the vectors we already store: for each
    chunk, its top-k cosine-nearest OTHER sections. Full 384-dim similarity (not a
    lossy 2D/3D projection), deterministic, offline — no external plugin/API.
    Writes `related(chunk_id, related_id, score)` (symmetric pair kept once, higher
    score wins). cross_doc=True favors cross-document links (the useful, non-obvious
    ones); set False to also relate adjacent sections of the same doc."""
    # Individual statements preserve the caller's transaction. sqlite3.executescript()
    # implicitly commits pending work and would make brain_sync.apply only partially atomic.
    _ensure_related_schema(c)
    c.execute("DELETE FROM related WHERE edge_type='SIMILAR'")
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
            cur = c.execute("SELECT score,edge_type FROM related WHERE chunk_id=? AND related_id=?", (a, b)).fetchone()
            # A derived similarity must never erase an explicit semantic relation.
            if cur is None or (cur[1] == "SIMILAR" and score > cur[0]):
                c.execute("INSERT OR REPLACE INTO related(chunk_id,related_id,score,edge_type,directed) VALUES(?,?,?,'SIMILAR',0)", (a, b, score))
            kept += 1
            if kept >= k:
                break
    n = c.execute("SELECT COUNT(*) FROM related").fetchone()[0]
    if commit:
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
    """Incrementally index changed chunks and skip unchanged documents."""
    _ensure_schema(c, dim)
    rows, skipped_docs, changed_docs = [], 0, 0
    for src in sources:
        f = os.path.join(corpus, src)
        if not os.path.exists(f):
            continue
        raw = open(f, encoding="utf-8", errors="replace").read()
        # The document-level cache key includes every setting that changes chunks
        # or vectors. A model/chunk-size change must not skip the whole document.
        doc_hash = hashlib.sha256(f"{model}\x00{dim}\x00{max_chars}\x00{raw}".encode()).hexdigest()
        known = c.execute("SELECT content_hash FROM documents WHERE source=?", (src,)).fetchone()
        if known and known[0] == doc_hash:
            skipped_docs += 1
            continue
        changed_docs += 1
        created_at, event_date = _document_dates(f, raw)
        new_ids = set()
        for i, record in enumerate(section_records(raw, max_chars)):
            title, body = record["title"], record["body"]
            cid = chunk_id(src, i); new_ids.add(cid)
            rows.append((src, i, cid, title, body, record["parent_heading"], record["breadcrumb_path"], created_at, event_date, doc_hash))
        old_ids = {r[0] for r in c.execute("SELECT id FROM chunks WHERE source=?", (src,))}
        for cid in old_ids - new_ids:
            c.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
            c.execute("DELETE FROM chunks_vec WHERE rowid=?", (cid,))
            if _has(c, "chunk_topics"):
                c.execute("DELETE FROM chunk_topics WHERE chunk_id=?", (cid,))
            if _has(c, "graph_edges"):
                c.execute("DELETE FROM graph_edges WHERE rel='about' AND source=?", (f"chunk:{cid}",))
            if _has(c, "related"):
                c.execute("DELETE FROM related WHERE chunk_id=? OR related_id=?", (cid, cid))
            c.execute("DELETE FROM chunks WHERE id=?", (cid,))
        c.execute("INSERT OR REPLACE INTO documents VALUES(?,?,datetime('now'))", (src, doc_hash))
    if not rows:
        return 0, 0, skipped_docs
    # pull image markers out BEFORE embedding so the marker never pollutes text/vectors.
    # A visual page may split into several sections (VLM sub-headings) — only the first
    # carries the marker, so sections INHERIT the last page image within the same doc
    # (reset when the source changes, or when a new page marker appears).
    clean = []
    cur_src, cur_img = None, None
    for (src, i, cid, title, body, parent, breadcrumb, created_at, event_date, doc_hash) in rows:
        if src != cur_src:
            cur_src, cur_img = src, None
        img, body = _split_image(body)
        speaker, body = _split_speaker(body)
        if img is not None:
            cur_img = img
        emb_hash = hashlib.sha256(
            f"{model}\x00{dim}\x00{title}\x00{body}\x00{breadcrumb or ''}\x00{speaker or ''}".encode()
        ).hexdigest()
        previous = c.execute(
            "SELECT embedding_content_hash,status,valid_from,valid_to FROM chunks WHERE id=?", (cid,)
        ).fetchone()
        clean.append((src, i, cid, title, cur_img, body, parent, breadcrumb, speaker,
                      created_at, event_date, emb_hash, previous))
    changed = [row for row in clean if not row[-1] or row[-1][0] != row[-2]]
    print(f"embedding {len(changed)} changed sections from {changed_docs} changed doc(s); skipped {skipped_docs} unchanged doc(s) ({model})…", file=sys.stderr)
    # Bug 9 fix: guard embed() call — fastembed behaviour on empty input is
    # undefined.  When every section is already embedded with the same hash
    # (e.g. only the doc-level cache-key changed), skip the embed call entirely.
    vecs = iter(embed(model, [f"{r[3]}\n\n{r[5]}" for r in changed]) if changed else [])
    for (src, i, cid, title, image, body, parent, breadcrumb, speaker, created_at, event_date, emb_hash, previous) in clean:
        sha = hashlib.sha256(body.encode()).hexdigest()
        unchanged = bool(previous and previous[0] == emb_hash)
        if unchanged:
            c.execute("""UPDATE chunks SET source=?,ord=?,title=?,text=?,sha=?,image=?,created_at=?,event_date=?,
                       valid_from=COALESCE(valid_from,?),parent_heading=?,breadcrumb_path=?,speaker=? WHERE id=?""",
                      (src,i,title,body,sha,image,created_at,event_date,event_date or created_at,parent,breadcrumb,speaker,cid))
            continue
        v = next(vecs)
        c.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
        c.execute("DELETE FROM chunks_vec WHERE rowid=?", (cid,))
        prior_status = previous[1] if previous else "ACTIVE"
        prior_valid_from = previous[2] if previous else None
        prior_valid_to = previous[3] if previous else None
        c.execute("""INSERT OR REPLACE INTO chunks(
                     id,source,ord,title,text,sha,image,embedding_content_hash,created_at,event_date,
                     valid_from,valid_to,status,parent_heading,breadcrumb_path,speaker)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (cid,src,i,title,body,sha,image,emb_hash,created_at,event_date,
                   prior_valid_from or event_date or created_at,prior_valid_to,prior_status,
                   parent,breadcrumb,speaker))
        c.execute("INSERT INTO chunks_fts(rowid,text) VALUES(?,?)", (cid, f"{breadcrumb}\n{speaker or ''}\n{title}\n{body}"))
        c.execute("INSERT INTO chunks_vec(rowid,embedding) VALUES(?,?)", (cid, sqlite_vec.serialize_float32(v)))
    return len(changed), changed_docs, skipped_docs

_INDEXED_EXTS = ("*.md", "*.vtt", "*.srt")
_BINARY_EXTS = ("*.pdf", "*.pptx", "*.xlsx", "*.docx")

def corpus_docs(corpus):
    indexed = sorted(
        os.path.relpath(f, corpus)
        for ext in _INDEXED_EXTS
        for f in glob.glob(os.path.join(corpus, "**", ext), recursive=True)
    )
    skipped = [
        f
        for ext in _BINARY_EXTS
        for f in glob.glob(os.path.join(corpus, "**", ext), recursive=True)
    ]
    if skipped:
        from collections import Counter
        by_ext = Counter(os.path.splitext(f)[1].lstrip(".") for f in skipped)
        summary = ", ".join(f"{count} .{ext}" for ext, count in sorted(by_ext.items()))
        print(
            f"WARNING: corpus_docs skipped {len(skipped)} binary file(s) ({summary}). "
            f"Pre-process with `parse_corpus.py --corpus {corpus}` first.",
            file=sys.stderr,
        )
    return indexed

def cmd_index(a):
    c = connect(a.db)
    if a.reset:
        if _has(c, "chunks"):
            delete_docs(c, [row[0] for row in c.execute("SELECT DISTINCT source FROM chunks")])
        c.executescript("""DROP TABLE IF EXISTS chunks;
                         DROP TABLE IF EXISTS chunks_fts;
                         DROP TABLE IF EXISTS chunks_vec;
                         DROP TABLE IF EXISTS documents;""")
    _ensure_schema(c, a.dim)
    deletes = [s.strip() for s in (a.delete or "").split(",") if s.strip()]
    if deletes:
        delete_docs(c, deletes)
        print(f"deleted {len(deletes)} doc(s) from the index", file=sys.stderr)
    # which docs to (re)index: an explicit --docs subset, else the whole corpus (idempotent)
    sources = ([s.strip() for s in a.docs.split(",") if s.strip()] if a.docs
               else corpus_docs(a.corpus)) if a.corpus else []
    if a.corpus and not a.docs:
        present = set(corpus_docs(a.corpus))
        indexed = {r[0] for r in c.execute("SELECT source FROM documents")}
        delete_docs(c, sorted(indexed - present))
    n_chunks, n_docs, skipped = index_docs(c, a.model, a.corpus, sources, a.dim, a.max_chars) if sources else (0, 0, 0)
    c.commit()
    print(f"indexed {n_chunks} changed chunks from {n_docs} changed doc(s); skipped {skipped} unchanged doc(s) -> {a.db}", file=sys.stderr)

def _search_filter(as_of=None, latest_only=False, source_contains=None, tag=None, alias="c"):
    clauses, params = [], []
    if as_of:
        clauses.extend([f"({alias}.valid_from IS NULL OR {alias}.valid_from<=?)", f"({alias}.valid_to IS NULL OR {alias}.valid_to>?)"])
        params.extend([as_of, as_of])
    # With as_of, validity intervals define which version was active at that
    # event time. Current status would incorrectly hide historical versions.
    if latest_only and not as_of:
        clauses.append(f"COALESCE({alias}.status,'ACTIVE')='ACTIVE'")
    if source_contains:
        escaped = source_contains.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append(f"{alias}.source LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped}%")
    if tag:
        clauses.append(f"EXISTS (SELECT 1 FROM chunk_topics ct WHERE ct.chunk_id={alias}.id AND ct.category_label=?)")
        params.append(tag)
    return clauses, params

def search(c, model, query, k, as_of=None, latest_only=False, source_contains=None, tag=None):
    qv = embed(model, [query])[0]
    chunk_cols = {row[1] for row in c.execute("PRAGMA table_info(chunks)")}
    if as_of and not {"valid_from", "valid_to"}.issubset(chunk_cols):
        raise ValueError("as-of search requires re-indexing once to add temporal chunk metadata")
    if latest_only and "status" not in chunk_cols:
        raise ValueError("latest-only search requires re-indexing once to add chunk lifecycle metadata")
    if tag and not _has(c, "chunk_topics"):
        raise ValueError("tag filtering requires the chunk_topics classification layer")
    clauses, params = _search_filter(as_of, latest_only, source_contains, tag)
    where = (" AND " + " AND ".join(clauses)) if clauses else ""
    fts = [r[0] for r in c.execute(
        "SELECT chunks_fts.rowid FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid "
        "WHERE chunks_fts MATCH ?" + where + " ORDER BY bm25(chunks_fts) LIMIT ?",
        (fts_query(query), *params, POOL))]
    if clauses:
        vec = [r[0] for r in c.execute(
            "SELECT v.rowid FROM chunks_vec v JOIN chunks c ON c.id=v.rowid WHERE "
            + " AND ".join(clauses)
            + " ORDER BY vec_distance_cosine(v.embedding, ?) LIMIT ?",
            (*params, sqlite_vec.serialize_float32(qv), POOL))]
    else:
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
        optional_cols = ("event_date", "status", "parent_heading", "breadcrumb_path", "speaker", "valid_from", "valid_to")
        projection = ",".join(col if col in chunk_cols else f"NULL AS {col}" for col in optional_cols)
        row = c.execute(f"SELECT source,ord,title,text,{projection} FROM chunks WHERE id=?", (rid,)).fetchone()
        src, ordv, title, txt, event_date, status, parent, breadcrumb, speaker, valid_from, valid_to = row
        out.append({"id": rid, "score": round(score[rid], 5), "source": src, "ord": ordv,
                    "title": title, "text": txt, "event_date": event_date,
                    "status": status or "ACTIVE", "parent_heading": parent or "",
                    "breadcrumb_path": breadcrumb or "", "speaker": speaker or "",
                    "valid_from": valid_from, "valid_to": valid_to})
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
    res = search(connect(a.db), a.model, a.query, a.k, a.as_of, a.latest_only, a.source_contains, a.tag)
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

def cmd_temporal_load(a):
    from temporal_memory import load_ledger
    with open(a.ledger, encoding="utf-8") as handle:
        ledger = json.load(handle)
    con = sqlite3.connect(a.db)
    try:
        counts = load_ledger(con, ledger)
    finally:
        con.close()
    print(f"temporal memory: {counts} -> {a.db}", file=sys.stderr)

def cmd_supersede(a):
    c = connect(a.db); _ensure_schema(c, a.dim)
    cur = c.execute("UPDATE chunks SET status='SUPERSEDED', valid_to=? WHERE source=? AND status!='SUPERSEDED'", (a.valid_to, a.source))
    c.commit(); c.close()
    print(f"superseded {cur.rowcount} chunks from {a.source} at {a.valid_to}", file=sys.stderr)

def cmd_add_edge(a):
    edge_type = a.edge_type.upper()
    if edge_type not in {"ANSWERS", "SUPERSEDES", "REFERENCES", "CONTRADICTS"}:
        raise ValueError("edge type must be ANSWERS, SUPERSEDES, REFERENCES, or CONTRADICTS")
    c = connect(a.db); _ensure_related_schema(c)
    existing = {row[0] for row in c.execute(
        "SELECT id FROM chunks WHERE id IN (?,?)", (a.from_chunk, a.to_chunk)
    )}
    missing = {a.from_chunk, a.to_chunk} - existing
    if missing:
        c.close()
        raise ValueError(f"unknown chunk id(s): {sorted(missing)}")
    c.execute("INSERT OR REPLACE INTO related(chunk_id,related_id,score,edge_type,directed) VALUES(?,?,1.0,?,1)",
              (a.from_chunk, a.to_chunk, edge_type))
    c.commit(); c.close()
    print(f"edge: {a.from_chunk} -[{edge_type}]-> {a.to_chunk}", file=sys.stderr)

def benchmark_metrics(cases, ranked_sources):
    reciprocal_ranks, hits = [], 0
    details = []
    for case, sources in zip(cases, ranked_sources):
        expected = {s.strip() for s in case["expected_sources"].split("|") if s.strip()}
        rank = next((i for i, source in enumerate(sources, 1) if source in expected), None)
        hits += int(rank is not None)
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        details.append({"eval_id": case["eval_id"], "hit": rank is not None, "rank": rank, "expected_sources": sorted(expected), "returned_sources": sources})
    total = len(cases)
    return {"count": total, "hit_rate_at_k": hits / total if total else 0.0,
            "mrr": sum(reciprocal_ranks) / total if total else 0.0, "cases": details}

def cmd_benchmark(a):
    with open(a.evals, newline="", encoding="utf-8") as handle:
        cases = list(csv.DictReader(handle))
    c = connect(a.db)
    try:
        ranked = [
            [r["source"] for r in search(c, a.model, case["query"], a.k,
                case.get("as_of") or None,
                (case.get("latest_only") or "").lower() in {"1", "true", "yes"},
                case.get("source_contains") or None,
                case.get("tag") or None)["results"]]
            for case in cases
        ]
    finally:
        c.close()
    result = benchmark_metrics(cases, ranked)
    payload = json.dumps(result, indent=2)
    if a.output:
        with open(a.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("index", "search", "related", "temporal-load", "supersede", "add-edge", "benchmark"):
        p = sub.add_parser(name)
        p.add_argument("--db", required=True)
        if name not in {"temporal-load", "add-edge"}:
            p.add_argument("--model", default=DEFAULT_MODEL); p.add_argument("--dim", type=int, default=DEFAULT_DIM)
        if name == "index":
            p.add_argument("--corpus"); p.add_argument("--max-chars", type=int, default=1200); p.add_argument("--reset", action="store_true")
            p.add_argument("--docs", help="comma list of source relpaths to (re)index only (incremental)")
            p.add_argument("--delete", help="comma list of source relpaths to remove from the index")
        elif name == "search":
            p.add_argument("--query", required=True); p.add_argument("--k", type=int, default=8); p.add_argument("--json", action="store_true")
            p.add_argument("--as-of", help="event-time cutoff (ISO date or timestamp)")
            p.add_argument("--latest-only", action="store_true", help="exclude SUPERSEDED chunks")
            p.add_argument("--source-contains", help="safe literal source-path substring")
            p.add_argument("--tag", help="exact chunk_topics category label")
        elif name == "related":
            p.add_argument("--k", type=int, default=6, help="neighbors per chunk")
            p.add_argument("--min-score", type=float, default=0.55, help="cosine cutoff [0..1]")
            p.add_argument("--within-doc", action="store_true", help="also relate sections of the same doc")
        elif name == "temporal-load":
            p.add_argument("--ledger", required=True, help="schema_version 1.0 temporal-memory JSON")
        elif name == "supersede":
            p.add_argument("--source", required=True, help="exact source path to retire")
            p.add_argument("--valid-to", required=True, help="ISO event date/time when source stopped being current")
        elif name == "add-edge":
            p.add_argument("--from-chunk", required=True, type=int)
            p.add_argument("--to-chunk", required=True, type=int)
            p.add_argument("--edge-type", required=True)
        else:
            p.add_argument("--evals", required=True, help="CSV with eval_id,query,expected_sources")
            p.add_argument("--k", type=int, default=5)
            p.add_argument("--output", help="optional JSON report path")
    a = ap.parse_args()
    {"index": cmd_index, "search": cmd_search, "related": cmd_related,
     "temporal-load": cmd_temporal_load, "supersede": cmd_supersede,
     "add-edge": cmd_add_edge, "benchmark": cmd_benchmark}[a.cmd](a)
