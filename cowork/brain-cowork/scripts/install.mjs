#!/usr/bin/env node
import {
  chmodSync, copyFileSync, existsSync, mkdirSync,
  readFileSync, renameSync, writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync, spawnSync } from "node:child_process";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const pluginDir = resolve(scriptDir, "..");
const home = homedir();

if (process.platform !== "darwin") {
  console.error("ERROR: This installer supports macOS only.");
  process.exit(1);
}

function fail(msg) { console.error(`ERROR: ${msg}`); process.exit(1); }

// Load brain.config.json
const configPath = join(pluginDir, "brain.config.json");
if (!existsSync(configPath))
  fail(`brain.config.json not found at ${configPath}\nCopy brain.config.example.json to brain.config.json and fill in your values.`);

const config = JSON.parse(readFileSync(configPath, "utf8"));
const { brainName, displayName, mcpEndpoint, apiKeyEnvVar } = config;
const gatewayUrl = config.codemie?.gatewayUrl;
const port = Number(config.port || 4318);

for (const [f, v] of [["brainName", brainName], ["displayName", displayName],
    ["mcpEndpoint", mcpEndpoint], ["apiKeyEnvVar", apiKeyEnvVar], ["codemie.gatewayUrl", gatewayUrl]])
  if (!v || typeof v !== "string" || !v.trim()) fail(`brain.config.json missing required field: ${f}`);

