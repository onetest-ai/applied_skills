from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import semantic_core as core

try:
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
except ImportError:
    Client = StdioTransport = None


def build_fixture(root: Path) -> dict[str, Path]:
    db = root / "knowledge.sqlite"
    catalog = root / "metrics.test.json"
    assets = root / "assets"
    skills = root / "skills"
    index = skills / "knowledge-index"
    assets.mkdir()
    index.mkdir(parents=True)
    with sqlite3.connect(db) as con:
        con.executescript(
            """
            CREATE TABLE facts(family TEXT, metric TEXT, grain TEXT, entity TEXT, month TEXT, value REAL, source_file TEXT);
            CREATE TABLE chunks(id INTEGER PRIMARY KEY, source TEXT, ord INT, title TEXT, text TEXT, sha TEXT, image TEXT);
            CREATE TABLE chunks_fts(text TEXT);
            CREATE TABLE chunks_vec(embedding BLOB);
            CREATE TABLE graph_nodes(id TEXT PRIMARY KEY, label TEXT, kind TEXT, parent TEXT);
            CREATE TABLE graph_edges(source TEXT, target TEXT, rel TEXT);
            CREATE TABLE chunk_topics(chunk_id INT, category_id TEXT, category_label TEXT, kind TEXT);
            CREATE TABLE related(chunk_id INT, related_id INT, score REAL);
            """
        )
        con.executemany("INSERT INTO facts VALUES(?,?,?,?,?,?,?)", [
            ("commercial", "revenue", "overall", "All", "2024-01", 100.0, "report.xlsx"),
            ("commercial", "revenue", "region", "North", "2024-01", 40.0, "report.xlsx"),
            ("commercial", "revenue", "region", "N%orth_\\HQ", "2024-02", 45.0, "report.xlsx"),
        ])
        con.executemany("INSERT INTO chunks VALUES(?,?,?,?,?,?,?)", [
            (1, "a.md", 0, "Overview", "Alpha evidence", "a", "page1.png"),
            (2, "b.md", 0, "Details", "Beta evidence", "b", None),
            (3, "c.md", 0, None, "Gamma evidence", "c", None),
        ])
        con.executemany("INSERT INTO graph_nodes VALUES(?,?,?,?)", [
            ("sales", "Sales", "topic", None),
            ("retail", "Retail", "segment", "sales"),
            ("ops", "Operations", "topic", None),
        ])
        con.executemany("INSERT INTO graph_edges VALUES(?,?,?)", [
            ("retail", "sales", "subclass_of"),
            ("sales", "ops", "related_to"),
        ])
        con.execute("INSERT INTO chunk_topics VALUES(?,?,?,?)", (1, "sales", "Sales", "topic"))
        con.executemany("INSERT INTO related VALUES(?,?,?)", [(1, 2, 0.9), (3, 1, 0.8)])
        con.executemany("INSERT INTO chunks_fts VALUES(?)", [("a",), ("b",), ("c",)])
        con.executemany("INSERT INTO chunks_vec VALUES(?)", [(b"x",), (b"y",), (b"z",)])
    catalog.write_text(json.dumps({"metrics": {
        "revenue": {"family": "commercial", "metric": "revenue", "desc": "Net revenue", "unit": "USD", "grain": ["overall", "region"]},
        "dormant": {"family": "commercial", "metric": "missing", "desc": "No rows", "unit": "count", "grain": ["overall"]},
    }}), encoding="utf-8")
    (assets / "page1.txt").write_text("VERBATIM PAGE", encoding="utf-8")
    (assets / "page1.tables.md").write_text("|A|B|\n|-|-|\n|1|2|", encoding="utf-8")
    (root / "sqlite_vec.py").write_text("def load(con):\n    return None\n", encoding="utf-8")
    (index / "knowledge_index.py").write_text(
        'DEFAULT_MODEL="fake"\ndef search(con, model, query, limit):\n return {"results":[{"id":1,"source":"a.md","title":"Overview","score":0.75,"text":"  Alpha\\n evidence  "}][:limit]}\n',
        encoding="utf-8",
    )
    return {"db": db, "catalog": catalog, "assets": assets, "skills": skills, "root": root}


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fx = build_fixture(Path(self.temp.name))
        self.env = patch.dict(os.environ, {
            "BRAIN_DB": str(self.fx["db"]),
            "BRAIN_CATALOG": str(self.fx["catalog"]),
            "BRAIN_SKILLS": str(self.fx["skills"]),
            "BRAIN_ASSETS": str(self.fx["assets"]),
            "BRAIN_KNOWLEDGE_VERSION": "fixture-v1",
        }, clear=False)
        self.env.start()
        self.vec = patch.dict(sys.modules, {"sqlite_vec": types.SimpleNamespace(load=lambda con: None)})
        self.vec.start()

    def tearDown(self):
        sys.modules.pop("knowledge_index", None)
        self.vec.stop()
        self.env.stop()
        self.temp.cleanup()


