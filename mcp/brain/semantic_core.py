"""Safe semantic operations over the private knowledge.sqlite store.

This module contains no MCP transport code.  It is deliberately reusable from tests,
FastMCP, or another host.  The public surface never accepts SQL.
"""
from __future__ import annotations

import inspect
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def _project_roots():
    """Yield likely project roots for all supported host layouts."""
    seen: set[Path] = set()
    # Installed layout: <root>/.<host>/mcp/brain/semantic_core.py.
    installed_root = HERE.parents[2] if len(HERE.parents) >= 3 else None
    for root in (Path.cwd(), installed_root):
        if root is not None:
            root = root.resolve()
            if root not in seen:
                seen.add(root)
                yield root


def resolve_skills() -> Path:
    configured = os.getenv("BRAIN_SKILLS")
    if configured:
        return Path(configured).expanduser().resolve()
    for root in _project_roots():
        for host_dir in (".dsh", ".claude", ".codex", ".github"):
            candidate = root / host_dir / "skills"
            if (candidate / "knowledge-index").is_dir():
                return candidate
    sibling = HERE.parents[1] / "skills"
    if (sibling / "knowledge-index").is_dir():
        return sibling
    raise RuntimeError("Cannot locate skills; set BRAIN_SKILLS")


def resolve_db() -> Path:
    configured = os.getenv("BRAIN_DB")
    if configured:
        path = Path(configured).expanduser().resolve()
        if path.is_file():
            return path
        raise RuntimeError(f"BRAIN_DB does not exist: {path}")
    for root in _project_roots():
        for candidate in (
            root / ".dsh" / "knowledge.sqlite",
            root / "knowledge.sqlite",
            root / "schema" / "knowledge.sqlite",
        ):
            if candidate.is_file():
                return candidate.resolve()
    raise RuntimeError("Cannot locate knowledge.sqlite; set BRAIN_DB")


def resolve_catalog_path() -> Path:
    configured = os.getenv("BRAIN_CATALOG")
    if configured:
        path = Path(configured).expanduser().resolve()
        if path.is_file():
            return path
        raise RuntimeError(f"BRAIN_CATALOG does not exist: {path}")
    for root in _project_roots():
        candidates = sorted((root / "schema").glob("metrics.*.json")) if (root / "schema").is_dir() else []
        if candidates:
            return candidates[0].resolve()
    raise RuntimeError("Cannot locate metrics catalog; set BRAIN_CATALOG")


def resolve_assets() -> Path:
    configured = os.getenv("BRAIN_ASSETS")
    if configured:
        return Path(configured).expanduser().resolve()
    for root in _project_roots():
        for host_dir in (".dsh", ".claude", ".codex", ".github"):
            candidate = root / host_dir / "assets"
            if candidate.is_dir():
                return candidate.resolve()
        candidate = root / "assets"
        if candidate.is_dir():
            return candidate.resolve()
    return resolve_db().parent / "assets"


def _readonly_connection(*, vectors: bool = False) -> sqlite3.Connection:
    db = resolve_db()
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA busy_timeout=5000")
    if vectors:
        enable_extension = getattr(con, "enable_load_extension", None)
        if enable_extension is None:
            con.close()
            raise RuntimeError("Python sqlite3 was built without extension loading support")
        try:
            enable_extension(True)
            import sqlite_vec

            sqlite_vec.load(con)
            enable_extension(False)
        except Exception:
            con.close()
            raise
    return con


def _present_tables(con: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
    }


def _catalog() -> dict[str, dict[str, Any]]:
    with resolve_catalog_path().open(encoding="utf-8") as handle:
        return json.load(handle).get("metrics", {})


def _bounded_limit(limit: int) -> int:
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


