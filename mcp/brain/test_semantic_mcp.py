from __future__ import annotations

import asyncio
import importlib.util
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

try:
    import httpx as _httpx
except ImportError:
    _httpx = None


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
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            """
        )
        con.executemany("INSERT INTO meta VALUES(?,?)", [
            ("goal", "optimize call-center operations"),
            ("audience", "ops managers and workforce planners"),
        ])
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
        # chunk_id is emitted as a STRING (int64 precision safety for JSON clients).
        self.assertEqual(taxonomy["tagged_sections"][0]["chunk_id"], "1")
        related = core.find_related_content(chunk_id=1)
        self.assertEqual([row["chunk_id"] for row in related["related"]], ["2", "3"])
        self.assertEqual(related["anchor"]["chunk_id"], "1")
        evidence = core.get_evidence(1)
        self.assertEqual(evidence["chunk_id"], "1")
        self.assertEqual(evidence["verbatim_page_text"], "VERBATIM PAGE")
        self.assertIn("|1|2|", evidence["extracted_tables"])

    def test_search_filters_need_a_capable_index(self):
        # An older bundled knowledge-index has a 4-arg search(); requesting a filter against
        # it must fail with a CLEAR ValueError (-> invalid_arguments), never an opaque arity
        # TypeError mislabeled as invalid_arguments (regression for the misleading error).
        def ro(*, vectors=False):
            con = sqlite3.connect(f"file:{self.fx['db'].as_posix()}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            return con
        old = types.SimpleNamespace(DEFAULT_MODEL="fake",
                                    search=lambda con, model, query, limit: {"results": []})
        with patch.dict(sys.modules, {"knowledge_index": old}), patch.object(core, "_readonly_connection", ro):
            self.assertEqual(core.search_knowledge("alpha", 1)["count"], 0)  # no filters: fine
            with self.assertRaisesRegex(ValueError, "does not support search filters"):
                core.search_knowledge("alpha", 1, source_contains="Transcript")
        # A capable index that accepts the extended signature receives the filter.
        seen = {}
        def ext(con, model, query, limit, as_of=None, latest_only=False,
                source_contains=None, tag=None, tag_boost=None):
            seen["source_contains"] = source_contains
            return {"results": []}
        new = types.SimpleNamespace(DEFAULT_MODEL="fake", search=ext)
        with patch.dict(sys.modules, {"knowledge_index": new}), patch.object(core, "_readonly_connection", ro):
            core.search_knowledge("alpha", 1, source_contains="Transcript")
        self.assertEqual(seen["source_contains"], "Transcript")

    def test_chunk_id_survives_as_string_for_precision(self):
        # A 63-bit int64 chunk id (sha256-derived) exceeds JS's 2**53 safe range, so it
        # must be emitted AND accepted as a string; otherwise a float64 JSON client (e.g.
        # Cowork) rounds it and get_evidence/find_related_content return not_modeled.
        big = 3199336978672560001
        self.assertGreater(big, 2 ** 53)
        with sqlite3.connect(self.fx["db"]) as con:
            con.execute(
                "INSERT INTO chunks VALUES(?,?,?,?,?,?,?)",
                (big, "ordering.md", 0, "Ordering", "Engage vs RMS", "h", None),
            )
            con.execute("INSERT INTO related VALUES(?,?,?)", (big, 1, 0.7))
        # Output is a string, exactly the id.
        ev = core.get_evidence(big)
        self.assertEqual(ev["status"], "ok")
        self.assertIsInstance(ev["chunk_id"], str)
        self.assertEqual(ev["chunk_id"], str(big))
        # The exact string round-trips back in (the JS-client path).
        self.assertEqual(core.get_evidence(str(big))["source"], "ordering.md")
        # not_modeled still echoes the id as a string.
        missing = core.get_evidence(str(big + 7))
        self.assertEqual(missing["status"], "not_modeled")
        self.assertEqual(missing["chunk_id"], str(big + 7))
        # find_related_content accepts the string id and returns string ids.
        rel = core.find_related_content(chunk_id=str(big))
        self.assertEqual(rel["anchor"]["chunk_id"], str(big))
        self.assertIsInstance(rel["related"][0]["chunk_id"], str)

    def test_evidence_rejects_asset_path_escape(self):
        with sqlite3.connect(self.fx["db"]) as con:
            con.execute("UPDATE chunks SET image='../outside.png' WHERE id=1")
        with self.assertRaises(PermissionError):
            core.get_evidence(1)

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
        self.assertEqual(result["about"]["goal"], "optimize call-center operations")
        self.assertEqual(result["about"]["audience"], "ops managers and workforce planners")
        # stock fixture seeds no `name` row: a store predating this change reports an empty name
        self.assertEqual(result["about"]["name"], "")

    def test_health_about_degrades_without_meta_table(self):
        # A store built before the meta table must still return about with empty strings.
        with sqlite3.connect(self.fx["db"]) as con:
            con.execute("DROP TABLE meta")
        result = core.health()
        self.assertEqual(result["about"], {"name": "", "goal": "", "audience": ""})

    def test_health_about_empty_when_meta_has_no_values(self):
        with sqlite3.connect(self.fx["db"]) as con:
            con.execute("DELETE FROM meta")
        result = core.health()
        self.assertEqual(result["about"], {"name": "", "goal": "", "audience": ""})

    def test_health_about_carries_name_when_set(self):
        with sqlite3.connect(self.fx["db"]) as con:
            con.execute("INSERT OR REPLACE INTO meta VALUES('name','ACME Contact Centre')")
        result = core.health()
        self.assertEqual(result["about"]["name"], "ACME Contact Centre")
        self.assertEqual(result["about"]["goal"], "optimize call-center operations")


@unittest.skipUnless(Client is not None, "fastmcp is not installed")
class FastMCPContractTests(FixtureCase):
    def test_in_process_and_real_stdio(self):
        import fastmcp_server

        async def run():
            expected = {"list_metrics", "get_metric", "search_knowledge", "get_current_fact", "get_question_status", "get_taxonomy", "find_related_content", "get_evidence", "health"}
            async with Client(fastmcp_server.mcp) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                self.assertEqual(set(tools), expected)
                for name in ("get_metric", "search_knowledge", "get_taxonomy", "find_related_content"):
                    limit = tools[name].inputSchema["properties"]["limit"]
                    self.assertIn("1..100", limit["description"])
                    self.assertIn("never send more than 100", limit["description"])
                    self.assertEqual(limit["type"], "integer")
                expected_types = {
                    "get_metric": {
                        "name": "string", "grain": "string", "entity": "string",
                        "entity_contains": "string", "month": "string", "start_month": "string",
                        "end_month": "string", "limit": "integer",
                    },
                    "search_knowledge": {"query": "string", "limit": "integer", "as_of": "string", "latest_only": "boolean", "source_contains": "string", "tag": "string", "tag_boost": "string"},
                    "get_current_fact": {"entity": "string", "predicate": "string", "as_of": "string"},
                    "get_question_status": {"question_id": "string", "as_of": "string"},
                    "get_taxonomy": {"label": "string", "relation": "string", "kind": "string", "limit": "integer"},
                    # chunk_id is advertised as STRING (not integer): a 63-bit int64 loses
                    # precision if the client sends it as a JSON number, so the schema must
                    # steer the client to pass the exact string it received from search.
                    "find_related_content": {"chunk_id": "string", "query": "string", "limit": "integer"},
                    "get_evidence": {"chunk_id": "string", "include_page_text": "boolean"},
                }
                for tool_name, field_types in expected_types.items():
                    properties = tools[tool_name].inputSchema["properties"]
                    for field, expected_type in field_types.items():
                        schema = properties[field]
                        advertised = ({schema.get("type")} if schema.get("type") else
                                      {item.get("type") for item in schema.get("anyOf", [])})
                        self.assertIn(expected_type, advertised, f"{tool_name}.{field} missing concrete type")
                        if schema.get("default", object()) is None:
                            self.assertIn("null", advertised, f"{tool_name}.{field} has invalid null default")
                # A successful data result stays a normal (non-error) MCP result.
                result = await client.call_tool("get_metric", {"name": "revenue", "grain": "overall"})
                self.assertFalse(result.is_error)
                self.assertEqual(result.data["rows"][0]["value"], 100.0)
                # An honest not_modeled ("no data") answer is NOT a failure: it must stay a
                # normal, non-error result even though it is not a data row.
                not_modeled = await client.call_tool("get_metric", {"name": "revenue", "entity": "does-not-exist"})
                self.assertFalse(not_modeled.is_error)
                self.assertEqual(not_modeled.data["status"], "not_modeled")
                # Genuine errors now surface as MCP isError=true, while still carrying the
                # structured how_to_fix guidance in BOTH structured data and text content so
                # clients that only check isError AND clients that read the body both see it.
                invalid = await client.call_tool("get_metric", {"name": "revenue", "limit": 200}, raise_on_error=False)
                self.assertTrue(invalid.is_error)
                self.assertEqual(invalid.data["status"], "error")
                self.assertEqual(invalid.data["error"]["code"], "invalid_arguments")
                self.assertIn("limit", invalid.data["how_to_fix"])
                self.assertIn("how_to_fix", invalid.content[0].text)
                missing_anchor = await client.call_tool("find_related_content", {}, raise_on_error=False)
                self.assertTrue(missing_anchor.is_error)
                self.assertEqual(missing_anchor.data["status"], "error")
                self.assertIn("chunk_id", missing_anchor.data["how_to_fix"])
                missing_required = await client.call_tool("get_evidence", {}, raise_on_error=False)
                self.assertTrue(missing_required.is_error)
                self.assertEqual(missing_required.data["error"]["code"], "invalid_arguments")
                # raise_on_error defaults to True, so a genuine error raises for clients that
                # rely on exceptions - the failure is never silently reported as success.
                with self.assertRaises(Exception):
                    await client.call_tool("get_evidence", {})
                unknown_metric = await client.call_tool("get_metric", {"name": "not-a-metric"}, raise_on_error=False)
                self.assertTrue(unknown_metric.is_error)
                self.assertEqual(unknown_metric.data["status"], "error")
                unknown_tool = await client.call_tool("definitely_not_a_tool", {}, raise_on_error=False)
                self.assertTrue(unknown_tool.is_error)
                unknown_payload = unknown_tool.data or json.loads(unknown_tool.content[0].text)
                self.assertEqual(unknown_payload["status"], "error")
                self.assertEqual(unknown_payload["error"]["code"], "unknown_tool")
                self.assertIn("not available", unknown_payload["error"]["message"])
                for legacy, replacement in fastmcp_server._LEGACY_TOOLS.items():
                    legacy_result = await client.call_tool(legacy, {}, raise_on_error=False)
                    self.assertTrue(legacy_result.is_error, legacy)
                    legacy_payload = legacy_result.data or json.loads(legacy_result.content[0].text)
                    self.assertEqual(legacy_payload["error"]["code"], "legacy_tool", legacy)
                    self.assertIn(replacement, legacy_payload["how_to_fix"], legacy)
                malformed_cases = (
                    ("get_metric", {"name": 123}),
                    ("get_metric", {"name": "revenue", "grain": ["region"]}),
                    ("get_metric", {"name": None}),
                    ("search_knowledge", {}),
                    ("search_knowledge", {"query": 42}),
                    ("search_knowledge", {"query": "alpha", "latest_only": "yes"}),
                    ("get_current_fact", {}),
                    ("get_question_status", {"question_id": 42}),
                    ("get_taxonomy", {"label": {"bad": "type"}}),
                    ("find_related_content", {"chunk_id": "one"}),
                    ("get_evidence", {"chunk_id": "one"}),
                    ("get_evidence", {"chunk_id": 1, "include_page_text": "yes"}),
                )
                for tool_name, arguments in malformed_cases:
                    malformed = await client.call_tool(tool_name, arguments, raise_on_error=False)
                    self.assertTrue(malformed.is_error, (tool_name, arguments))
                    self.assertEqual(malformed.data["status"], "error", (tool_name, arguments))
                    self.assertEqual(malformed.data["error"]["code"], "invalid_arguments")
                    self.assertTrue(malformed.data["how_to_fix"])
                with patch.object(fastmcp_server, "_get_evidence", side_effect=PermissionError("denied /secret/local/path")):
                    hidden_path = await client.call_tool("get_evidence", {"chunk_id": 1}, raise_on_error=False)
                self.assertTrue(hidden_path.is_error)
                self.assertEqual(hidden_path.data["error"]["code"], "not_configured")
                self.assertNotIn("secret/local/path", json.dumps(hidden_path.data))
                self.assertNotIn("secret/local/path", hidden_path.content[0].text)
                with patch.object(fastmcp_server, "_search_knowledge", side_effect=ModuleNotFoundError("missing /secret/module")):
                    hidden_dependency = await client.call_tool("search_knowledge", {"query": "alpha"}, raise_on_error=False)
                self.assertTrue(hidden_dependency.is_error)
                self.assertEqual(hidden_dependency.data["error"]["code"], "dependency_unavailable")
                self.assertNotIn("secret/module", json.dumps(hidden_dependency.data))
                for tool_name, attribute in (
                    ("list_metrics", "_list_metrics"),
                    ("get_metric", "_get_metric"),
                    ("search_knowledge", "_search_knowledge"),
                    ("get_current_fact", "_get_current_fact"),
                    ("get_question_status", "_get_question_status"),
                    ("get_taxonomy", "_get_taxonomy"),
                    ("find_related_content", "_find_related_content"),
                    ("get_evidence", "_get_evidence"),
                    ("health", "_health"),
                ):
                    arguments = {
                        "get_metric": {"name": "revenue"},
                        "search_knowledge": {"query": "alpha"},
                        "get_current_fact": {"entity": "project-atlas", "predicate": "release_date"},
                        "get_question_status": {"question_id": "q-atlas-owner"},
                        "get_taxonomy": {},
                        "find_related_content": {"chunk_id": 1},
                        "get_evidence": {"chunk_id": 1},
                    }.get(tool_name, {})
                    with patch.object(fastmcp_server, attribute, side_effect=RuntimeError("secret /private/path")):
                        unhandled = await client.call_tool(tool_name, arguments, raise_on_error=False)
                    self.assertTrue(unhandled.is_error, tool_name)
                    self.assertEqual(unhandled.data["error"]["code"], "internal_error", tool_name)
                    self.assertNotIn("private/path", json.dumps(unhandled.data), tool_name)
            # Limit contract still documented (guards Bug 10 regression), and the
            # citation rule that keeps raw ids out of user-facing answers.
            self.assertIn("limit is 1-100", fastmcp_server._ROUTING_INSTRUCTIONS)
            self.assertIn("make multiple narrower calls", fastmcp_server._ROUTING_INSTRUCTIONS)
            self.assertIn("Never print internal ids", fastmcp_server._ROUTING_INSTRUCTIONS)
            env = {key: os.environ[key] for key in ("BRAIN_DB", "BRAIN_CATALOG", "BRAIN_SKILLS", "BRAIN_ASSETS", "BRAIN_KNOWLEDGE_VERSION")}
            env["BRAIN_SHOW_BANNER"] = "0"
            env["PYTHONPATH"] = os.pathsep.join((str(self.fx["root"]), str(HERE)))
            transport = StdioTransport(command=sys.executable, args=[str(HERE / "fastmcp_server.py"), "--transport", "stdio"], env=env)
            async with Client(transport) as client:
                result = await client.call_tool("health", {})
                # "healthy" when sqlite_vec extension loads; "degraded" when
                # the Python build lacks enable_load_extension (macOS system Python).
                _has_ext = hasattr(sqlite3.connect(":memory:"), "enable_load_extension")
                expected_health = "healthy" if _has_ext else "degraded"
                self.assertEqual(result.data["status"], expected_health)
        asyncio.run(run())

    def test_invalid_transport_env_is_parser_error(self):
        import fastmcp_server

        with patch.dict(os.environ, {"BRAIN_MCP_TRANSPORT": "invalid"}), self.assertRaises(SystemExit) as raised:
            fastmcp_server.main([])
        self.assertEqual(raised.exception.code, 2)

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
        padded = asyncio.run(request("/custom/mcp", [(b"X-API-Key", b"  secret  ")]))
        health = asyncio.run(request("/healthz", []))
        self.assertEqual(missing[0]["status"], 401)
        self.assertEqual(wrong[0]["status"], 401)
        self.assertEqual(valid[0]["status"], 204)
        self.assertEqual(padded[0]["status"], 204)
        self.assertEqual(health[0]["status"], 204)
        self.assertIn(b"www-authenticate", dict(missing[0]["headers"]))
        with patch.dict(os.environ, {"BRAIN_API_KEY": " secret ", "BRAIN_MCP_PATH": "/custom/mcp"}):
            configured = fastmcp_server._http_middleware()
        self.assertEqual(len(configured), 1)
        with patch.dict(os.environ, {"BRAIN_API_KEY": "   "}):
            self.assertEqual(fastmcp_server._http_middleware(), [])

    def test_http_app_enforces_api_key_end_to_end(self):
        import httpx

        async def run():
            module_name = "fastmcp_server_authenticated_test"
            with patch.dict(os.environ, {"BRAIN_API_KEY": "secret", "BRAIN_MCP_PATH": "/secure-mcp"}):
                spec = importlib.util.spec_from_file_location(module_name, HERE / "fastmcp_server.py")
                module = importlib.util.module_from_spec(spec)
                assert spec.loader is not None
                spec.loader.exec_module(module)
            try:
                async with module.app.router.lifespan_context(module.app):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://test") as client:
                        health = await client.get("/healthz")
                        missing = await client.post("/secure-mcp", headers={"accept": "application/json, text/event-stream"}, json={})
                        wrong = await client.post("/secure-mcp", headers={"X-API-Key": "wrong", "accept": "application/json, text/event-stream"}, json={})
                        valid = await client.post("/secure-mcp", headers={"X-API-Key": " secret ", "accept": "application/json, text/event-stream"}, json={
                            "jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
                        })
                # Health status is 200 when sqlite_vec is available, 503 when
                # the environment lacks enable_load_extension (macOS system Python).
                # Both are valid outcomes on this machine — we only assert the
                # API-key protection (401 vs 200/valid), not a specific health status.
                self.assertIn(health.status_code, (200, 503))
                self.assertEqual(missing.status_code, 401)
                self.assertEqual(wrong.status_code, 401)
                self.assertEqual(valid.status_code, 200)
            finally:
                sys.modules.pop(module_name, None)

        asyncio.run(run())

    def test_http_asgi_health_and_initialize(self):
        import httpx
        import fastmcp_server

        async def run():
            app = fastmcp_server.app
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    health = await client.get("/healthz")
                    # 200 when sqlite_vec extension is available; 503 (degraded) when not.
                    self.assertIn(health.status_code, (200, 503))
                    with patch.object(fastmcp_server, "_health", side_effect=RuntimeError("secret /private/path")):
                        degraded = await client.get("/healthz")
                    self.assertEqual(degraded.status_code, 503)
                    self.assertEqual(degraded.json()["error"]["code"], "health_check_failed")
                    self.assertNotIn("private/path", degraded.text)
                    response = await client.post("/mcp", headers={"accept": "application/json, text/event-stream"}, json={
                        "jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
                    })
                    self.assertEqual(response.status_code, 200)
                    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
                    # The fixture store has no meta.name, so identity falls back to the
                    # first clause of meta.goal ("optimize call-center operations").
                    self.assertEqual(
                        payload["result"]["serverInfo"]["name"],
                        "Semantic Knowledge Brain — optimize call-center operations",
                    )
        asyncio.run(run())


@unittest.skipUnless(_httpx is not None, "httpx is not installed")
class RestShimLimitTests(FixtureCase):
    """Bug 10: REST shim /api/v1/search must cap limit at 100 before calling _search_knowledge.

    Before fix: ``limit = int(body.get("limit") or 15)`` passes the raw value
    straight to ``_search_knowledge`` which raises (via ``_limit_or_error``)
    and the shim returns ``str(ToolResult(...))`` — a stringified object, not a
    structured error.  Clients sending ``limit=200`` get a 200 response with
    a plain text string instead of a JSON payload.

    After fix: the shim clamps limit to ``max(1, min(int(limit), 100))`` before
    the call, so ``_search_knowledge`` always receives a valid integer and never
    raises a limit-validation error through the shim path.
    """

    def test_rest_shim_clamps_limit_to_100(self):
        import asyncio
        import httpx
        import fastmcp_server

        search_calls = []

        def _recording_search(query, limit, *args, **kwargs):
            search_calls.append(limit)
            return {"hits": []}

        async def run():
            app = fastmcp_server.app
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    with patch.object(fastmcp_server, "_search_knowledge", _recording_search):
                        # limit=200 must be capped to 100 by the shim
                        resp = await client.post(
                            "/api/v1/search",
                            json={"query": "alpha", "limit": 200},
                        )
                        self.assertEqual(resp.status_code, 200)
                        payload = resp.json()
                        # The response must be a list (valid JSON structure), not a string
                        self.assertIsInstance(
                            payload, list,
                            msg=(
                                "Bug 10: REST shim returned a stringified ToolResult instead of "
                                f"a list. Got: {payload!r}"
                            ),
                        )
                        # _search_knowledge must have been called with limit capped at 100
                        self.assertEqual(search_calls, [100], (
                            f"Bug 10: shim passed limit={search_calls} to _search_knowledge; "
                            "expected [100]"
                        ))

        asyncio.run(run())

    def test_rest_shim_rejects_invalid_limit_gracefully(self):
        """Non-integer limit in REST shim body must default to 15 (not crash)."""
        import asyncio
        import httpx
        import fastmcp_server

        search_calls = []

        def _recording_search(query, limit, *args, **kwargs):
            search_calls.append(limit)
            return {"hits": []}

        async def run():
            app = fastmcp_server.app
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    with patch.object(fastmcp_server, "_search_knowledge", _recording_search):
                        resp = await client.post(
                            "/api/v1/search",
                            json={"query": "alpha", "limit": "not-a-number"},
                        )
                        self.assertEqual(resp.status_code, 200)
                        payload = resp.json()
                        self.assertIsInstance(payload, list)
                        self.assertEqual(search_calls, [15])  # fallback to default

        asyncio.run(run())


@unittest.skipIf(Client is None, "fastmcp not installed")
class TestFinalizeErrorFlags(unittest.TestCase):
    """Unit-level checks for the isError promotion, independent of a live client."""

    def test_tagged_error_becomes_iserror_and_marker_is_stripped(self):
        import fastmcp_server as fs
        err = fs._error_result("search_knowledge", "invalid_arguments",
                               "query is required", how_to_fix="Provide a non-empty query")
        result = fs._finalize_error_flags(err.to_mcp_result())
        self.assertIsInstance(result, fs.CallToolResult)
        self.assertTrue(result.isError)
        # the private marker must never reach the client
        self.assertFalse((result.meta or {}).get(fs._ERROR_META_KEY))
        # actionable guidance survives for body-reading clients (structured + text)
        text_blob = " ".join(getattr(c, "text", "") for c in (result.content or []))
        blob = json.dumps(result.structuredContent or {}) + text_blob
        self.assertIn("query is required", blob)
        self.assertIn("Provide a non-empty query", blob)

    def test_untagged_result_stays_non_error(self):
        import fastmcp_server as fs
        # An ok/not_modeled result reaches _finalize_error_flags as a CallToolResult
        # with no error marker (a successful ToolResult otherwise converts to a plain
        # (content, structured) tuple, which the function also leaves untouched).
        ok = fs.CallToolResult(content=[fs.TextContent(type="text", text='{"status": "not_modeled"}')])
        result = fs._finalize_error_flags(ok)
        self.assertFalse(bool(result.isError))
        # a non-CallToolResult (successful tuple form) passes through unchanged
        passthrough = fs._finalize_error_flags(([fs.TextContent(type="text", text="ok")], {"status": "ok"}))
        self.assertIsInstance(passthrough, tuple)

    def test_legacy_tool_call_is_iserror_with_fix(self):
        import fastmcp_server as fs
        err = fs._error_result("search", "legacy_tool", "Legacy tool 'search' was removed.",
                               how_to_fix="Retry with search_knowledge.")
        result = fs._finalize_error_flags(err.to_mcp_result())
        self.assertTrue(result.isError)
        blob = json.dumps(result.structuredContent or {})
        self.assertIn("search_knowledge", blob)


class BrainIdentityTests(FixtureCase):
    def _server(self):
        import fastmcp_server as fs
        return fs

    def test_identity_prefers_name(self):
        with sqlite3.connect(self.fx["db"]) as con:
            con.execute("INSERT OR REPLACE INTO meta VALUES('name','ACME Contact Centre')")
        self.assertEqual(self._server()._brain_identity(), "ACME Contact Centre")

    def test_identity_falls_back_to_first_clause_of_goal(self):
        with sqlite3.connect(self.fx["db"]) as con:
            con.execute("DELETE FROM meta WHERE key='name'")
        # fixture goal: "optimize call-center operations"
        self.assertEqual(self._server()._brain_identity(), "optimize call-center operations")

    def test_identity_is_empty_without_meta(self):
        with sqlite3.connect(self.fx["db"]) as con:
            con.execute("DROP TABLE meta")
        self.assertEqual(self._server()._brain_identity(), "")

    def test_identity_is_empty_when_the_store_cannot_be_read(self):
        # _IDENTITY is computed at import time; if this guard ever narrows, an
        # unreadable store would crash the server on boot instead of serving anonymously.
        fs = self._server()
        with patch.object(fs, "_health", side_effect=sqlite3.OperationalError("unable to open database file")):
            self.assertEqual(fs._brain_identity(), "")
        # The clause must catch broadly, not just database errors.
        with patch.object(fs, "_health", side_effect=RuntimeError("boom")):
            self.assertEqual(fs._brain_identity(), "")

    def test_instructions_name_the_brain(self):
        with sqlite3.connect(self.fx["db"]) as con:
            con.execute("INSERT OR REPLACE INTO meta VALUES('name','ACME Contact Centre')")
        text = self._server()._instructions_for("ACME Contact Centre")
        self.assertIn("ACME Contact Centre", text)
        self.assertIn("never blend", text.lower())
        # The routing doctrine survives the identity paragraph.
        self.assertIn("search_knowledge", text)

    def test_instructions_without_identity_are_still_valid(self):
        text = self._server()._instructions_for("")
        self.assertNotIn("This Brain answers for", text)
        self.assertIn("search_knowledge", text)


if __name__ == "__main__":
    unittest.main()
