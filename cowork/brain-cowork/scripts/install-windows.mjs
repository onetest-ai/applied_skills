#!/usr/bin/env node
// Windows installer — equivalent of install.mjs for macOS.
// Uses Windows Task Scheduler (schtasks) instead of launchctl/plist.
// Requires Node.js 18+ and PowerShell 5.1+ (both pre-installed on Windows 10/11).
//
// Usage (PowerShell):
//   node scripts/install-windows.mjs
import {
  copyFileSync, existsSync, mkdirSync,
  readFileSync, renameSync, writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync, spawnSync } from "node:child_process";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const pluginDir = resolve(scriptDir, "..");

if (process.platform !== "win32") {
  console.error("ERROR: This installer is for Windows only. On macOS use: node scripts/install.mjs");
  process.exit(1);
}

function fail(msg) { console.error(`ERROR: ${msg}`); process.exit(1); }

// Load brain.config.json
const configPath = join(pluginDir, "brain.config.json");
if (!existsSync(configPath))
  fail(`brain.config.json not found.\nCopy brain.config.example.json to brain.config.json and fill in your values.`);

let config;
try { config = JSON.parse(readFileSync(configPath, "utf8")); }
catch (e) { fail(`brain.config.json is not valid JSON: ${e.message}`); }

const { brainName, displayName, mcpEndpoint, apiKeyEnvVar } = config;
const gatewayUrl = config.codemie?.gatewayUrl;
const port = Number(config.port || 4318);

for (const [f, v] of [
  ["brainName", brainName], ["displayName", displayName],
  ["mcpEndpoint", mcpEndpoint], ["apiKeyEnvVar", apiKeyEnvVar],
  ["codemie.gatewayUrl", gatewayUrl],
])
  if (!v || typeof v !== "string" || !v.trim()) fail(`brain.config.json missing required field: ${f}`);

if (!/^https:\/\//.test(mcpEndpoint)) fail("mcpEndpoint must be an HTTPS URL");
if (!/^[a-z][a-z0-9-]*$/.test(brainName)) fail("brainName must be kebab-case (lowercase letters, digits, hyphens)");
if (/[/\\]/.test(displayName)) fail("displayName must not contain path separators");
if (/\.\./.test(displayName) || displayName.trim() !== displayName)
  fail("displayName must not contain '..' or leading/trailing whitespace");

// ---------------------------------------------------------------------------
// Step 1: Validate CodeMie Gateway
// ---------------------------------------------------------------------------
const configLibrary = join(process.env.LOCALAPPDATA, "Claude-3p", "configLibrary");
const metaPath = join(configLibrary, "_meta.json");
if (!existsSync(metaPath))
  fail(`CodeMie Gateway not configured.\nRun: codemie proxy connect --claude-desktop --url ${gatewayUrl}`);
let appliedId;
try { ({ appliedId } = JSON.parse(readFileSync(metaPath, "utf8"))); }
catch (e) { fail(`Cannot parse Gateway _meta.json: ${e.message}`); }
const gatewayConfigPath = join(configLibrary, `${appliedId}.json`);
if (!appliedId || !existsSync(gatewayConfigPath))
  fail("Applied Gateway config not found. Re-run: codemie proxy connect --claude-desktop");
console.log("✓ CodeMie Gateway found");

// ---------------------------------------------------------------------------
// Step 2: Read API key from env or .env file
// ---------------------------------------------------------------------------
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
if (!secret)
  fail(`${apiKeyEnvVar} not found.\nAdd it to ${join(pluginDir, ".env")} or set it as an environment variable.`);
console.log(`✓ ${apiKeyEnvVar} found`);

// ---------------------------------------------------------------------------
// Step 3: Write credentials file to AppData\Local\<displayName>\
// ---------------------------------------------------------------------------
const supportDir = join(process.env.LOCALAPPDATA, displayName);
mkdirSync(supportDir, { recursive: true });
const credentialsPath = join(supportDir, "credentials.env");
writeFileSync(credentialsPath, `${apiKeyEnvVar}=${secret}\n`);
// Restrict file to current user only via icacls (Windows equivalent of chmod 600)
spawnSync("icacls", [credentialsPath, "/inheritance:r", "/grant:r", `${process.env.USERNAME}:F`],
  { stdio: "inherit" });
console.log(`✓ Credentials → ${credentialsPath}`);

// ---------------------------------------------------------------------------
// Step 4: Copy bridge to AppData\Local\<displayName>\
// ---------------------------------------------------------------------------
const bridgeDest = join(supportDir, "brain_mcp_bridge.mjs");
copyFileSync(join(scriptDir, "brain_mcp_bridge.mjs"), bridgeDest);
console.log(`✓ Bridge → ${bridgeDest}`);

// ---------------------------------------------------------------------------
// Step 5: Create a wrapper .cmd so Task Scheduler can invoke node cleanly
// ---------------------------------------------------------------------------
const logsDir = join(process.env.LOCALAPPDATA, displayName, "logs");
mkdirSync(logsDir, { recursive: true });
const wrapperPath = join(supportDir, `${brainName}-bridge.cmd`);
writeFileSync(wrapperPath, [
  `@echo off`,
  `set BRAIN_MCP_ENDPOINT=${mcpEndpoint}`,
  `set BRAIN_API_KEY_VAR=${apiKeyEnvVar}`,
  `set BRAIN_ENV_FILE=${credentialsPath}`,
  `set BRAIN_BRIDGE_PORT=${port}`,
  `"${process.execPath}" "${bridgeDest}" >> "${join(logsDir, "stdout.log")}" 2>> "${join(logsDir, "stderr.log")}"`,
].join("\r\n") + "\r\n");
console.log(`✓ Wrapper → ${wrapperPath}`);

// ---------------------------------------------------------------------------
// Step 6: Register Task Scheduler job (runs at logon, restarts on failure)
// Equivalent of macOS LaunchAgent with KeepAlive=true
// ---------------------------------------------------------------------------
const taskName = `${brainName}-mcp-bridge`;

// Delete any existing task with this name first (ignore errors)
spawnSync("schtasks", ["/Delete", "/TN", taskName, "/F"], { stdio: "ignore" });

// Create new task: runs at logon for current user, restarts every 1 min on failure
const createResult = spawnSync("schtasks", [
  "/Create",
  "/TN", taskName,
  "/TR", `"${wrapperPath}"`,
  "/SC", "ONLOGON",
  "/RU", process.env.USERNAME,
  "/RL", "HIGHEST",
  "/F",
], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });

