#!/usr/bin/env python3
"""brain — the MCP server: the deterministic TOOL layer of the local brain.

Deliberately dependency-free: MCP over **stdio** is just line-delimited JSON-RPC
2.0, so this is ~100 lines of stdlib — no SDK, no web framework. The point is to
*hide the scripts behind tools*, not to run a server. (`FastMCP`/`MCPServer` from
the `mcp` SDK would work too, but it's a heavy dep with a churning API for what is
a handful of stdio messages.)

Responsibility split — the whole reason this exists:
  • THIS server owns the venv + skills' code + store connection, and exposes
    cited/computed tools. It NEVER reasons or narrates.
  • The AGENT (client) decomposes the question, calls tools, composes one cited
    answer with honest gaps.

Truthfulness at the boundary:
  numbers are COMPUTED  → `sql` / `metric` (values from `facts`, each with source_file)
  meaning is CITED      → `search` (RAG) / `graph` (taxonomy); text hits, never figures

Launch (normally via the host MCP config, using the brain venv's python):
  python brain_mcp.py            # stdio
Env: BRAIN_DB (store), BRAIN_CATALOG (metrics.<corpus>.json), BRAIN_SKILLS.
"""
import json, os, re, sqlite3, sys
from pathlib import Path

def _coerce_cid(v):
    """Chunk ids are 63-bit int64; float64 JSON clients pass them back as strings to
    keep precision. Accept int or numeric string, else 0 (falls through to 'need id')."""
    if isinstance(v, bool):
        return 0
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return 0

def _resolve_skills():
    """Find the skills dir. Installed layout: <host>/mcp/brain/brain_mcp.py with
    skills at <host>/skills/ (siblings). Overridable by BRAIN_SKILLS; also tolerates
    the old skills/brain-mcp/ location."""
    if os.environ.get("BRAIN_SKILLS"):
        return Path(os.environ["BRAIN_SKILLS"])
    here = Path(__file__).resolve()
    for cand in (here.parents[2] / "skills",   # <host>/skills  (mcp/brain/ + skills/ siblings)
                 here.parent.parent):           # old layout: skills/brain-mcp/ -> skills/
        if (cand / "knowledge-index").is_dir():
            return cand
    return here.parents[2] / "skills"

SKILLS = _resolve_skills()
sys.path.insert(0, str(SKILLS / "knowledge-index"))
sys.path.insert(0, str(SKILLS / "corpus-taxonomy-extraction"))
try:
    from to_obsidian import note_path as _note_path   # shared vault naming
except Exception:
    _note_path = None

def vault_note(source, ordv, title):
    """Vault-relative note path for a chunk (for transparency — where it is in the vault,
    even if the vault isn't exported; regenerate with `./brain vault`)."""
    if _note_path is None or source is None:
        return None
    try:
        return _note_path(source, ordv or 0, title) + ".md"
    except Exception:
        return None

# ---- resolution (same discovery contract as the `brain` launcher) -------------
def _project_roots():
    yield Path.cwd()
    # <root> when skills are at <root>/.<host>/skills — go up from the skills dir
    p = SKILLS.parent.parent
    if p != Path.cwd():
        yield p

def resolve_db():
    if os.environ.get("BRAIN_DB"):
        return os.environ["BRAIN_DB"]
    for base in _project_roots():
        for c in (base / "knowledge.sqlite", base / "schema" / "knowledge.sqlite"):
            if c.exists():
                return str(c)
    raise RuntimeError("no store found — set BRAIN_DB or place knowledge.sqlite at the project root")

def resolve_assets():
    if os.environ.get("BRAIN_ASSETS"):
        return os.environ["BRAIN_ASSETS"]
    for base in _project_roots():
        d = base / "assets"
        if d.is_dir():
            return str(d)
    return str(next(_project_roots()))

def resolve_vault():
    """Where the vault is / would be (it's optional & regenerable)."""
    if os.environ.get("BRAIN_VAULT"):
        return os.environ["BRAIN_VAULT"]
    try:
        return os.path.join(os.path.dirname(resolve_db()), "vault")
    except Exception:
        return str(next(_project_roots()) / "vault")

def resolve_catalog():
    p = os.environ.get("BRAIN_CATALOG")
    if p and Path(p).exists():
        return json.load(open(p)).get("metrics", {})
    for base in _project_roots():
        for d in (base / "schema", base):
            if d.exists():
                for f in sorted(d.glob("metrics.*.json")):
                    return json.load(open(f)).get("metrics", {})
    return {}

