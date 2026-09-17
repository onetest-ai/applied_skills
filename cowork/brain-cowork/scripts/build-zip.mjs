#!/usr/bin/env node
// Extracts the ZIP-building step from install.mjs so it can run standalone
// and be tested without CodeMie Gateway, launchctl, or a real API key.
//
// Usage:
//   node scripts/build-zip.mjs \
//     --plugin-dir <path>     \
//     --brain-name <name>     \
//     --plugin-json <path>    \
//     --out <zip-path>
import {
  copyFileSync, existsSync, mkdirSync, readdirSync,
  readFileSync, rmSync, unlinkSync, writeFileSync,
} from "node:fs";
import { join, resolve } from "node:path";
import { platform } from "node:os";
import { spawnSync } from "node:child_process";

function fail(msg) { console.error(`ERROR: ${msg}`); process.exit(1); }

// Parse args
const args = process.argv.slice(2);
function arg(name) {
  const i = args.indexOf(name);
  return i !== -1 ? args[i + 1] : undefined;
}
const pluginDir  = arg("--plugin-dir");
const brainName  = arg("--brain-name");
const pluginJson = arg("--plugin-json");
const outPath    = arg("--out");
const coworkMode = args.includes("--cowork"); // skip mcpServers injection (Cowork uses Gateway)

if (!pluginDir)  fail("--plugin-dir required");
if (!brainName)  fail("--brain-name required");
if (!pluginJson) fail("--plugin-json required");
if (!outPath)    fail("--out required");

// Resolve to absolute paths so zip (which runs with cwd=stageDir) can find them
const absPluginDir  = resolve(pluginDir);
const absPluginJson = resolve(pluginJson);
const absOutPath    = resolve(outPath);

if (!/^[a-z][a-z0-9-]*$/.test(brainName))
  fail(`--brain-name must be kebab-case (lowercase letters, digits, hyphens): got "${brainName}"`);

// Read + stamp SKILL.md in memory only — never touch the source file
const skillSrcPath = join(absPluginDir, "skills", "brain-librarian", "SKILL.md");
if (!existsSync(skillSrcPath)) fail(`SKILL.md not found at ${skillSrcPath}`);
const skillSrc = readFileSync(skillSrcPath, "utf8");
if (!/^name: brain-librarian$/m.test(skillSrc))
  fail("SKILL.md is missing 'name: brain-librarian' — template may be corrupted");
const stampedSkill = skillSrc.replace(/^name: brain-librarian$/m, `name: ${brainName}`);

// Build staging dir next to the output ZIP (absolute path)
const stageDir = absOutPath.replace(/\.zip$/i, "") + "-stage";
mkdirSync(stageDir, { recursive: true });

const copyDir = (src, dst) => {
  mkdirSync(dst, { recursive: true });
  for (const entry of readdirSync(src, { withFileTypes: true })) {
    if (entry.name === "brain-librarian" && src.endsWith("skills")) continue; // stamped separately
    if (entry.name === ".env" || entry.name.startsWith(".env.")) continue; // never copy credentials
    if (entry.name === "brain.config.json") continue; // never copy config
    if (entry.name === ".DS_Store" || entry.name === "Thumbs.db") continue; // OS metadata
    const s = join(src, entry.name);
    const d = join(dst, entry.name);
    if (entry.isDirectory()) copyDir(s, d);
    else copyFileSync(s, d);
  }
};

// skills/ — stamped SKILL.md written under brainName folder first, then copy the rest
// brain-librarian template is kept in ZIP so the scripts are self-contained on a fresh checkout.
// copyDir skips it from the recursive copy because it's handled separately (stamped above).
const stageSkillDir = join(stageDir, "skills", brainName);
mkdirSync(stageSkillDir, { recursive: true });
writeFileSync(join(stageSkillDir, "SKILL.md"), stampedSkill);
// Also keep the brain-librarian template itself so build-zip.mjs works after unzip on Windows
const stageLibrarian = join(stageDir, "skills", "brain-librarian");
mkdirSync(stageLibrarian, { recursive: true });
copyFileSync(skillSrcPath, join(stageLibrarian, "SKILL.md"));
copyDir(join(absPluginDir, "skills"), join(stageDir, "skills"));

