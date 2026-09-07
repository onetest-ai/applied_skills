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

SKILLS = Path(os.environ.get("BRAIN_SKILLS") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(SKILLS / "knowledge-index"))

# ---- resolution (same discovery contract as the `brain` launcher) -------------
def _project_roots():
    yield Path.cwd()
    p = SKILLS.parent.parent          # <root> when skills live at <root>/.claude/skills
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
    return {"skills": str(SKILLS), "db": db, "catalog_metrics": sorted(resolve_catalog().keys())}

def t_search(query, k=5):
    import knowledge_index as K
    con = K.connect(resolve_db())
    res = K.search(con, K.DEFAULT_MODEL, query, int(k))
    out = [{"source": r["source"], "section": r.get("title") or "", "score": r["score"],
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
                tagged = [dict(zip(("chunk_id", "source", "section"), r)) for r in con.execute(
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
                "serverInfo": {"name": "brain", "version": "1.0.0"}}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (f, d, s) in TOOLS.items()]}
    if method == "tools/call":
        name = params.get("name"); args = params.get("arguments") or {}
        if name not in TOOLS:
            return {"content": [{"type": "text", "text": f"unknown tool '{name}'"}], "isError": True}
        try:
            return _text_result(TOOLS[name][0](**args))
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
