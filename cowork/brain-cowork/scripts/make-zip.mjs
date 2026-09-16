#!/usr/bin/env node
// Guided ZIP builder — reads brain.config.json, prompts for missing values,
// generates plugin.json, and produces the dist ZIP. No flags required.
// Usage: node scripts/make-zip.mjs
import { createInterface } from "node:readline";
import {
  existsSync, mkdirSync, readFileSync, writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const pluginDir = resolve(scriptDir, "..");

function fail(msg) { console.error(`\nERROR: ${msg}\n`); process.exit(1); }

// ---------------------------------------------------------------------------
// Readline helper — buffers all stdin lines upfront, serves from queue.
// Works reliably with both TTY and piped stdin on Node 18–24.
// ---------------------------------------------------------------------------
const lineQueue = [];
let lineResolve = null;

const rl = createInterface({ input: process.stdin, output: process.stdout, terminal: false });
rl.on("line", line => {
  if (lineResolve) { const r = lineResolve; lineResolve = null; r(line); }
  else lineQueue.push(line);
});
// Resolve any pending readLine() when stdin closes (Ctrl+D, pipe EOF, aborted prompt)
rl.on("close", () => { if (lineResolve) { const r = lineResolve; lineResolve = null; r(""); } });

function readLine() {
  if (lineQueue.length) return Promise.resolve(lineQueue.shift());
  return new Promise(resolve => { lineResolve = resolve; });
}

async function prompt(question, defaultVal) {
  const hint = defaultVal ? ` [${defaultVal}]` : "";
  process.stdout.write(`  ${question}${hint}: `);
  const answer = (await readLine()).trim();
  return answer || defaultVal || "";
}

// ---------------------------------------------------------------------------
// Step 1: Load or guide through brain.config.json
// ---------------------------------------------------------------------------
const configPath = join(pluginDir, "brain.config.json");

let config = {};
if (existsSync(configPath)) {
  try { config = JSON.parse(readFileSync(configPath, "utf8")); }
  catch { fail(`brain.config.json is not valid JSON. Fix it and re-run.`); }
}

const REQUIRED = [
  { key: "brainName",      label: "brainName (kebab-case, e.g. acme-brain)",       validate: v => !/^[a-z][a-z0-9-]*$/.test(v) ? "must be kebab-case" : v === "my-project-brain" ? "replace the placeholder with your real brain name" : null },
  { key: "displayName",    label: "displayName (human name, e.g. Acme Brain)",      validate: v => v === "My Project Brain" ? "replace the placeholder with your real display name" : null },
  { key: "mcpEndpoint",    label: "mcpEndpoint (HTTPS URL of your MCP brain)",       validate: v => !/^https:\/\//.test(v) ? "must start with https://" : v.includes("example.com") ? "replace the placeholder URL with your real endpoint" : null },
  { key: "apiKeyEnvVar",   label: "apiKeyEnvVar (env var name, e.g. ACME_BRAIN_API_KEY)", validate: () => null },
];

let dirty = false;
console.log("\n=== Brain Cowork — ZIP Builder ===\n");

for (const { key, label, validate } of REQUIRED) {
  // Loop until value is present AND valid (handles both missing and placeholder values)
  while (true) {
    const current = config[key] ? String(config[key]).trim() : "";
    const err = current ? validate(current) : "required";
    if (!err) break; // value is good — move on

    if (current) console.log(`  ${key}: "${current}" — ${err}`);
    const val = (await prompt(label)).trim();
    if (!val) { console.log("    (required — cannot be empty)"); continue; }
    const verr = validate(val);
    if (verr) { console.log(`    (${verr})`); continue; }
    config[key] = val;
    dirty = true;
    break;
  }
}

// Ensure codemie.gatewayUrl
if (!config.codemie?.gatewayUrl) {
  const url = await prompt("codemie.gatewayUrl (CodeMie instance URL)", "https://codemie.lab.epam.com");
  config.codemie = { ...(config.codemie || {}), gatewayUrl: url };
  dirty = true;
}

config.port = Number(config.port || 4318);

rl.close();

// Save updated config if anything changed
if (dirty) {
  writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n");
  console.log(`\n  ✓ Saved brain.config.json`);
}

const { brainName, displayName } = config;

// ---------------------------------------------------------------------------
// Step 2: Generate plugin.json
// ---------------------------------------------------------------------------
const pluginJsonDir = join(pluginDir, ".claude-plugin");
mkdirSync(pluginJsonDir, { recursive: true });
const pluginJsonPath = join(pluginJsonDir, "plugin.json");
writeFileSync(pluginJsonPath, JSON.stringify({
  "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
  name: brainName, displayName, version: "1.0.0",
  description: `Brain knowledge assistant for ${displayName}.`,
  author: { name: "Applied AI" },
}, null, 2) + "\n");
console.log(`  ✓ Generated .claude-plugin/plugin.json`);

// ---------------------------------------------------------------------------
// Step 3: Build ZIP via build-zip.mjs
// ---------------------------------------------------------------------------
mkdirSync(join(pluginDir, "dist"), { recursive: true });
const zipPath = join(pluginDir, "dist", `${brainName}-1.0.0.zip`);

console.log(`\n  Building ZIP → ${zipPath}\n`);
execFileSync("node", [
  join(scriptDir, "build-zip.mjs"),
  "--plugin-dir", pluginDir,
  "--brain-name", brainName,
  "--plugin-json", pluginJsonPath,
  "--out", zipPath,
], { stdio: "inherit" });

console.log(`
Done! Next steps:
  1. Open Claude Desktop → Customize → Plugins → Add
  2. Upload: ${zipPath}
  3. Enable the plugin
  4. In Cowork, run: /${brainName}
`);