if (createResult.status !== 0)
  fail(`Failed to create scheduled task:\n${createResult.stderr}`);
console.log(`✓ Scheduled task '${taskName}' created (runs at logon)`);

// ---------------------------------------------------------------------------
// Step 7: Register MCP server in Gateway config
// ---------------------------------------------------------------------------
let gwConfig;
try { gwConfig = JSON.parse(readFileSync(gatewayConfigPath, "utf8")); }
catch (e) { fail(`Cannot parse Gateway config: ${e.message}`); }

const rawServers = gwConfig.managedMcpServers ?? "[]";
const servers = typeof rawServers === "string" ? JSON.parse(rawServers) : rawServers;
if (!Array.isArray(servers)) fail("Unsupported managedMcpServers format in Gateway config.");
const next = servers.filter(s => !(s && typeof s === "object" && s.name === brainName));
next.push({ name: brainName, url: `http://127.0.0.1:${port}/mcp`, transport: "http", oauth: false });

const stamp = new Date().toISOString().replace(/[-:.]/g, "");
copyFileSync(gatewayConfigPath, `${gatewayConfigPath}.backup-${stamp}`);
gwConfig.managedMcpServers = JSON.stringify(next);
const tmpGw = `${gatewayConfigPath}.tmp`;
writeFileSync(tmpGw, JSON.stringify(gwConfig, null, 2) + "\n");
renameSync(tmpGw, gatewayConfigPath);
console.log(`✓ Registered '${brainName}' in Gateway (backup: .backup-${stamp})`);

// ---------------------------------------------------------------------------
// Step 8: Start the task immediately (don't wait for next logon)
// ---------------------------------------------------------------------------
const runResult = spawnSync("schtasks", ["/Run", "/TN", taskName],
  { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
if (runResult.status !== 0)
  fail(`Failed to start scheduled task:\n${runResult.stderr}`);
console.log("✓ Bridge task started");

// ---------------------------------------------------------------------------
// Step 9: Health check (20 × 250ms = 5s max)
// ---------------------------------------------------------------------------
let healthy = false;
for (let i = 0; i < 20; i++) {
  try {
    const r = await fetch(`http://127.0.0.1:${port}/healthz`);
    if (r.ok) { healthy = true; break; }
  } catch {}
  await new Promise(r => setTimeout(r, 250));
}
if (!healthy)
  fail(`Bridge did not become healthy within 5s.\nCheck logs: ${logsDir}\nThen run manually: node "${bridgeDest}"`);
console.log(`✓ Bridge healthy at http://127.0.0.1:${port}/mcp`);

// ---------------------------------------------------------------------------
// Step 10: Restart Claude Desktop (Windows)
// ---------------------------------------------------------------------------
spawnSync("taskkill", ["/IM", "Claude.exe", "/F"], { stdio: "ignore" });
await new Promise(r => setTimeout(r, 1500));
// Find and relaunch Claude — try common install locations
const claudePaths = [
  join(process.env.LOCALAPPDATA, "Programs", "claude", "Claude.exe"),
  join(process.env.LOCALAPPDATA, "Programs", "Claude", "Claude.exe"),
  join(process.env.PROGRAMFILES, "Claude", "Claude.exe"),
];
const claudeExe = claudePaths.find(p => existsSync(p));
if (claudeExe) {
  spawnSync("cmd", ["/c", "start", "", claudeExe], { detached: true, stdio: "ignore" });
  console.log("✓ Claude Desktop restarted");
} else {
  console.log("⚠ Could not find Claude.exe — please restart Claude Desktop manually.");
}

// ---------------------------------------------------------------------------
// Step 11: Build dist ZIP
// ---------------------------------------------------------------------------
const distDir = join(pluginDir, "dist");
mkdirSync(distDir, { recursive: true });
const zipPath = join(distDir, `${brainName}-cowork-1.0.0.zip`);
execFileSync("node", [
  join(scriptDir, "build-zip.mjs"),
  "--plugin-dir", pluginDir,
  "--brain-name", brainName,
  "--plugin-json", join(pluginDir, ".claude-plugin", "plugin.json"),
  "--out", zipPath,
  "--cowork",
], { stdio: "inherit" });

console.log("");
console.log(`✓ ${displayName} installed.`);
console.log(`  Upload ${zipPath} in Customize → Plugins → Add.`);
console.log(`  Then open Cowork and run: /${brainName}`);
console.log("");
console.log(`  Bridge logs: ${logsDir}`);
console.log(`  To uninstall: schtasks /Delete /TN ${taskName} /F`);