def connect():
    con = sqlite3.connect(resolve_db())
    try:
        con.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(con)
    except Exception:
        pass
    return con

# ---- read-only SQL guard -----------------------------------------------------
_WRITE = re.compile(r"(?is)\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex)\b")
def _guard_select(q):
    s = q.strip().rstrip(";").strip()
    if ";" in s:
        raise ValueError("only a single statement is allowed")
    if not re.match(r"(?is)^(select|with)\b", s):
        raise ValueError("only read-only SELECT / WITH queries are allowed")
    if _WRITE.search(s):
        raise ValueError("write/DDL keywords are not allowed")
    return s

# ---- tools (plain functions returning JSON-able values) ----------------------
def t_which():
    try:
        db = resolve_db()
    except Exception as e:
        db = f"<none: {e}>"
    vault = resolve_vault()
    return {"skills": str(SKILLS), "db": db, "assets": resolve_assets(),
            "vault": vault, "vault_exists": os.path.isdir(vault),
            "vault_note": "each search/related/page hit carries a `note` = its vault path; run `./brain vault` to export",
            "catalog_metrics": sorted(resolve_catalog().keys())}

def t_search(query, k=5):
    import knowledge_index as K
    con = K.connect(resolve_db())
    res = K.search(con, K.DEFAULT_MODEL, query, int(k))
    out = [{"source": r["source"], "section": r.get("title") or "", "score": r["score"],
            "note": vault_note(r["source"], r.get("ord"), r.get("title")),   # where this hit lives in the vault
            "text": " ".join((r["text"] or "").split())} for r in res["results"]]
    con.close()
    return out

def t_sql(query):
    s = _guard_select(query)
    con = connect()
    try:
        cur = con.execute(s)
        cols = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchall()]
    finally:
        con.close()
    return {"columns": cols, "rows": rows}

def t_metric(name, grain="", entity="", entity_like="", month="", months=""):
    cat = resolve_catalog()
    if name not in cat:
        return {"error": f"unknown metric '{name}'", "available": sorted(cat.keys())}
    spec = cat[name]
    clauses, params = ["family = ?", "metric = ?"], [spec["family"], spec["metric"]]
    if grain:       clauses.append("grain = ?");            params.append(grain)
    if entity:      clauses.append("entity = ?");           params.append(entity)
    if entity_like: clauses.append("lower(entity) LIKE ?"); params.append(f"%{entity_like.lower()}%")
    if month:       clauses.append("month = ?");            params.append(month)
    if months:
        ms = [m.strip() for m in months.split(",") if m.strip()]
        clauses.append("month IN (" + ",".join("?" * len(ms)) + ")"); params += ms
    q = ("SELECT entity, month, value, source_file FROM facts WHERE "
         + " AND ".join(clauses) + " ORDER BY entity, month")
    con = connect()
    try:
        rows = [dict(zip(("entity", "month", "value", "source_file"), r))
                for r in con.execute(q, params).fetchall()]
    finally:
        con.close()
    return {"metric": name, "family": spec["family"], "unit": spec.get("unit"), "rows": rows}

def t_related(chunk_id=0, query="", k=6):
    """SEMANTIC NEIGHBORS (cited): sections most similar by meaning (cosine kNN over the
    same vectors RAG uses — full 384-dim, not a lossy projection). Give a `chunk_id`, or a
    `query` (its top search hit is the anchor). Returns [{chunk_id, source, section, score}]
    — cross-document links you can cite; still text/relations, never a number."""
    con = connect()
    try:
        chunk_id = _coerce_cid(chunk_id)
        if not query and not chunk_id:
            return {"error": "give chunk_id or query"}
        if query and not chunk_id:
            import knowledge_index as K
            res = K.search(K.connect(resolve_db()), K.DEFAULT_MODEL, query, 1)
            if not res["results"]:
                return {"anchor": None, "related": []}
            chunk_id = res["results"][0]["id"]
        if not con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='related'").fetchone():
            return {"error": "no related layer — run: knowledge_index.py related --db <db>"}
        rows = con.execute(
            "SELECT related_id, score FROM related WHERE chunk_id=? "
            "UNION SELECT chunk_id, score FROM related WHERE related_id=? ORDER BY score DESC LIMIT ?",
            (chunk_id, chunk_id, int(k))).fetchall()
        out = []
        for rid, sc in rows:
            row = con.execute("SELECT source, ord, title FROM chunks WHERE id=?", (rid,)).fetchone()
            if row:
                out.append({"chunk_id": str(rid), "source": row[0], "section": row[2] or "", "score": sc,
                            "note": vault_note(row[0], row[1], row[2])})
        a = con.execute("SELECT source, ord, title FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        return {"anchor": ({"chunk_id": str(chunk_id), "source": a[0], "section": a[2] or "",
                            "note": vault_note(a[0], a[1], a[2])} if a else None),
                "related": out}
    finally:
        con.close()