if (!/^https:\/\//.test(mcpEndpoint)) fail("mcpEndpoint must be an HTTPS URL");
if (!/^[a-z][a-z0-9-]*$/.test(brainName)) fail("brainName must be kebab-case (lowercase letters, digits, hyphens)");
if (/[/\\]/.test(displayName)) fail("displayName must not contain path separators (/ or \\)");
if (/\.\./.test(displayName) || displayName.trim() !== displayName)
  fail("displayName must not contain '..' or leading/trailing whitespace");
{
  const resolved = join(home, "Library", "Application Support", displayName);
  if (!resolved.startsWith(join(home, "Library", "Application Support") + "/"))
    fail(`displayName resolves outside Application Support — got: ${resolved}`);
}

// Step 1: Validate CodeMie Gateway
const configLibrary = join(home, "Library", "Application Support", "Claude-3p", "configLibrary");
const metaPath = join(configLibrary, "_meta.json");
if (!existsSync(metaPath))
  fail(`CodeMie Gateway not configured. Run: codemie proxy connect --claude-desktop --url ${gatewayUrl}`);
const { appliedId } = JSON.parse(readFileSync(metaPath, "utf8"));
const gatewayConfigPath = join(configLibrary, `${appliedId}.json`);
if (!appliedId || !existsSync(gatewayConfigPath))
  fail("Applied Gateway config not found. Re-run: codemie proxy connect --claude-desktop");
console.log("✓ CodeMie Gateway found");

// Step 2: Read API key
function parseEnvFile(p) {
  if (!existsSync(p)) return undefined;
  for (const raw of readFileSync(p, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const i = line.indexOf("=");
    if (i < 1 || line.slice(0, i).trim() !== apiKeyEnvVar) continue;
    let v = line.slice(i + 1).trim();
    if (v.length >= 2 && v[0] === v.at(-1) && ["'", '"'].includes(v[0])) v = v.slice(1, -1);
    if (v) return v;
  }
}
const secret = process.env[apiKeyEnvVar]
  || parseEnvFile(join(process.cwd(), ".env"))
  || parseEnvFile(join(pluginDir, ".env"));
if (!secret) fail(`${apiKeyEnvVar} not found. Add it to ${join(pluginDir, ".env")} or set it as an environment variable.`);
console.log(`✓ ${apiKeyEnvVar} found`);

// Step 3: Write credentials file
const supportDir = join(home, "Library", "Application Support", displayName);
mkdirSync(supportDir, { recursive: true });
const credentialsPath = join(supportDir, "credentials.env");
writeFileSync(credentialsPath, `${apiKeyEnvVar}=${secret}\n`, { mode: 0o600 });
chmodSync(credentialsPath, 0o600);
console.log(`✓ Credentials → ${credentialsPath}`);

// Step 4: Copy bridge
const bridgeDest = join(supportDir, "brain_mcp_bridge.mjs");
copyFileSync(join(scriptDir, "brain_mcp_bridge.mjs"), bridgeDest);
console.log(`✓ Bridge → ${bridgeDest}`);

// Step 5: Write plist — stamps BRAIN_MCP_ENDPOINT and BRAIN_API_KEY_VAR as env vars
// so the bridge (copied outside the plugin dir) never needs brain.config.json at runtime.
const label = `com.epam.${brainName}-mcp-bridge`;
const logsDir = join(home, "Library", "Logs", `${brainName}-mcp-bridge`);
mkdirSync(logsDir, { recursive: true });
mkdirSync(join(home, "Library", "LaunchAgents"), { recursive: true });

function xe(s) {
  return String(s).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;");
}
const plist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>${label}</string>
<key>ProgramArguments</key><array>
  <string>${xe(process.execPath)}</string>
  <string>${xe(bridgeDest)}</string>
</array>
<key>EnvironmentVariables</key><dict>
  <key>BRAIN_MCP_ENDPOINT</key><string>${xe(mcpEndpoint)}</string>
  <key>BRAIN_API_KEY_VAR</key><string>${xe(apiKeyEnvVar)}</string>
  <key>BRAIN_ENV_FILE</key><string>${xe(credentialsPath)}</string>
  <key>BRAIN_BRIDGE_PORT</key><string>${port}</string>
</dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>${xe(join(logsDir, "stdout.log"))}</string>
<key>StandardErrorPath</key><string>${xe(join(logsDir, "stderr.log"))}</string>
</dict></plist>\n`;
const plistPath = join(home, "Library", "LaunchAgents", `${label}.plist`);
writeFileSync(plistPath, plist, { mode: 0o600 });
chmodSync(plistPath, 0o600);
console.log(`✓ Plist → ${plistPath}`);

// Step 6: Register MCP server in Gateway config
const gwConfig = JSON.parse(readFileSync(gatewayConfigPath, "utf8"));
const rawServers = gwConfig.managedMcpServers ?? "[]";
const servers = typeof rawServers === "string" ? JSON.parse(rawServers) : rawServers;
if (!Array.isArray(servers)) fail("Unsupported managedMcpServers format in Gateway config.");
const next = servers.filter(s => !(s && typeof s === "object" && s.name === brainName));
next.push({ name: brainName, url: `http://127.0.0.1:${port}/mcp`, transport: "http", oauth: false });
const stamp = new Date().toISOString().replaceAll(/[-:.]/g,"");
copyFileSync(gatewayConfigPath, `${gatewayConfigPath}.backup-${stamp}`);
gwConfig.managedMcpServers = JSON.stringify(next);
const tmpGw = `${gatewayConfigPath}.tmp`;
writeFileSync(tmpGw, JSON.stringify(gwConfig, null, 2) + "\n", { mode: 0o600 });
chmodSync(tmpGw, 0o600);
renameSync(tmpGw, gatewayConfigPath);
console.log(`✓ Registered '${brainName}' in Gateway (backup: .backup-${stamp})`);

// Step 7: Generate .claude-plugin/plugin.json
const pluginJsonDir = join(pluginDir, ".claude-plugin");
mkdirSync(pluginJsonDir, { recursive: true });
const pluginJsonPath = join(pluginJsonDir, "plugin.json");
writeFileSync(pluginJsonPath, JSON.stringify({
  "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
  name: brainName, displayName, version: "1.0.0",
  description: `Brain knowledge assistant for ${displayName}.`,
  author: { name: "Applied AI" },
}, null, 2) + "\n");
console.log(`✓ Generated ${pluginJsonPath}`);

// Step 8: Bootstrap + kickstart LaunchAgent
const domain = `gui/${process.getuid()}`;
spawnSync("launchctl", ["bootout", domain, plistPath]);
execFileSync("launchctl", ["bootstrap", domain, plistPath], { stdio: "inherit" });
execFileSync("launchctl", ["kickstart", "-k", `${domain}/${label}`], { stdio: "inherit" });
console.log("✓ LaunchAgent started");

// Step 9: Health check (20 × 250ms = 5s max)
let healthy = false;
for (let i = 0; i < 20; i++) {
  try { const r = await fetch(`http://127.0.0.1:${port}/healthz`); if (r.ok) { healthy = true; break; } } catch {}
  await new Promise(r => setTimeout(r, 250));
}
if (!healthy) fail(`Bridge did not become healthy. Logs: ${join(logsDir, "stderr.log")}`);
console.log(`✓ Bridge healthy at http://127.0.0.1:${port}/mcp`);

// Step 10: Restart Claude Desktop
spawnSync("osascript", ["-e", 'tell application "Claude" to quit']);
spawnSync("open", ["-a", "Claude"]);
console.log("✓ Claude Desktop restarted");

// Step 11: Build dist ZIP — delegate to build-zip.mjs (single source of truth)
const distDir = join(pluginDir, "dist");
mkdirSync(distDir, { recursive: true });
const zipPath = join(distDir, `${brainName}-1.0.0.zip`);
execFileSync("node", [
  join(scriptDir, "build-zip.mjs"),
  "--plugin-dir", pluginDir,
  "--brain-name", brainName,
  "--plugin-json", pluginJsonPath,
  "--out", zipPath,
], { stdio: "inherit" });
console.log("");
console.log(`✓ ${displayName} installed. Upload ${zipPath} in Customize → Plugins → Add.`);
console.log(`  Then open Cowork and run: /${brainName}`);
