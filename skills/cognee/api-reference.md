# Cognee API — full endpoint catalog (v1.5.x)

Base URL is per-deployment (set `COGNEE_URL`, e.g. `http://localhost:8000`). All `/api/v1/*` require `Authorization: Bearer <token>`. `*` marks required fields.

## Auth & users

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api/v1/auth/login` | **form-encoded** `username`,`password` → `{access_token, token_type}` |
| POST | `/api/v1/auth/logout` | |
| POST | `/api/v1/auth/register` | create user |
| GET | `/api/v1/auth/me` | current user |
| GET/POST/DELETE | `/api/v1/auth/api-keys[/{id}]` | manage API keys |
| POST | `/api/v1/auth/forgot-password` · `/reset-password` · `/verify` · `/request-verify-token` | |
| GET/PATCH/DELETE | `/api/v1/users/me` · `/users/{id}` | |
| POST | `/api/v1/users/get-user-id` | |

## Data lifecycle

### POST /api/v1/add — `multipart/form-data`
- `data`: array (file uploads)
- `raw_data`: array|null — inline strings to ingest (one entry each)
- `labels`: string|null — JSON array of per-item labels, Nth applies to Nth item
- `external_metadata`: string|null — JSON array of per-file metadata objects
- `datasetName`: string|null — created if missing; required unless `datasetId`
- `datasetId`: string|null — mandatory for sharing a dataset between users
- `node_set`: array|null (default `['']`) — tags for graph organization/access control
- `run_in_background`: bool (default false)

### POST /api/v1/cognify — `application/json`
- `datasets`: array|null — names (owned datasets)
- `datasetIds`: array|null — UUIDs (required for shared; takes precedence)
- `runInBackground`: bool (default false) → returns `pipeline_run_id`
- `graphModel`: object|null — custom graph model JSON schema for entity extraction
- `customPrompt`: string|null — replace default entity-extraction prompt
- `chunkSize`: int|null — max tokens/chunk (e.g. 4096); null = auto
- `ontologyKey`: array|null — keys of uploaded ontologies to ground extraction
- `chunksPerBatch`: int|null — chunks per task batch (parallelism)
- `dataPerBatch`: int|null (default 20) — concurrent data items per dataset

### POST /api/v1/memify — `application/json`
Enrichment pass. `extractionTasks`, `enrichmentTasks`, `data`, `datasetName`, `datasetId`, `nodeName`, `runInBackground`.

### POST /api/v1/improve — `application/json`
Like memify plus `buildGlobalContextIndex` (bool), `sessionIds` (array).

## Search & recall

### POST /api/v1/search — `application/json`
- `query`*: string — required, no default
- `searchType`: SearchType (default `HYBRID_COMPLETION`)
- `datasets`: array|null (names) · `datasetIds`: array|null (UUIDs; for shared)
- `systemPrompt`: string|null (default "Answer the question using the provided context. Be as brief as possible.")
- `nodeName`: array|null — restrict to these node_sets
- `topK`: int (default 15)
- `onlyContext`: bool (default false) · `contextFormat`: `context`|`prompt` (default `context`)
- `sessionId`: string|null — session history feeds the completion
- `verbose`: bool (default false) — include graph representation
- `skills`: array|null · `tools`: array|null · `maxIter`: int|null — require `AGENTIC_COMPLETION`
- `includeReferences`: bool (default false) — attach source references
- `codeQuery`: object|null — for `searchType=CODE` (operation `query_facts`, `explore`, …)

**SearchType enum:** `SUMMARIES`, `CHUNKS`, `CHUNKS_LEXICAL`, `RAG_COMPLETION`, `HYBRID_COMPLETION`, `TRIPLET_COMPLETION`, `GRAPH_COMPLETION`, `GRAPH_COMPLETION_DECOMPOSITION`, `GRAPH_COMPLETION_COT`, `GRAPH_COMPLETION_CONTEXT_EXTENSION`, `GRAPH_SUMMARY_COMPLETION`, `GRAPH_REPORT`, `CYPHER`, `NATURAL_LANGUAGE`, `FEELING_LUCKY`, `TEMPORAL`, `CODING_RULES`, `AGENTIC_COMPLETION`, `CODE`, `SKILLS`.

- **GET /api/v1/search** — search history.

### POST /api/v1/recall — `application/json`
Superset of search. Adds: `scope`: array|string|null — which memory sources (`graph`, `session`, `trace`, `session_context`, `tools`, …); `toolConnections`: array|null; `stream`: bool|null (SSE); `toolsTrigger`: `always`|`on_empty`; `contextProfile`: `qa`|`agent`; `responseSchema`: object|null (JSON Schema for structured output). Also `searchType`, `datasets`, `datasetIds`, `query`*, `systemPrompt`, `nodeName`, `topK`, `onlyContext`, `contextFormat`, `verbose`, `includeReferences`, `sessionId`, `codeQuery`.
- **GET /api/v1/recall** — recall history.

## Memory (agent-oriented)

### POST /api/v1/remember — `multipart/form-data`
add + cognify in one call, session-attributed. Key fields: `data`, `raw_data`, `labels`, `external_metadata`, `datasetName`/`datasetId`, `session_id`, `node_set`, `run_in_background`, `custom_prompt`, `chunk_size` (default 4096), `chunks_per_batch` (default 36), `ontology_key`, `graph_model`, `content_type` (`skills`|`code`), `import_mode` (`preserve`|`hybrid`|`re-derive`), `skills_text`, `skill_name`, `index_vectors`.

### POST /api/v1/remember/entry — `application/json`
Structured memory entry. `entry`*, `dataset_name` (default `main_dataset`), `dataset_id`, `session_id` (required for qa/trace/feedback), `skill_improvement`.

## Datasets

| Method | Path | Body/params |
|--------|------|-------------|
| GET | `/api/v1/datasets` | list |
| POST | `/api/v1/datasets` | `{name*}` |
| DELETE | `/api/v1/datasets` | delete all |
| DELETE | `/api/v1/datasets/{dataset_id}` | path: UUID |
| GET | `/api/v1/datasets/{dataset_id}/data` | list documents |
| DELETE | `/api/v1/datasets/{dataset_id}/data/{data_id}` | delete one doc |
| GET | `/api/v1/datasets/{dataset_id}/data/{data_id}/raw` | raw file bytes |
| GET | `/api/v1/datasets/{dataset_id}/graph` | nodes+edges JSON |
| GET/PUT | `/api/v1/datasets/{dataset_id}/schema` | PUT body `{graphSchema, customPrompt}` |
| GET | `/api/v1/datasets/status?dataset={id}&pipeline={name}` | pipelines: `add_pipeline`,`cognify_pipeline`,`code_pipeline` |
| GET | `/api/v1/datasets/status/progress?dataset={id}` | progress % |
| GET | `/api/v1/datasets/graph-summary?dataset_ids={id}` | per-dataset summary |

## Modify / delete

- **PATCH /api/v1/update** — query `data_id*`, `dataset_id*`, `chunk_level_diff` (bool); multipart body `data*` (new version), `node_set`.
- **DELETE /api/v1/delete** — query `data_id*`, `dataset_id*`, `mode`, `delete_dataset_if_empty`.
- **POST /api/v1/forget** — `{dataId?, dataset?, datasetId?, everything=false, memoryOnly=false}`. `memoryOnly` clears graph+embeddings, keeps raw. ⚠️ `everything=true` deletes ALL user data.

## Visualization

`dataset_id*` plus optional `full`, `query`, `seed_node_ids`, `neighborhood_depth`, `neighborhood_seed_top_k`, `max_nodes`:
- **GET /api/v1/visualize** — HTML graph
- **GET /api/v1/visualize/json** — graph JSON
- **GET /api/v1/visualize/semantic** — semantic layout
- **GET /api/v1/visualize/brains** (`max_nodes`) · **/brains-summary** — all datasets
- **POST /api/v1/visualize/multi** — body: array of `{user, dataset}` pairs
- **GET /api/v1/visualize/live-events** — SSE stream

## Ontologies

- **GET /api/v1/ontologies** — list
- **POST /api/v1/ontologies** — multipart: `ontology_key*`, `ontology_file*` (OWL RDF/XML, `.owl`), `description`
- **DELETE /api/v1/ontologies/{ontology_key}**

## Skills (dataset-scoped knowledge)

- **POST /api/v1/skills** — `{skills_text*, skill_name?, dataset_name?, dataset_id?}` ingest SKILL.md as a Skill node
- **GET /api/v1/skills/?dataset_id={id}** — list (`include_inactive`, `limit`, `offset`)
- **GET/DELETE /api/v1/skills/{skill_id}**

## Config, sessions, sync, misc

- **GET/POST /api/v1/settings** — `{llm, vectorDb}`
- **GET/POST /api/v1/configuration/get_user_configuration[/{id}]` · `/store_user_configuration**
- **GET /api/v1/sessions[/{id}]** · `/sessions/stats` · `/cost-by-model` · `/cost-by-user-agent` · `/with-agent-info`
- **POST /api/v1/sync** — `{datasetIds}` → cloud · **GET /api/v1/sync/status**
- **GET /api/v1/validate?dataset={name}** — validate graph
- **POST /api/v1/responses/** — OpenAI-style: `{model=cognee-v1, input*, tools, toolChoice, temperature, maxCompletionTokens}`
- **POST /api/v1/llm/custom-prompt** · `/llm/infer-schema`
- **GET /api/v1/schema/inventory` · `/schema/provenance[/json]**
- **Agents:** `GET/POST /api/v1/agents/*` (register, list, connections)
- **Permissions/tenants/roles:** `/api/v1/permissions/*`
- **Integrations/plugins/slack:** `/api/v1/integrations/*`, `/api/v1/slack/*`
- **Activity:** `/api/v1/activity/{agents,users,spans,pipeline-runs,export/{dataset_id}}`
- **GET /health** · **GET /health/detailed** — no auth
