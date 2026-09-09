# MCP servers

Top-level `mcp/` holds MCP servers — the **tool layer** that fronts the skills, so an
agent answers by calling tools instead of shelling out to scripts. It sits alongside
`skills/` (the SKILL.md capabilities) and `bundles/` (curated install sets).

```
mcp/<name>/
  server.json      # manifest (required)
  <entry>.py       # the server entry point (one or more declared transports)
  README.md        # what it does
```

## `server.json`

| Field | Meaning |
|---|---|
| `name` | server id (must equal the dir name); also the key written into the host config |
| `description` | one line |
| `entry` | the script the host launches (relative to `mcp/<name>/`) |
| `transport` | Supported transport string or list, e.g. `["stdio", "streamable-http"]`; installers register stdio by default |
| `runtime` | `bundle-venv` → launch with the bundle's venv python; `system` → plain `python3` |
| `bundle` | the bundle whose venv runs it (for `runtime: bundle-venv`) |
| `env` | env keys the server reads (`<skills>`/`<db>`/`<catalog>`/`<assets>` are filled by the installer when discovered; unknown/optional ones use server auto-discovery) |

## Install / register

```bash
./install.sh --bundle brain --deps --mcp
```
`--mcp` (needs `--bundle`) copies each MCP server the bundle names (`factory.json` →
`mcp.servers`) into `<host>/mcp/<name>/`, then registers it:
- **Claude Code** → merges into `<root>/.mcp.json` under `mcpServers.<name>`.
- **other hosts (dsh/codex/copilot)** → merges into `<host>/mcp.json` (e.g. `.dsh/mcp.json`), registered from inside the host dir.
Both set `command` = the bundle venv's python, `args` = `[<host>/mcp/<name>/<entry>, "--transport", "stdio"]`, and env `BRAIN_SKILLS` (+ `BRAIN_DB`/`BRAIN_CATALOG`/`BRAIN_ASSETS` when discovered). Registration stays on local stdio; HTTP and secrets such as `BRAIN_API_KEY` are explicit deployment choices and installers never write secret placeholders into host configs.

The server is registered with the venv interpreter, so the agent never launches Python
itself — it calls the tools. (Bootstrapping the server process — the venv path in the
config and its working directory — is the one place "where does it run" still matters.)

## Servers
- **brain** — governed semantic tool layer over `knowledge.sqlite`, with local stdio and opt-in Streamable HTTP. See [`brain/README.md`](brain/README.md).
