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
  readFileSync, writeFileSync,
} from "node:fs";
import { join } from "node:path";
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

if (!pluginDir)  fail("--plugin-dir required");
if (!brainName)  fail("--brain-name required");
if (!pluginJson) fail("--plugin-json required");
if (!outPath)    fail("--out required");

// Read + stamp SKILL.md in memory only — never touch the source file
const skillSrcPath = join(pluginDir, "skills", "brain-librarian", "SKILL.md");
if (!existsSync(skillSrcPath)) fail(`SKILL.md not found at ${skillSrcPath}`);
const stampedSkill = readFileSync(skillSrcPath, "utf8")
  .replace(/^name: brain-librarian$/m, `name: ${brainName}`);

// Build staging dir next to the output ZIP
const stageDir = outPath.replace(/\.zip$/, "") + "-stage";
mkdirSync(stageDir, { recursive: true });

const copyDir = (src, dst) => {
  mkdirSync(dst, { recursive: true });
  for (const entry of readdirSync(src, { withFileTypes: true })) {
    if (entry.name === "brain-librarian" && src.endsWith("skills")) continue; // stamped separately
    if (entry.name === ".env") continue; // never copy credentials
    const s = join(src, entry.name);
    const d = join(dst, entry.name);
    if (entry.isDirectory()) copyDir(s, d);
    else copyFileSync(s, d);
  }
};

// skills/ — stamped SKILL.md first, then the rest
const stageSkillDir = join(stageDir, "skills", "brain-librarian");
mkdirSync(stageSkillDir, { recursive: true });
writeFileSync(join(stageSkillDir, "SKILL.md"), stampedSkill);
copyDir(join(pluginDir, "skills"), join(stageDir, "skills"));

// docs/
if (existsSync(join(pluginDir, "docs")))
  copyDir(join(pluginDir, "docs"), join(stageDir, "docs"));

// README.md
const readmeSrc = join(pluginDir, "README.md");
if (existsSync(readmeSrc)) copyFileSync(readmeSrc, join(stageDir, "README.md"));

// .claude-plugin/plugin.json
const stagePlugin = join(stageDir, ".claude-plugin");
mkdirSync(stagePlugin, { recursive: true });
copyFileSync(pluginJson, join(stagePlugin, "plugin.json"));

// Zip from staging dir
const result = spawnSync("zip", ["-r", outPath, "."], { cwd: stageDir, stdio: "inherit" });
spawnSync("rm", ["-rf", stageDir]);

if (result.status !== 0) fail("zip failed");
console.log(`✓ ZIP → ${outPath}`);
