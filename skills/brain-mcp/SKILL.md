---
name: brain-mcp
description: The brain's MCP server — the deterministic TOOL layer over the local knowledge.sqlite. A dependency-free stdio JSON-RPC server (stdlib only, no SDK/web framework) exposing cited retrieval + computed-number tools (search, sql, metric, graph, verify) so an agent answers by calling tools instead of shelling out to Python. Owns its own venv + skills + store connection. Use to serve/register the brain over MCP, or when a host asks how to connect to the brain.
---

# brain-mcp (the tool layer)

A **dependency-free stdio MCP server** (line-delimited JSON-RPC 2.0, ~100 lines of
stdlib — no SDK, no web framework) that turns the local **`knowledge.sqlite`** brain
into a set of tools. The goal is to *hide the scripts behind tools*, not to run a
web server. It makes **who-does-what** unambiguous:

| Layer | Who | Does |
|---|---|---|
| **Tool layer** | this MCP server | owns the **venv + skills + store**; returns cited text / computed numbers. **Never reasons or narrates.** |
| **Reasoning layer** | the agent (MCP client) | decomposes the question, calls the tools, composes **one cited answer** with honest gaps |

The truthfulness rule lives at the tool boundary: **numbers are computed** (`sql`/`metric`, every value carries `source_file`), **meaning is cited** (`search`/`graph`, text hits — never figures).

## Tools
- **`which()`** — resolved store / catalog / skills (call first if unsure).
- **`search(query, k=5)`** — narrative lane: hybrid RAG (BM25+vector, RRF). Cited section hits; not for numbers.
- **`sql(query)`** — numbers lane: read-only `SELECT`/`WITH` over the store (guarded — write/DDL rejected). `{columns, rows}`.
- **`metric(name, grain?, entity?, entity_like?, month?, months?)`** — governed metric from `metrics.<corpus>.json` → exact `facts` values with `source_file`.
- **`graph(label?, relation?, kind?)`** — taxonomy: a node + its subclasses + tagged sections, or node/edge listings.
- **`verify()`** — per-lane row counts + empty-lane flag.

## How it resolves things (no hardcoding)
Same discovery contract as the `brain` launcher, all overridable by env:
- **store** — `BRAIN_DB` → `./knowledge.sqlite` → `./schema/knowledge.sqlite` (also relative to the project root).
- **catalog** — `BRAIN_CATALOG` → `schema/metrics.*.json`.
- **skills** — `BRAIN_SKILLS` → the dir this server lives in.

## Run / register
The server must run under the **brain venv** (it needs `mcp` + `sqlite-vec` + `fastembed`, all in `bundles/brain/requirements.txt`). The installer wires this for you:

```bash
./install.sh --bundle brain --deps --mcp          # copy skills, build the venv, register the server
#   → writes .mcp.json:  command=<venv>/bin/python  args=[…/brain-mcp/brain_mcp.py]  env={BRAIN_DB,BRAIN_SKILLS}
```
Manual: `./brain mcp-config` prints the JSON block to paste into a host that isn't Claude Code. Direct launch (debug): `"<venv>/bin/python" brain_mcp.py` (stdio).

Because the server is registered with the venv's interpreter, the agent never has to know **where** to run — it just calls the tools.
