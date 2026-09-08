# brain — MCP server (the tool layer)

A **dependency-free stdio MCP server** (line-delimited JSON-RPC 2.0, ~100 lines of
stdlib — no SDK, no web framework) that turns the local **`knowledge.sqlite`** brain
into tools. The point is to *hide the scripts behind tools*, not to run a web server.

**Responsibility split (why it exists):**

| Layer | Who | Does |
|---|---|---|
| Tool layer | **this server** | owns the **venv + skills' code + store connection**; returns cited text / computed numbers. Never reasons. |
| Reasoning layer | the **agent** (MCP client) | decomposes the question, calls tools, composes **one cited answer** with honest gaps |

Truthfulness at the boundary: **numbers are computed** (`sql`/`metric`, every value carries `source_file`); **meaning is cited** (`search`/`graph`, text hits — never figures).

## Tools
- `which()` — resolved store / catalog / skills (call first if unsure)
- `search(query, k=5)` — narrative lane: hybrid RAG (BM25+vector, RRF); cited hits, not for numbers
- `sql(query)` — numbers lane: read-only `SELECT`/`WITH` (write/DDL rejected) → `{columns, rows}`
- `metric(name, grain?, entity?, entity_like?, month?, months?)` — governed metric → exact `facts` values with `source_file`
- `graph(label?, relation?, kind?)` — taxonomy: node + subclasses + tagged sections, or listings
- `verify()` — per-lane row counts + empty-lane flag

## Resolution (no hardcoding; env overrides)
- **skills** — `BRAIN_SKILLS` → the sibling `skills/` dir (installed at `<host>/skills`)
- **store** — `BRAIN_DB` → `./knowledge.sqlite` → `./schema/knowledge.sqlite`
- **catalog** — `BRAIN_CATALOG` → `schema/metrics.*.json`

## Run / register
Runs under the **brain venv** (needs `sqlite-vec` + `fastembed`; the server itself adds no dep). Install + register in one shot — the installer copies this dir to `<host>/mcp/brain/` and writes the host MCP config:

```bash
./install.sh --bundle brain --deps --mcp    # Claude Code → .mcp.json (command=<venv>/bin/python, args=[…/mcp/brain/brain_mcp.py])
./brain mcp-config                          # print the JSON block for another host
```
Debug: `BRAIN_SKILLS=… BRAIN_DB=… "<venv>/bin/python" brain_mcp.py` (stdio).
