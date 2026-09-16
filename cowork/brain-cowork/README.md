# Brain Cowork Plugin — Generic Template

A config-driven Claude Cowork plugin template. Edit one file, run one command,
deploy a private Brain knowledge assistant for any project.

## Quick start

```bash
# 1. Copy and fill in config
cp brain.config.example.json brain.config.json
# edit brain.config.json

# 2. Add your API key
echo "MY_BRAIN_API_KEY=<your-key>" > .env

# 3. Install
node scripts/install.mjs
```

See `docs/ONBOARDING.md` for full prerequisites.

## What gets installed

| Component | Location |
|---|---|
| MCP bridge | `~/Library/Application Support/<displayName>/brain_mcp_bridge.mjs` |
| Credentials | `~/Library/Application Support/<displayName>/credentials.env` (chmod 600) |
| macOS service | `~/Library/LaunchAgents/com.epam.<brainName>-mcp-bridge.plist` |
| Plugin ZIP | `dist/<brainName>-1.0.0.zip` — upload to Cowork Plugins |

## Security

No credential is in the ZIP or committed files. The bridge reads its endpoint
and key-var name from plist env vars set at install time. See `docs/SECURITY.md`.

## Reuse for 100 projects

Each team clones this repo, fills in `brain.config.json` with their own
`brainName`, `mcpEndpoint`, and `apiKeyEnvVar` — the code never changes.