def _coerce_chunk_id(value: Any) -> int:
    """Accept a chunk_id as an int OR a numeric string and return the exact int.

    Chunk ids are 63-bit int64 values; float64 JSON clients must send them back as
    strings to preserve precision. bool is rejected (isinstance(True, int) is True)."""
    if isinstance(value, bool):
        raise ValueError("chunk_id must be an integer id, not a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise ValueError("chunk_id must be an integer or a numeric string")


def _as_chunk_id_str(value: Any) -> Any:
    """Render a chunk id as a string for JSON output; pass through None/blank."""
    return str(value) if value is not None else value


def _metric_spec(name: str) -> dict[str, Any]:
    catalog = _catalog()
    if name not in catalog:
        raise ValueError(f"Unknown metric '{name}'. Call list_metrics to discover valid names.")
    return catalog[name]


def list_metrics() -> dict[str, Any]:
    """List governed business metrics and dimensions actually available in facts."""
    catalog = _catalog()
    with _readonly_connection() as con:
        availability = {
            (row["family"], row["metric"]): {
                "grains": sorted(filter(None, (row["grains"] or "").split(","))),
                "first_month": row["first_month"],
                "last_month": row["last_month"],
                "row_count": row["row_count"],
            }
            for row in con.execute(
                """SELECT family, metric,
                          group_concat(DISTINCT grain) AS grains,
                          MIN(month) AS first_month, MAX(month) AS last_month,
                          COUNT(*) AS row_count
                   FROM facts GROUP BY family, metric"""
            )
        }
    metrics = []
    for name, spec in sorted(catalog.items()):
        available = availability.get((spec["family"], spec["metric"]), {})
        grains = available.get("grains", [])
        metrics.append(
            {
                "name": name,
                "description": spec.get("desc"),
                "definition": spec.get("definition") or spec.get("provenance"),
                "unit": spec.get("unit"),
                "catalog_grain": spec.get("grain"),
                **available,
                "grains": grains,
            }
        )
    return {"metrics": metrics, "count": len(metrics)}


def get_metric(
    name: str,
    grain: str | None = None,
    entity: str | None = None,
    entity_contains: str | None = None,
    month: str | None = None,
    start_month: str | None = None,
    end_month: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Return exact governed fact rows with source citations; no aggregation or guessed values."""
    limit = _bounded_limit(limit)
    spec = _metric_spec(name)
    clauses = ["family = ?", "metric = ?"]
    params: list[Any] = [spec["family"], spec["metric"]]
    filters: dict[str, Any] = {}
    for column, value in (("grain", grain), ("entity", entity), ("month", month)):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
            filters[column] = value
    if entity_contains:
        clauses.append("lower(entity) LIKE ? ESCAPE '\\'")
        escaped = entity_contains.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
        filters["entity_contains"] = entity_contains
    if start_month:
        clauses.append("month >= ?")
        params.append(start_month)
        filters["start_month"] = start_month
    if end_month:
        clauses.append("month <= ?")
        params.append(end_month)
        filters["end_month"] = end_month
    params.append(limit + 1)
    query = (
        "SELECT grain, entity, month, value, source_file FROM facts WHERE "
        + " AND ".join(clauses)
        + " ORDER BY month, grain, entity LIMIT ?"
    )
    with _readonly_connection() as con:
        rows = [dict(row) for row in con.execute(query, params)]
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {
        "status": "ok" if rows else "not_modeled",
        "metric": name,
        "family": spec["family"],
        "unit": spec.get("unit"),
        "description": spec.get("desc"),
        "definition": spec.get("definition") or spec.get("provenance"),
        "filters": filters,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "guidance": "Every value is computed from facts; cite source_file and state grain/entity/month.",
    }


def search_knowledge(
    query: str, limit: int = 5, as_of: str | None = None,
    latest_only: bool = False, source_contains: str | None = None,
    tag: str | None = None, tag_boost: str | None = None,
) -> dict[str, Any]:
    """Hybrid BM25+vector retrieval for narrative evidence, never authoritative figures."""
    limit = _bounded_limit(limit)
    if not query.strip():
        raise ValueError("query must not be empty")
    skills = resolve_skills()
    index_dir = str(skills / "knowledge-index")
    if index_dir not in sys.path:
        sys.path.insert(0, index_dir)
    import knowledge_index as knowledge

    # Filters (as_of/latest_only/source_contains/tag/tag_boost) need the extended
    # knowledge.search signature. An older bundled knowledge-index only accepts
    # (con, model, query, limit); calling it with filters would raise an opaque arity
    # TypeError. Detect capability by parameter name and fail with a clear, actionable
    # message instead — this is a real "can't do that here", not an internal crash.
    requested = [n for n, v in (
        ("as_of", as_of), ("latest_only", latest_only), ("source_contains", source_contains),
        ("tag", tag), ("tag_boost", tag_boost),
    ) if v]
    supports_filters = {"as_of", "source_contains", "tag"}.issubset(
        inspect.signature(knowledge.search).parameters
    )
    if requested and not supports_filters:
        raise ValueError(
            "This Brain's index does not support search filters "
            f"({', '.join(requested)}); omit them, or rebuild the store with a knowledge-index "
            "version that supports filtered search."
        )
    with _readonly_connection(vectors=True) as con:
        if requested:
            result = knowledge.search(con, knowledge.DEFAULT_MODEL, query, limit, as_of, latest_only, source_contains, tag, tag_boost)
        else:
            result = knowledge.search(con, knowledge.DEFAULT_MODEL, query, limit)
    hits = [
        {
            # chunk_id is a 63-bit int64 (sha256-derived). Emit it as a STRING so
            # float64 JSON clients (e.g. Cowork) don't round it past 2**53 and then
            # fail to round-trip it into get_evidence / find_related_content.
            "chunk_id": str(row["id"]),
            "source": row["source"],
            "section": row.get("title") or "",
            "score": row["score"],
            "text": " ".join((row.get("text") or "").split()),
            "event_date": row.get("event_date"),
            "status": row.get("status"),
            "breadcrumb_path": row.get("breadcrumb_path") or "",
            "speaker": row.get("speaker") or "",
        }
        for row in result["results"]
    ]
    return {
        "query": query,
        "hits": hits,
        "count": len(hits),
        "guidance": "Narrative evidence only. Use get_metric for every numeric claim.",
    }


def _temporal_operation(name: str, *args: Any) -> dict[str, Any]:
    skills = resolve_skills()
    index_dir = str(skills / "knowledge-index")
    if index_dir not in sys.path:
        sys.path.insert(0, index_dir)
    import temporal_memory

    with _readonly_connection() as con:
        required = {"memory_assertions", "memory_assertion_links", "memory_questions", "memory_answers"}
        present = _present_tables(con)
        if not required.issubset(present):
            return {"status": "not_modeled", "missing_tables": sorted(required - present)}
        return getattr(temporal_memory, name)(con, *args)


def get_current_fact(entity: str, predicate: str, as_of: str | None = None) -> dict[str, Any]:
    """Return the explicit current/as-of value, its history, and every source citation."""
    if not entity.strip() or not predicate.strip():
        raise ValueError("entity and predicate must not be empty")
    return _temporal_operation("current_fact", entity, predicate, as_of)


def get_question_status(question_id: str, as_of: str | None = None) -> dict[str, Any]:
    """Return open/resolved state for a stable question and cite question plus answer sessions."""
    if not question_id.strip():
        raise ValueError("question_id must not be empty")
    return _temporal_operation("question_status", question_id, as_of)


def get_taxonomy(
    label: str | None = None,
    relation: str | None = None,
    kind: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Explore governed taxonomy nodes, edges, subclasses, and tagged source sections."""
    limit = _bounded_limit(limit)
    with _readonly_connection() as con:
        present = _present_tables(con)
        required = {"graph_nodes", "graph_edges"}
        if not required.issubset(present):
            return {"status": "not_modeled", "missing_tables": sorted(required - present)}
        # Detect if description column exists in graph_nodes
        has_description = "description" in {r[1] for r in con.execute("PRAGMA table_info(graph_nodes)")}
        cols = "id, label, kind, parent" + (", description" if has_description else "")
        if label:
            node = con.execute(
                f"SELECT {cols} FROM graph_nodes WHERE lower(label)=lower(?) OR id=? LIMIT 1",
                (label, label),
            ).fetchone()
            if not node:
                return {"status": "not_modeled", "label": label, "node": None}
            children = [
                dict(row)
                for row in con.execute(
                    """SELECT n.id, n.label, n.kind FROM graph_edges e
                       JOIN graph_nodes n ON n.id=e.source
                       WHERE e.rel='subclass_of' AND e.target=? LIMIT ?""",
                    (node["id"], limit),
                )
            ]
            sections = []
            if {"chunk_topics", "chunks"}.issubset(present):
                sections = [
                    {**dict(row), "chunk_id": _as_chunk_id_str(row["chunk_id"])}
                    for row in con.execute(
                        """SELECT c.id AS chunk_id, c.source, c.title AS section
                           FROM chunk_topics t JOIN chunks c ON c.id=t.chunk_id
                           WHERE t.category_label=? LIMIT ?""",
                        (node["label"], limit),
                    )
                ]
            return {"status": "ok", "node": dict(node), "subclasses": children, "tagged_sections": sections}
        if relation:
            rows = [
                dict(row)
                for row in con.execute(
                    "SELECT source, rel AS relation, target FROM graph_edges WHERE rel=? LIMIT ?",
                    (relation, limit + 1),
                )
            ]
            truncated = len(rows) > limit
            return {"status": "ok" if rows else "not_modeled", "edges": rows[:limit], "truncated": truncated}
        clauses, params = [], []
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        params.append(limit + 1)
        sql = f"SELECT {cols} FROM graph_nodes"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY kind, label LIMIT ?"
        nodes = [dict(row) for row in con.execute(sql, params)]
        truncated = len(nodes) > limit
        return {"status": "ok" if nodes else "not_modeled", "nodes": nodes[:limit], "truncated": truncated}


def find_related_content(
    chunk_id: int | None = None, query: str | None = None, limit: int = 6
) -> dict[str, Any]:
    """Return precomputed semantic neighbors for a source section."""
    limit = _bounded_limit(limit)
    if chunk_id is not None:
        chunk_id = _coerce_chunk_id(chunk_id)
    if not chunk_id and not query:
        raise ValueError("Provide chunk_id or query")
    if not chunk_id:
        hits = search_knowledge(query or "", 1)["hits"]
        if not hits:
            return {"status": "not_modeled", "anchor": None, "related": []}
        chunk_id = _coerce_chunk_id(hits[0]["chunk_id"])
    with _readonly_connection() as con:
        present = _present_tables(con)
        required = {"chunks", "related"}
        if not required.issubset(present):
            return {"status": "not_modeled", "missing_tables": sorted(required - present), "anchor": None, "related": []}
        anchor = con.execute(
            "SELECT id AS chunk_id, source, title AS section FROM chunks WHERE id=?", (chunk_id,)
        ).fetchone()
        related_cols = {row["name"] for row in con.execute("PRAGMA table_info(related)")}
        if {"edge_type", "directed"}.issubset(related_cols):
            rows = [dict(row) for row in con.execute(
                """SELECT c.id AS chunk_id, c.source, c.title AS section, r.score,
                          r.edge_type, r.direction
                   FROM (
                     SELECT related_id AS id, score, edge_type,
                            CASE WHEN directed=1 THEN 'outgoing' ELSE 'undirected' END AS direction
                       FROM related WHERE chunk_id=?
                     UNION ALL
                     SELECT chunk_id AS id, score, edge_type,
                            CASE WHEN directed=1 THEN 'incoming' ELSE 'undirected' END AS direction
                       FROM related WHERE related_id=?
                   ) r JOIN chunks c ON c.id=r.id
                   ORDER BY CASE r.edge_type WHEN 'SIMILAR' THEN 1 ELSE 0 END,
                            r.score DESC LIMIT ?""",
                    (chunk_id, chunk_id, limit),
                )]
        else:
            rows = [dict(row) for row in con.execute(
                """SELECT c.id AS chunk_id, c.source, c.title AS section, r.score
                   FROM (
                     SELECT related_id AS id, score FROM related WHERE chunk_id=?
                     UNION ALL
                     SELECT chunk_id AS id, score FROM related WHERE related_id=?
                   ) r JOIN chunks c ON c.id=r.id ORDER BY r.score DESC LIMIT ?""",
                (chunk_id, chunk_id, limit),
            )]
            for row in rows:
                row.update(edge_type="SIMILAR", direction="undirected")
    for row in rows:
        row["chunk_id"] = _as_chunk_id_str(row["chunk_id"])
    anchor_out = None
    if anchor:
        anchor_out = {**dict(anchor), "chunk_id": _as_chunk_id_str(anchor["chunk_id"])}
    return {"status": "ok" if anchor else "not_modeled", "anchor": anchor_out, "related": rows}


def get_evidence(chunk_id: int | str, include_page_text: bool = True) -> dict[str, Any]:
    """Return a cited section and optional verbatim page/table text, but not binary images."""
    chunk_id = _coerce_chunk_id(chunk_id)
    with _readonly_connection() as con:
        row = con.execute(
            "SELECT id AS chunk_id, source, ord, title AS section, text, image FROM chunks WHERE id=?",
            (chunk_id,),
        ).fetchone()
    if not row:
        return {"status": "not_modeled", "chunk_id": _as_chunk_id_str(chunk_id)}
    result = dict(row)
    result["chunk_id"] = _as_chunk_id_str(result["chunk_id"])
    result["status"] = "ok"
    image = result.pop("image")
    result["page_asset"] = image
    if image and include_page_text:
        assets = resolve_assets().resolve()
        path = Path(image).resolve() if Path(image).is_absolute() else (assets / image).resolve()
        if path != assets and assets not in path.parents:
            raise PermissionError("Page asset resolves outside the configured assets directory")
        base = path.with_suffix("")
        text_path = base.with_suffix(".txt")
        tables_path = base.with_suffix(".tables.md")
        result["verbatim_page_text"] = text_path.read_text(encoding="utf-8") if text_path.is_file() else ""
        result["extracted_tables"] = tables_path.read_text(encoding="utf-8") if tables_path.is_file() else ""
    result["guidance"] = "Use this source citation for narrative; tabular figures require verbatim tables or get_metric."
    return result


def _read_about(con: sqlite3.Connection) -> dict[str, str]:
    """Read name + goal + audience from the durable `meta(key,value)` table. Degrades
    gracefully: a store built before this change (no meta table), an empty meta, or a
    store that predates `name` yields empty strings, never an error."""
    about = {"name": "", "goal": "", "audience": ""}
    if "meta" not in _present_tables(con):
        return about
    try:
        for row in con.execute(
                "SELECT key, value FROM meta WHERE key IN ('name','goal','audience')"):
            if row["key"] in about:
                about[row["key"]] = row["value"] or ""
    except sqlite3.Error:
        return {"name": "", "goal": "", "audience": ""}
    return about


def health() -> dict[str, Any]:
    """Return deployment and store health without exposing local filesystem paths.

    Includes an `about: {name, goal, audience}` block read from the durable `meta` table so
    consumers (e.g. the kb plugin) can tune answer altitude/artifact style; empty strings
    when the store predates the meta table or has no values recorded."""
    tables = ("chunks", "chunks_fts", "chunks_vec", "graph_nodes", "graph_edges", "chunk_topics", "facts")
    vector_extension = "available"
    try:
        con = _readonly_connection(vectors=True)
    except Exception as exc:
        vector_extension = f"unavailable: {type(exc).__name__}"
        con = _readonly_connection()
    with con:
        present = _present_tables(con)
        counts = {table: con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] if table in present else None for table in tables}
        quick_check = con.execute("PRAGMA quick_check").fetchone()[0]
        about = _read_about(con)
    empty_lanes = []
    if not counts.get("chunks"):
        empty_lanes.append("narrative")
    if not counts.get("graph_nodes"):
        empty_lanes.append("taxonomy")
    if not counts.get("facts"):
        empty_lanes.append("numbers")
    return {
        "status": "healthy" if quick_check == "ok" and not empty_lanes and vector_extension == "available" else "degraded",
        "database_check": quick_check,
        "vector_extension": vector_extension,
        "counts": counts,
        "empty_lanes": empty_lanes,
        "knowledge_version": os.getenv("BRAIN_KNOWLEDGE_VERSION", "unversioned"),
        "about": about,
    }