class SemanticCoreTests(FixtureCase):
    def test_metrics_are_governed_bounded_and_literal(self):
        listing = core.list_metrics()
        self.assertEqual([m["name"] for m in listing["metrics"]], ["dormant", "revenue"])
        result = core.get_metric("revenue", entity_contains="N%orth_\\HQ")
        self.assertEqual(result["rows"][0]["value"], 45.0)
        self.assertEqual(core.get_metric("revenue", entity_contains="%") ["status"], "ok")
        self.assertEqual(core.get_metric("revenue", entity_contains="' OR 1=1 --")["status"], "not_modeled")
        self.assertTrue(core.get_metric("revenue", limit=1)["truncated"])
        with self.assertRaisesRegex(ValueError, "Unknown metric"):
            core.get_metric("unknown")
        with self.assertRaises(ValueError):
            core.get_metric("revenue", limit=101)

    def test_database_is_read_only(self):
        with core._readonly_connection() as con, self.assertRaises(sqlite3.OperationalError):
            con.execute("INSERT INTO facts VALUES('x','x','x','x','x',1,'x')")

    def test_search_taxonomy_related_and_evidence(self):
        fake = types.SimpleNamespace(
            DEFAULT_MODEL="fake",
            search=lambda con, model, query, limit: {"results": [{"id": 1, "source": "a.md", "title": "Overview", "score": 0.75, "text": " Alpha\n evidence "}]},
        )
        def fixture_connection(*, vectors=False):
            con = sqlite3.connect(f"file:{self.fx['db'].as_posix()}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only=ON")
            return con

        with patch.dict(sys.modules, {"knowledge_index": fake}), patch.object(core, "_readonly_connection", fixture_connection):
            hit = core.search_knowledge("alpha", 1)["hits"][0]
        self.assertEqual(hit["text"], "Alpha evidence")
        taxonomy = core.get_taxonomy("sales")
        self.assertEqual(taxonomy["subclasses"][0]["label"], "Retail")
        self.assertEqual(taxonomy["tagged_sections"][0]["chunk_id"], 1)
        related = core.find_related_content(chunk_id=1)
        self.assertEqual([row["chunk_id"] for row in related["related"]], [2, 3])
        evidence = core.get_evidence(1)
        self.assertEqual(evidence["verbatim_page_text"], "VERBATIM PAGE")
        self.assertIn("|1|2|", evidence["extracted_tables"])

    def test_missing_optional_tables_return_not_modeled(self):
        with sqlite3.connect(self.fx["db"]) as con:
            con.execute("DROP TABLE related")
            con.execute("DROP TABLE graph_edges")
        self.assertEqual(core.find_related_content(chunk_id=1)["status"], "not_modeled")
        self.assertEqual(core.get_taxonomy()["status"], "not_modeled")

    def test_health_is_safe_and_versioned(self):
        result = core.health()
        expected = "healthy" if hasattr(sqlite3.connect(":memory:"), "enable_load_extension") else "degraded"
        self.assertEqual(result["status"], expected)
        self.assertEqual(result["knowledge_version"], "fixture-v1")
        self.assertNotIn(str(self.fx["root"]), json.dumps(result))


@unittest.skipUnless(Client is not None, "fastmcp is not installed")
class FastMCPContractTests(FixtureCase):
    def test_in_process_and_real_stdio(self):
        import fastmcp_server

        async def run():
            expected = {"list_metrics", "get_metric", "search_knowledge", "get_taxonomy", "find_related_content", "get_evidence", "health"}
            async with Client(fastmcp_server.mcp) as client:
                self.assertEqual({tool.name for tool in await client.list_tools()}, expected)
                result = await client.call_tool("get_metric", {"name": "revenue", "grain": "overall"})
                self.assertEqual(result.data["rows"][0]["value"], 100.0)
            env = {key: os.environ[key] for key in ("BRAIN_DB", "BRAIN_CATALOG", "BRAIN_SKILLS", "BRAIN_ASSETS", "BRAIN_KNOWLEDGE_VERSION")}
            env["BRAIN_SHOW_BANNER"] = "0"
            env["PYTHONPATH"] = os.pathsep.join((str(self.fx["root"]), str(HERE)))
            transport = StdioTransport(command=sys.executable, args=[str(HERE / "fastmcp_server.py"), "--transport", "stdio"], env=env)
            async with Client(transport) as client:
                result = await client.call_tool("health", {})
                self.assertEqual(result.data["status"], "healthy")
        asyncio.run(run())

    def test_api_key_middleware(self):
        import fastmcp_server

        async def request(path, headers):
            messages = []

            async def downstream(scope, receive, send):
                await send({"type": "http.response.start", "status": 204, "headers": []})
                await send({"type": "http.response.body", "body": b""})

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                messages.append(message)

            middleware = fastmcp_server.ApiKeyMiddleware(downstream, "secret", "/custom/mcp")
            await middleware({"type": "http", "path": path, "headers": headers}, receive, send)
            return messages

        missing = asyncio.run(request("/custom/mcp", []))
        wrong = asyncio.run(request("/custom/mcp/", [(b"x-api-key", b"wrong")]))
        valid = asyncio.run(request("/custom/mcp", [(b"X-API-Key", b"secret")]))
        health = asyncio.run(request("/healthz", []))
        self.assertEqual(missing[0]["status"], 401)
        self.assertEqual(wrong[0]["status"], 401)
        self.assertEqual(valid[0]["status"], 204)
        self.assertEqual(health[0]["status"], 204)
        self.assertIn(b"www-authenticate", dict(missing[0]["headers"]))
        with patch.dict(os.environ, {"BRAIN_API_KEY": " secret ", "BRAIN_MCP_PATH": "/custom/mcp"}):
            configured = fastmcp_server._http_middleware()
        self.assertEqual(len(configured), 1)
        with patch.dict(os.environ, {"BRAIN_API_KEY": "   "}):
            self.assertEqual(fastmcp_server._http_middleware(), [])

    def test_http_asgi_health_and_initialize(self):
        import httpx
        import fastmcp_server

        async def run():
            app = fastmcp_server.app
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    health = await client.get("/healthz")
                    self.assertEqual(health.status_code, 200)
                    response = await client.post("/mcp", headers={"accept": "application/json, text/event-stream"}, json={
                        "jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
                    })
                    self.assertEqual(response.status_code, 200)
                    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
                    self.assertEqual(payload["result"]["serverInfo"]["name"], "Semantic Knowledge Brain")
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
