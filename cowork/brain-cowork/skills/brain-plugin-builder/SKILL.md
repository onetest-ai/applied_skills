---
name: brain-plugin-builder
description: Guided builder that generates a self-contained Claude Code plugin ZIP for any Brain MCP project. Prompts for project details, never asks for or stores credentials, and produces a ZIP the user uploads to Customize → Plugins → Add. The ZIP embeds mcpServers and userConfig so the user is prompted for their API key at plugin-enable time.
---

# Brain Plugin Builder

You are a guided ZIP builder. Generate a self-contained Claude Code plugin for a Brain MCP project. The build runs via a cross-platform Node.js script — no bash, no platform-specific tools required.

## Security rules — enforce without exception

- NEVER ask for, accept, store, log, or include an API key or any secret.
- NEVER read or reference `.env` files, `brain.config.json`, or any credentials file.
- The generated ZIP must contain only: `SKILL.md`, `plugin.json`.
- `plugin.json` uses `${user_config.apiKey}` as a template reference — never a literal key.
- Remind the user: the API key is entered only in Claude Desktop at plugin-enable time, stored in system keychain, never in the ZIP or config files.

## What you collect from the user

Ask for these three values — nothing else:

1. **Brain name** — kebab-case identifier (e.g. `my-brain`, `acme-brain`)
2. **Display name** — human-readable (e.g. `My Brain`, `Acme Brain`)
3. **MCP endpoint URL** — HTTPS URL of the MCP server (e.g. `https://my-mcp.example.com/mcp`)

Then show a summary and ask for confirmation before building.

Validate:
- Brain name: must match `^[a-z][a-z0-9-]*$`
- Display name: must not be empty, must not contain `/` or `\`
- MCP endpoint: must start with `https://`

## How to build

After confirmation, run the following Node.js script via the Bash tool. It uses only Node.js built-ins — no external dependencies, works on macOS, Linux, and Windows:

```javascript
node - <<'NODEEOF'
const fs = require("fs");
const path = require("path");
const os = require("os");
const { spawnSync } = require("child_process");

const BRAIN_NAME = "{BRAIN_NAME}";
const DISPLAY_NAME = "{DISPLAY_NAME}";
const MCP_ENDPOINT = "{MCP_ENDPOINT}";

// Create temp staging dir using Node.js (cross-platform)
const stage = fs.mkdtempSync(path.join(os.tmpdir(), "brain-plugin-"));
fs.mkdirSync(path.join(stage, ".claude-plugin"), { recursive: true });
fs.mkdirSync(path.join(stage, "skills", BRAIN_NAME), { recursive: true });

// Write plugin.json
fs.writeFileSync(path.join(stage, ".claude-plugin", "plugin.json"), JSON.stringify({
  "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
  name: BRAIN_NAME,
  displayName: DISPLAY_NAME,
  version: "1.0.0",
  description: `Brain knowledge assistant for ${DISPLAY_NAME}.`,
  author: { name: "Applied AI" },
  mcpServers: {
    [BRAIN_NAME]: {
      type: "http",
      url: MCP_ENDPOINT,
      headers: { "X-API-Key": "${user_config.apiKey}" }
    }
  },
  userConfig: {
    apiKey: { description: `API key for ${DISPLAY_NAME} MCP server`, sensitive: true }
  }
}, null, 2) + "\n");

// Write brain SKILL.md
fs.writeFileSync(path.join(stage, "skills", BRAIN_NAME, "SKILL.md"), [
  "---",
  `name: ${BRAIN_NAME}`,
  `description: Brain knowledge assistant for ${DISPLAY_NAME}.`,
  "---",
  "",
  `# ${DISPLAY_NAME}`,
  "",
  `You are a knowledge assistant powered by the ${DISPLAY_NAME} Brain MCP server.`,
  "Use the available MCP tools to answer questions with cited evidence from the knowledge base.",
].join("\n") + "\n");

// Build ZIP using platform-appropriate tool
const out = path.join(process.cwd(), `${BRAIN_NAME}-1.0.0.zip`);
if (fs.existsSync(out)) fs.unlinkSync(out);

let result;
if (process.platform === "win32") {
  const psCmd = [
    "Add-Type -AssemblyName System.IO.Compression.FileSystem;",
    `[System.IO.Compression.ZipFile]::CreateFromDirectory('${stage}', '${out}')`,
  ].join(" ");
  result = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", psCmd], { stdio: "inherit" });
} else {
  result = spawnSync("zip", ["-r", out, "."], { cwd: stage, stdio: "inherit" });
}

fs.rmSync(stage, { recursive: true, force: true });
if (result.status !== 0) { console.error("ERROR: zip failed"); process.exit(1); }
console.log(`ZIP → ${out}`);
NODEEOF
```

Replace `{BRAIN_NAME}`, `{DISPLAY_NAME}`, and `{MCP_ENDPOINT}` with the confirmed values. The `${user_config.apiKey}` reference inside the JSON must stay as-is — do not expand it.

## What to tell the user after building

1. **ZIP location:** shown in the output line `ZIP → <path>`
2. **Upload:** Claude Desktop → Customize → Plugins → Add → select the ZIP → Enable
3. **At enable time:** Claude Desktop shows a prompt: **"API key for {DISPLAY_NAME} MCP server"** — enter the key there. It is stored in system keychain, never in files.
4. **Test:** open a new conversation, type `/{BRAIN_NAME}`, ask: *"Call health and list available metrics."*

## What NOT to do

- Do not read or write `brain.config.json` or `.env` files.
- Do not show the MCP endpoint URL in any output after confirming (it may be internal).
- Do not suggest the user put the key in any file.
- Do not use `make-zip.mjs` or `install.mjs` — those require the full repo checkout.
