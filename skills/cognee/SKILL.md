---
name: cognee
description: Use to access a Cognee knowledge-graph server over its REST API — authenticate, then search/recall (RAG over the graph), inspect datasets ("brains"), and ingest (add + cognify) or update/forget data. Connection is per-deployment via COGNEE_URL / credentials (no hardcoded host). For narrative/qualitative retrieval; pair with a deterministic numeric lane for exact figures.
---

# Cognee (generic access)

Work with any Cognee server (v1.5.x) over its REST API. Cognee calls a dataset a **"brain."** This skill is connection-agnostic — supply the deployment's URL and credentials; nothing is hardcoded.

Full endpoint catalog: [api-reference.md](api-reference.md).

## Connection (per-deployment — never hardcode)

Set these for the target instance (env vars, a project config, or a secrets store):
- `COGNEE_URL` — base URL (e.g. `http://localhost:8000` for a local dev server).
- Credentials — email + password, or an API key. Local dev images often ship `default_user@example.com` / `default_password`; **treat any real deployment's credentials as secrets.**
- The **dataset (brain) name or id** you intend to query — a project detail, kept with the project, not this skill.

`GET /health` needs no auth; everything under `/api/v1/*` needs a Bearer token.

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
