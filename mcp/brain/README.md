# Semantic Knowledge Brain — FastMCP

A governed MCP tool layer over a private `knowledge.sqlite` store. The same entry point supports:

- **STDIO by default** for local clients, with no listening port;
- **Streamable HTTP**, explicitly enabled for remote clients.

Raw SQL is not exposed. SQLite is opened in read-only/query-only mode, filters are parameterized, and every result `limit` is strictly bounded to `1..100`. Broad retrieval must be split into multiple focused calls rather than requesting an oversized response.

All tool-level input, configuration, dependency, and unexpected runtime failures are returned as normal MCP results (`isError=false`) with `status=error`, a stable error code, and actionable `how_to_fix` guidance. Every public tool has local validation plus a shared final exception boundary, and internal errors are masked to avoid leaking paths or sensitive details. This prevents gateways from translating tool failures into HTTP 500 responses. Agents should follow `how_to_fix` and retry; `status=not_modeled` remains a valid data-gap response rather than an error. `/healthz` also catches unexpected failures and returns a sanitized `503` degraded payload rather than crashing the HTTP application.

Every advertised tool property carries a concrete primitive schema, with a `null` alternative where `None` is the default. The Python boundary uses Pydantic `SkipValidation`, so malformed values still reach local fail-safe checks and return actionable normal tool results. This prevents schema-converting clients from silently replacing annotation-only properties with an empty fallback model while keeping defaults valid against their own schemas.

## Tools

| Tool | Purpose |
|---|---|
| `list_metrics` | Discover governed metrics, units, grains, and periods |
| `get_metric` | Read exact `facts` rows with `source_file` citations |
| `search_knowledge` | Hybrid BM25 + vector narrative retrieval |
| `get_taxonomy` | Explore taxonomy nodes, edges, and tagged sections |
| `find_related_content` | Read precomputed semantic neighbors |
| `get_evidence` | Inspect one cited section and optional page/table text |
| `health` | Check knowledge lanes and deployed knowledge version; also returns `about: {goal, audience}` |

`health` additionally returns an `about` object — `{"goal": <str>, "audience": <str>}` — read from the store's durable `meta` table (seeded from `goal.txt` and `brain.toml` `[project].audience`). Consumers such as the `kb` plugin use it to tune answer altitude and authored-artifact style. Stores built before the `meta` table (or with no values recorded) return empty strings, never an error.

`brain_mcp.py` is retained temporarily as the legacy stdio implementation. New integrations should use `fastmcp_server.py`.

## Install and run

```bash
./install.sh --bundle brain --deps --mcp

# local stdio (default)
<venv>/bin/python <host>/mcp/brain/fastmcp_server.py

# explicit local stdio
<venv>/bin/python <host>/mcp/brain/fastmcp_server.py --transport stdio

# opt-in HTTP; binds all interfaces for container/orchestrator reachability
BRAIN_API_KEY='<secret>' <venv>/bin/python <host>/mcp/brain/fastmcp_server.py --transport http
```

The server listens on `0.0.0.0:8000` by default. Connect through the machine/container address; from the same host use:

- MCP: `http://127.0.0.1:8000/mcp`
- health: `http://127.0.0.1:8000/healthz`

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `BRAIN_MCP_TRANSPORT` | `stdio` | `stdio`, `http`, or `streamable-http` |
| `HOST` | `0.0.0.0` | HTTP listen address; set `127.0.0.1` for local-only access |
| `PORT` | `8000` | HTTP port |
| `BRAIN_MCP_PATH` | `/mcp` | Streamable HTTP endpoint |
| `BRAIN_DB` | auto-discovered | Private SQLite store |
| `BRAIN_CATALOG` | auto-discovered | Governed metrics JSON |
| `BRAIN_SKILLS` | auto-discovered | Runtime retrieval modules |
| `BRAIN_ASSETS` | auto-discovered | Visual evidence sidecars |
| `BRAIN_KNOWLEDGE_VERSION` | `unversioned` | Version reported by health |
| `BRAIN_API_KEY` | empty | When set, require this value in `X-API-Key` on every MCP HTTP request |

API-key protection applies only to the configured MCP path; `/healthz` remains unauthenticated for platform probes. Missing or incorrect keys receive `401 Unauthorized`. For Copilot Studio, configure **API key → Header** with header name `X-API-Key`. Store the key in a secret manager and inject it as `BRAIN_API_KEY`; never commit it to MCP config or source control.

The default all-interface bind makes container and orchestrator networking manageable, but may expose private knowledge anywhere the port is reachable. Set `BRAIN_API_KEY`, restrict ingress/firewalls, and use TLS, authorization, rate limits, key rotation, and audit controls. An API key authenticates the caller but does not provide user-level authorization.

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
| `sql` | removed; use `list_metrics`/`get_metric` (no raw-SQL alias) |

Legacy names are deliberately **not advertised or executed as aliases** because that would preserve the unsafe/raw contract and make migration invisible. If an older client calls one, the server returns a normal `isError=false`, `status=error`, `code=legacy_tool` result naming the replacement and telling the agent to retry. This avoids both silent semantic changes and gateway HTTP 500 failures.

`get_evidence` returns source text and deterministic table sidecars, not binary images. Clients that need the rendered image should resolve the returned `page_asset` inside the configured private assets store.

## Deployment contract

Deployment automation is intentionally not embedded in the generic server yet: registry names, cloud subscriptions, resource groups, secret identifiers, ingress policy, and metric-catalog filenames belong to the consuming project. A reusable deployment layer should accept those values as explicit configuration and package the project's `knowledge.sqlite`, governed metric catalog, assets, and installed retrieval skills into an immutable image.

Regardless of platform, a deployment is incomplete until all of these checks pass against the public endpoint:

1. the new immutable image/revision is healthy and receives the intended traffic;
2. `GET /healthz` returns `200`, `database_check=ok`, non-empty required lanes, and the intended `BRAIN_KNOWLEDGE_VERSION`;
3. `/mcp` rejects missing and incorrect credentials;
4. authenticated MCP initialization succeeds and `tools/list` returns exactly the seven governed tools;
5. every advertised argument property has a concrete JSON Schema `type`;
6. `get_metric` returns a real governed row with `source_file`;
7. `search_knowledge` returns at least one cited hit without downloading its embedding model at runtime; and
8. application logs contain no startup exception, request exception, or model-download attempt.

Prefer a two-layer design when deployment automation is added:

- a provider-neutral image contract and smoke-test command maintained here;
- a consuming-project deployment profile or thin adapter containing Azure/AWS/GCP-specific resource names and secret references.

Never put API-key values into manifests, build arguments, logs, documentation, or generated client configuration.

## Test

```bash
python mcp/brain/test_semantic_mcp.py -v
```
