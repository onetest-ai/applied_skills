# Semantic Knowledge Brain — FastMCP

A governed MCP tool layer over a private `knowledge.sqlite` store. The same entry point supports:

- **STDIO by default** for local clients, with no listening port;
- **Streamable HTTP**, explicitly enabled for remote clients.

Raw SQL is not exposed. SQLite is opened in read-only/query-only mode, filters are parameterized, and every result `limit` is strictly bounded to `1..100`. Broad retrieval must be split into multiple focused calls rather than requesting an oversized response.

## Tools

| Tool | Purpose |
|---|---|
| `list_metrics` | Discover governed metrics, units, grains, and periods |
| `get_metric` | Read exact `facts` rows with `source_file` citations |
| `search_knowledge` | Hybrid BM25 + vector narrative retrieval |
| `get_taxonomy` | Explore taxonomy nodes, edges, and tagged sections |
| `find_related_content` | Read precomputed semantic neighbors |
| `get_evidence` | Inspect one cited section and optional page/table text |
| `health` | Check knowledge lanes and deployed knowledge version |

`brain_mcp.py` is retained temporarily as the legacy stdio implementation. New integrations should use `fastmcp_server.py`.

## Install and run

```bash
./install.sh --bundle brain --deps --mcp

# local stdio (default)
<venv>/bin/python <host>/mcp/brain/fastmcp_server.py

# explicit local stdio
<venv>/bin/python <host>/mcp/brain/fastmcp_server.py --transport stdio

# opt-in HTTP; loopback is the safe default
BRAIN_API_KEY='<secret>' <venv>/bin/python <host>/mcp/brain/fastmcp_server.py --transport http
```

HTTP endpoints default to:

- MCP: `http://127.0.0.1:8000/mcp`
- health: `http://127.0.0.1:8000/healthz`

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `BRAIN_MCP_TRANSPORT` | `stdio` | `stdio`, `http`, or `streamable-http` |
| `HOST` | `127.0.0.1` | HTTP listen address |
| `PORT` | `8000` | HTTP port |
| `BRAIN_MCP_PATH` | `/mcp` | Streamable HTTP endpoint |
| `BRAIN_DB` | auto-discovered | Private SQLite store |
| `BRAIN_CATALOG` | auto-discovered | Governed metrics JSON |
| `BRAIN_SKILLS` | auto-discovered | Runtime retrieval modules |
| `BRAIN_ASSETS` | auto-discovered | Visual evidence sidecars |
| `BRAIN_KNOWLEDGE_VERSION` | `unversioned` | Version reported by health |
| `BRAIN_API_KEY` | empty | When set, require this value in `X-API-Key` on every MCP HTTP request |

API-key protection applies only to the configured MCP path; `/healthz` remains unauthenticated for platform probes. Missing or incorrect keys receive `401 Unauthorized`. For Copilot Studio, configure **API key → Header** with header name `X-API-Key`. Store the key in a secret manager and inject it as `BRAIN_API_KEY`; never commit it to MCP config or source control.

Binding to a non-loopback address exposes private knowledge to the network. Use TLS, authorization, rate limits, key rotation, and audit controls before production or public deployment. An API key authenticates the caller but does not provide user-level authorization.

## Migration from the legacy server

| Legacy | Governed API |
|---|---|
| `search` | `search_knowledge` |
| `metric` | `get_metric` |
| `graph` | `get_taxonomy` |
| `related` | `find_related_content` |
| `page` | `get_evidence` |
| `verify` | `health` |
| `which` | configuration plus `health` |
| `sql` | removed; use governed tools |

`get_evidence` returns source text and deterministic table sidecars, not binary images. Clients that need the rendered image should resolve the returned `page_asset` inside the configured private assets store.

## Test

```bash
python mcp/brain/test_semantic_mcp.py -v
```
