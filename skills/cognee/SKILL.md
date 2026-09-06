---
name: cognee
description: Use to access a Cognee knowledge-graph server — search/recall (RAG over the graph), inspect datasets ("brains"), ingest (remember / add + cognify), update/forget. Prefer its MCP server when the runtime has it wired; fall back to the REST API for the full surface, scripting, and CI. Connection is per-deployment (no hardcoded host). For narrative/qualitative retrieval; pair with a deterministic numeric lane for exact figures.
---

# Cognee (generic access)

Work with any Cognee server. Cognee calls a dataset a **"brain."** This skill is connection-agnostic — supply the deployment's endpoint(s); nothing is hardcoded.

## Access path: MCP (preferred) vs REST (full surface)

Cognee ships **both** an MCP server and a REST API. Choose by task:

| | MCP server | REST API |
|---|---|---|
| Best for | interactive agent use — one `recall`/`remember` call | full control, scripting, CI, exact `searchType` |
| Surface | ~11 tools (memory + dataset ops) | ~80 endpoints (status, graph export, ontologies, permissions…) |
| Wiring | runtime MCP config (per client) | plain HTTP anywhere |

**MCP tools (v1.29.x):** `recall` (search, auto-routing + session-aware), `remember` (add+cognify), `forget`, `cognify_file` (base64 ingest), `list_datasets_json`, `list_dataset_data_json`, `create_dataset_json`, `get_client_info_json`, and 3 UI openers (`visualize_graph_ui`, `upload_file_ui`, `open_cognee_workspace`). When these are available in the runtime (as `mcp__cognee__*`), **prefer `recall` for narrative retrieval and `remember` for ingest** — no curl, no token handling.

**Wire the MCP server** (transport is SSE or stdio depending on the deployment), e.g. Claude Code:
```bash
claude mcp add --transport sse --scope user cognee <COGNEE_MCP_URL>   # e.g. http://<host>:8001/sse
```
MCP servers load at client startup — after adding, restart the session for the tools to appear. An SSE endpoint answers `GET /sse` with an `event: endpoint` handshake.

**Use REST when** you need a specific `searchType` (GRAPH_COMPLETION / CHUNKS / TEMPORAL…), processing-status polling, graph JSON export, ontologies/permissions/settings, deterministic scripted calls, or CI — none of which the MCP tool set exposes. The rest of this doc + the full endpoint catalog cover REST: [api-reference.md](api-reference.md).

## Connection (per-deployment — never hardcode)

Set these for the target instance (env vars, a project config, or a secrets store):
- `COGNEE_URL` — base URL (e.g. `http://localhost:8000` for a local dev server).
- Credentials — email + password, or an API key. Local dev images often ship `default_user@example.com` / `default_password`; **treat any real deployment's credentials as secrets.**
- The **dataset (brain) name or id** you intend to query — a project detail, kept with the project, not this skill.

`GET /health` needs no auth. On a standard deployment everything under `/api/v1/*` needs a Bearer token; some deployments **run the API without auth** (e.g. a trusted LAN box) — then calls work directly with no login/token. Probe once: an unauthenticated `GET /api/v1/datasets` returning `200` (not `401`) means auth is off.

## Get a token

```bash
TOKEN=$(curl -s -X POST "$COGNEE_URL/api/v1/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "username=$COGNEE_USER" \
  --data-urlencode "password=$COGNEE_PASS" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
AUTH=(-H "Authorization: Bearer $TOKEN")
```

Login is **form-encoded** (fastapi-users), not JSON.

## Search — the main retrieval action

```bash
curl -s -X POST "$COGNEE_URL/api/v1/search" "${AUTH[@]}" -H "Content-Type: application/json" \
  -d '{"searchType":"GRAPH_COMPLETION","query":"YOUR QUESTION","datasets":["<brain>"],"topK":15}'
```

**searchType:** `HYBRID_COMPLETION` (default), `GRAPH_COMPLETION`, `GRAPH_COMPLETION_COT`, `RAG_COMPLETION`, `CHUNKS`, `SUMMARIES`, `TEMPORAL`, `FEELING_LUCKY`, `AGENTIC_COMPLETION`, `CODE`, `CYPHER`, `NATURAL_LANGUAGE`, and more (see reference). `*_COMPLETION` = synthesized LLM answer; `CHUNKS`/`SUMMARIES` = raw evidence; `AGENTIC_COMPLETION` enables `skills`/`tools`/`maxIter`. Query owned datasets by **name** (`datasets`); use `datasetIds` for shared datasets. `POST /api/v1/recall` is a richer sibling (adds `scope`, streaming, `responseSchema`).

## Data lifecycle

```
add  →  cognify  →  search        (ingest raw data → build the graph → query it)
```
- `POST /api/v1/add` — ingest files/text (**multipart**): `raw_data` for inline strings, `datasetName`/`datasetId`, `node_set` tags, `run_in_background`.
- `POST /api/v1/cognify` — build the graph (**JSON**): `datasets`/`datasetIds`, optional `customPrompt`, `ontologyKey`, `graphModel`, `chunkSize`, `runInBackground`.
- `POST /api/v1/memify` / `improve` — enrichment passes.

## Inspect

| Goal | Call |
|------|------|
| List datasets/brains | `GET /api/v1/datasets` |
| Brain summary (node counts) | `GET /api/v1/visualize/brains-summary` |
| Documents in a dataset | `GET /api/v1/datasets/{id}/data` |
| Raw text of one document | `GET /api/v1/datasets/{id}/data/{data_id}/raw` |
| Processing status | `GET /api/v1/datasets/status?dataset={id}` |
| Graph JSON | `GET /api/v1/datasets/{id}/graph` |
| Who am I | `GET /api/v1/auth/me` |

Status `DATASET_PROCESSING_STARTED` means cognify is still running; `brains-summary` can report a stale `node_count: 0` while graph/search already work on partial data.

## Modify / remove

`PATCH /api/v1/update` (replace a doc), `DELETE /api/v1/delete` (one item), `DELETE /api/v1/datasets/{id}` (a brain), `POST /api/v1/forget` (by `dataId`/`dataset`; `memoryOnly=true` clears graph+embeddings but keeps raw). ⚠️ `forget` with `everything=true` deletes ALL of the caller's data — never use it to clear one brain.

## Common mistakes

- Any `/api/v1/*` call without the Bearer token → 401. Log in first.
- Login sent as JSON — it must be **form-encoded**.
- `add`/`remember`/`update` are **multipart**; `cognify`/`search`/`recall`/`memify` are **JSON**.
- Passing a **shared** dataset by name in `datasets` — names resolve only for datasets you own; use `datasetIds`.
- Trusting `node_count` while a dataset is still processing.
- **Hardcoding a URL or credentials** — keep them per-deployment (env/config/secrets).

When unsure of a field: `curl -s "$COGNEE_URL/openapi.json"` and read `paths` / `components.schemas`, or see [api-reference.md](api-reference.md).