def t_graph(label="", relation="", kind=""):
    con = connect()
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "graph_nodes" not in tables:
            return {"error": "no graph in this store"}
        if label:
            node = con.execute("SELECT id,label,kind FROM graph_nodes WHERE label=? OR id=? LIMIT 1",
                               (label, label)).fetchone()
            if not node:
                return {"error": f"no node '{label}'"}
            nid = node[0]
            subs = [dict(zip(("id", "label"), r)) for r in con.execute(
                "SELECT n.id,n.label FROM graph_edges e JOIN graph_nodes n ON n.id=e.source "
                "WHERE e.rel='subclass_of' AND e.target=?", (nid,)).fetchall()]
            tagged = []
            if "chunk_topics" in tables:
                tagged = [{"chunk_id": str(r[0]), "source": r[1], "section": r[2]} for r in con.execute(
                    "SELECT c.id,c.source,c.title FROM chunk_topics t JOIN chunks c ON c.id=t.chunk_id "
                    "WHERE t.category_label=? LIMIT 50", (node[1],)).fetchall()]
            return {"node": dict(zip(("id", "label", "kind"), node)), "subclasses": subs, "tagged_sections": tagged}
        if relation:
            edges = [dict(zip(("source", "rel", "target"), r)) for r in con.execute(
                "SELECT source,rel,target FROM graph_edges WHERE rel=? LIMIT 500", (relation,)).fetchall()]
            return {"edges": edges}
        q = "SELECT id,label,kind FROM graph_nodes" + (" WHERE kind=?" if kind else "")
        nodes = [dict(zip(("id", "label", "kind"), r))
                 for r in con.execute(q, (kind,) if kind else ()).fetchall()]
        return {"nodes": nodes}
    finally:
        con.close()