// scripts/ — include installers and bridge so the ZIP is self-contained on Windows
const scriptsToBundle = [
  "install-windows.mjs",
  "install.mjs",
  "build-zip.mjs",
  "brain_mcp_bridge.mjs",
];
const stageScripts = join(stageDir, "scripts");
mkdirSync(stageScripts, { recursive: true });
for (const f of scriptsToBundle) {
  const src = join(absPluginDir, "scripts", f);
  if (existsSync(src)) copyFileSync(src, join(stageScripts, f));
}

// brain.config.example.json — copy template (never the real config)
const exampleConfig = join(absPluginDir, "brain.config.example.json");
if (existsSync(exampleConfig))
  copyFileSync(exampleConfig, join(stageDir, "brain.config.example.json"));

// docs/
if (existsSync(join(absPluginDir, "docs")))
  copyDir(join(absPluginDir, "docs"), join(stageDir, "docs"));

// README.md
const readmeSrc = join(absPluginDir, "README.md");
if (existsSync(readmeSrc)) copyFileSync(readmeSrc, join(stageDir, "README.md"));

// .claude-plugin/plugin.json — augment with mcpServers + userConfig so the
// user is prompted for the API key at plugin-enable time (no install.mjs needed).
const stagePlugin = join(stageDir, ".claude-plugin");
mkdirSync(stagePlugin, { recursive: true });

const configPath = join(absPluginDir, "brain.config.json");
let mcpEndpoint = "";
let apiKeyEnvVar = "BRAIN_API_KEY";
if (existsSync(configPath)) {
  let cfg;
  try {
    cfg = JSON.parse(readFileSync(configPath, "utf8"));
  } catch (e) {
    fail(`brain.config.json is not valid JSON: ${e.message}`);
  }
  if (cfg.mcpEndpoint) {
    if (!/^https:\/\//.test(cfg.mcpEndpoint))
      fail(`brain.config.json mcpEndpoint must start with https:// — got: ${cfg.mcpEndpoint}`);
    mcpEndpoint = cfg.mcpEndpoint;
  }
  if (cfg.apiKeyEnvVar) apiKeyEnvVar = cfg.apiKeyEnvVar;
}

const basePlugin = JSON.parse(readFileSync(absPluginJson, "utf8"));

if (mcpEndpoint && !coworkMode) {
  // Declare the MCP server — key injected from userConfig secure storage.
  // Skip in --cowork mode: CodeMie Gateway handles the MCP connection instead.
  basePlugin.mcpServers = {
    [brainName]: {
      type: "http",
      url: mcpEndpoint,
      headers: {
        "X-API-Key": "${user_config.apiKey}",
      },
    },
  };
  // Prompt user for the API key when they enable the plugin.
  basePlugin.userConfig = {
    apiKey: {
      description: `API key for ${basePlugin.displayName || brainName} MCP server`,
      sensitive: true,
    },
  };
}

writeFileSync(join(stagePlugin, "plugin.json"), JSON.stringify(basePlugin, null, 2) + "\n");

// Remove any existing output ZIP so we always produce a fresh archive (not a merge)
if (existsSync(absOutPath)) unlinkSync(absOutPath);

// Zip from staging dir — cross-platform: PowerShell on Windows, zip on macOS/Linux
let result;
if (platform() === "win32") {
  // ZipFile::CreateFromDirectory preserves directory structure including dotfile dirs (.claude-plugin/).
  // Compress-Archive with wildcards silently drops dotfiles in PowerShell 5.1 on Windows 10.
  const psCmd = [
    "Add-Type -AssemblyName System.IO.Compression.FileSystem;",
    `[System.IO.Compression.ZipFile]::CreateFromDirectory('${stageDir}', '${absOutPath}')`,
  ].join(" ");
  result = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", psCmd], { stdio: "inherit" });
} else {
  result = spawnSync("zip", ["-r", absOutPath, "."], { cwd: stageDir, stdio: "inherit" });
}

// Clean up staging dir only after checking exit code so failures are diagnosable
if (result.status !== 0) {
  console.error(`Staging directory left for inspection: ${stageDir}`);
  fail("zip failed");
}
rmSync(stageDir, { recursive: true, force: true });
console.log(`✓ ZIP → ${outPath}`);
