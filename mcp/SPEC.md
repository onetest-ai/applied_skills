# MCP servers

Top-level `mcp/` holds MCP servers — the **tool layer** that fronts the skills, so an
agent answers by calling tools instead of shelling out to scripts. It sits alongside
`skills/` (the SKILL.md capabilities) and `bundles/` (curated install sets).

```
mcp/<name>/
  server.json      # manifest (required)
  <entry>.py       # the server (stdio JSON-RPC 2.0; stdlib where possible)
  README.md        # what it does
```

## `server.json`

| Field | Meaning |
|---|---|
| `name` | server id (must equal the dir name); also the key written into the host config |
| `description` | one line |
| `entry` | the script the host launches (relative to `mcp/<name>/`) |
| `transport` | `stdio` (only stdio is supported today) |
| `runtime` | `bundle-venv` → launch with the bundle's venv python; `system` → plain `python3` |
| `bundle` | the bundle whose venv runs it (for `runtime: bundle-venv`) |
| `env` | env keys the server reads (`<skills>`/`<db>`/`<catalog>` are filled by the installer; unknown/optional ones are left to the server's own auto-discovery) |

## Install / register

```bash
./install.sh --bundle brain --deps --mcp
```
`--mcp` (needs `--bundle`) copies each MCP server the bundle names (`factory.json` →
`mcp.servers`) into `<host>/mcp/<name>/`, then registers it:
- **Claude Code** → merges into `<root>/.mcp.json` under `mcpServers.<name>`, with
  `command` = the bundle venv's python and `args` = `[<host>/mcp/<name>/<entry>]`.
- **other hosts** → the installer prints the JSON block to paste (or run `./brain mcp-config`).

The server is registered with the venv interpreter, so the agent never launches Python
itself — it calls the tools. (Bootstrapping the server process — the venv path in the
config and its working directory — is the one place "where does it run" still matters.)

## Servers
- **brain** — deterministic tool layer over `knowledge.sqlite` (search/sql/metric/graph/verify). See [`brain/README.md`](brain/README.md).