def t_page(chunk_id=0, query=""):
    """FULL PAGE CONTENT for ANSWERING (not just finding). Retrieval runs on the
    semantic transcription, which is LOSSY — so before you answer from a visual/table
    page, pull its full content here: the rendered IMAGE + the verbatim TEXT layer +
    any DETERMINISTICALLY-EXTRACTED TABLE grids (cite these for figures, never the
    prose paraphrase). Anchor by chunk_id or query. Returns image + text blocks."""
    import base64, mimetypes
    con = connect()
    try:
        chunk_id = _coerce_cid(chunk_id)
        if query and not chunk_id:
            import knowledge_index as K
            res = K.search(K.connect(resolve_db()), K.DEFAULT_MODEL, query, 1)
            chunk_id = res["results"][0]["id"] if res["results"] else 0
        row = con.execute("SELECT source, title, image, ord FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        if not row or not row[2]:
            return {"error": "no image for this chunk (not a visual page)"}
        img_rel = row[2]
        path = img_rel if os.path.isabs(img_rel) else os.path.join(resolve_assets(), img_rel)
        if not os.path.exists(path):
            return {"error": f"image not found: {path}"}
        base = os.path.splitext(path)[0]                       # sibling raw text + tables
        raw_text = open(base + ".txt").read() if os.path.exists(base + ".txt") else ""
        tables = open(base + ".tables.md").read() if os.path.exists(base + ".tables.md") else ""
        data = base64.b64encode(open(path, "rb").read()).decode()
        mime = mimetypes.guess_type(path)[0] or "image/png"
        meta = {"chunk_id": str(chunk_id), "source": row[0], "section": row[1], "image": img_rel,
                "note": vault_note(row[0], row[3], row[1]), "has_tables": bool(tables)}
        blocks = [{"type": "image", "data": data, "mimeType": mime}]
        if tables:
            blocks.append({"type": "text", "text": "EXTRACTED TABLES (verbatim cells — cite for numbers):\n" + tables})
        if raw_text:
            blocks.append({"type": "text", "text": "VERBATIM PAGE TEXT:\n" + raw_text})
        blocks.append({"type": "text", "text": json.dumps(meta, default=str)})
        return {"content": blocks}
    finally:
        con.close()

def t_verify():
    con = connect()
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        def n(t):
            if t not in tables: return None
            try: return con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception: return "?"
        lanes = {"narrative": ["chunks", "chunks_fts", "chunks_vec"],
                 "taxonomy": ["graph_nodes", "graph_edges", "chunk_topics"],
                 "numbers": ["facts"]}
        counts = {lane: {t: n(t) for t in ts} for lane, ts in lanes.items()}
        empty = [lane for lane, ts in lanes.items() if not counts[lane].get(ts[0])]
        return {"db": resolve_db(), "counts": counts, "empty_lanes": empty, "ok": not empty}
    finally:
        con.close()

# name -> (fn, description, JSON-Schema of arguments)
TOOLS = {
    "which":  (t_which, "Resolved store / catalog / skills. Call first if unsure.",
               {"type": "object", "properties": {}}),
    "search": (t_search, "NARRATIVE lane (cited): hybrid RAG (BM25+vector, RRF). Cited section hits — never for numbers.",
               {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer", "default": 5}},
                "required": ["query"]}),
    "sql":    (t_sql, "NUMBERS lane (computed): read-only SELECT/WITH over the store (e.g. facts). {columns, rows}.",
               {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
    "metric": (t_metric, "NUMBERS lane (governed): a catalog metric's exact values from facts, with source_file.",
               {"type": "object", "properties": {"name": {"type": "string"}, "grain": {"type": "string"},
                "entity": {"type": "string"}, "entity_like": {"type": "string"},
                "month": {"type": "string"}, "months": {"type": "string"}}, "required": ["name"]}),
    "graph":  (t_graph, "TAXONOMY lane (cited): a node + subclasses + tagged sections, or node/edge listings.",
               {"type": "object", "properties": {"label": {"type": "string"}, "relation": {"type": "string"},
                "kind": {"type": "string"}}}),
    "related": (t_related, "SEMANTIC NEIGHBORS (cited): sections nearest by meaning (cosine kNN over the RAG vectors), cross-doc. Anchor by chunk_id or query.",
               {"type": "object", "properties": {"chunk_id": {"type": "integer"}, "query": {"type": "string"},
                "k": {"type": "integer", "default": 6}}}),
    "page":   (t_page, "VISUAL PAGE: the rendered slide/page IMAGE for a chunk (diagram/flow/timeline) when layout matters. Anchor by chunk_id or query.",
               {"type": "object", "properties": {"chunk_id": {"type": "integer"}, "query": {"type": "string"}}}),
    "verify": (t_verify, "Store health: per-lane row counts + empty-lane flag.",
               {"type": "object", "properties": {}}),
}

# ---- minimal stdio JSON-RPC 2.0 loop (MCP) -----------------------------------
def _text_result(obj):
    return {"content": [{"type": "text", "text": json.dumps(obj, default=str, ensure_ascii=False, indent=2)}]}

def _dispatch(method, params):
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "brain", "version": "1.1.0"}}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (f, d, s) in TOOLS.items()]}
    if method == "tools/call":
        name = params.get("name"); args = params.get("arguments") or {}
        if name not in TOOLS:
            return {"content": [{"type": "text", "text": f"unknown tool '{name}'"}], "isError": True}
        try:
            r = TOOLS[name][0](**args)
            # a tool may return pre-formed MCP content (e.g. an image block); pass it through
            if isinstance(r, dict) and isinstance(r.get("content"), list):
                return r
            return _text_result(r)
        except Exception as e:
            return {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
    if method == "ping":
        return {}
    raise KeyError(f"method not found: {method}")

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        if "id" not in req:            # notification (e.g. notifications/initialized) — no reply
            continue
        try:
            resp = {"jsonrpc": "2.0", "id": req["id"], "result": _dispatch(req.get("method"), req.get("params") or {})}
        except KeyError as e:
            resp = {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32601, "message": str(e)}}
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32603, "message": str(e)}}
        sys.stdout.write(json.dumps(resp) + "\n"); sys.stdout.flush()

if __name__ == "__main__":
    main()
