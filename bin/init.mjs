#!/usr/bin/env node
/*
 * applied_skills installer (npx one-liner).
 *
 *   npx github:onetest-ai/applied_skills init [options]
 *
 * Installs the SKILL.md skills into each host's native skills dir — all hosts
 * use the same format, so this is a copy (or symlink), no translation.
 *
 *   claude   -> <root>/.claude/skills     (user: ~/.claude/skills)
 *   dsh      -> <root>/.dsh/skills         (user: ~/.dsh/skills)      [rank-100 project source]
 *   copilot  -> <root>/.github/skills      (user: ~/.copilot/skills)
 *   codex    -> <root>/.codex/skills       (user: ~/.codex/skills)
 *
 * Options:
 *   --target claude,dsh,copilot,codex   (default: all four)
 *   --bundle <name>                      install a curated bundle (bundles/<name>/factory.json)
 *   --optional                           with --bundle, also install its optionalSkills
 *   --deps                               build a DEDICATED venv (uv) for the skills' deps
 *   --venv <dir>                         where that venv lives (default ~/.brain/venv)
 *   --skills a,b,c                       (default: all; ignored when --bundle is given)
 *   --user                               install under $HOME instead of the project
 *   --symlink                            symlink instead of copy (edits reflect live)
 *   --dry-run                            preview only
 *
 *   npx github:onetest-ai/applied_skills init --bundle brain --deps
 */
import { existsSync, mkdirSync, rmSync, cpSync, symlinkSync, readdirSync, statSync, readFileSync, writeFileSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";
import { spawnSync } from "node:child_process";

const HOSTS = {
  claude:  { project: ".claude/skills",  user: join(homedir(), ".claude/skills") },
  dsh:     { project: ".dsh/skills",     user: join(homedir(), ".dsh/skills") },
  copilot: { project: ".github/skills",  user: join(homedir(), ".copilot/skills") },
  codex:   { project: ".codex/skills",   user: join(homedir(), ".codex/skills") },
};

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(ROOT, "skills");
const BUNDLES = join(ROOT, "bundles");

function parseArgs(argv) {
  const o = { targets: Object.keys(HOSTS), skills: null, bundle: null, optional: false, deps: false, mcp: false, venv: null, user: false, symlink: false, dry: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "init") continue;                       // npx passes the bin name as arg0
    else if (a === "--target") o.targets = argv[++i].split(",").map(s => s.trim()).filter(Boolean);
    else if (a === "--bundle" || a === "--factory") o.bundle = argv[++i];
    else if (a === "--optional") o.optional = true;
    else if (a === "--deps") o.deps = true;
    else if (a === "--mcp") o.mcp = true;
    else if (a === "--venv") o.venv = argv[++i];
    else if (a === "--skills") o.skills = argv[++i].split(",").map(s => s.trim()).filter(Boolean);
    else if (a === "--user") o.user = true;
    else if (a === "--symlink") o.symlink = true;
    else if (a === "--dry-run") o.dry = true;
    else if (a === "-h" || a === "--help") { help(); process.exit(0); }
    else { console.error(`unknown arg: ${a}`); process.exit(2); }
  }
  return o;
}
function help() {
  console.log("npx github:onetest-ai/applied_skills init [--target claude,dsh,copilot,codex]");
  console.log("  [--bundle <name> [--optional]] [--deps [--venv <dir>]] [--mcp] [--skills a,b,c] [--user] [--symlink] [--dry-run]");
}

// --mcp: install the bundle's MCP servers from mcp/<name>/ into <host>/mcp/<name>/
// and register each for local stdio (command = the bundle venv python).
// Streamable HTTP remains an explicit deployment choice.
function installMcp(o, targets, bases) {
  const factory = join(BUNDLES, o.bundle || "", "factory.json");
  if (!o.bundle || !existsSync(factory)) { console.error("error: --mcp needs --bundle"); process.exit(2); }
  const servers = (JSON.parse(readFileSync(factory, "utf8")).mcp || {}).servers || [];
  if (!servers.length) { console.log(`  (bundle '${o.bundle}' declares no mcp servers)`); return; }
  for (let t = 0; t < targets.length; t++) {
    const skillsDir = bases[t], hostDir = dirname(skillsDir);
    const py = join(o.venv || join(hostDir, "venv"), "bin", "python");
    // discover store + assets: prefer inside the host dir, then the project root
    let db = "";
    for (const c of [join(hostDir, "knowledge.sqlite"), join(process.cwd(), "knowledge.sqlite"), join(process.cwd(), "schema", "knowledge.sqlite")])
      if (existsSync(c)) { db = c; break; }
    let assets = "";
    for (const c of [join(hostDir, "assets"), join(process.cwd(), "assets")])
      if (existsSync(c)) { assets = c; break; }
    let catalog = "";
    for (const schemaDir of [join(process.cwd(), "schema"), join(hostDir, "schema")]) {
      if (!existsSync(schemaDir)) continue;
      const catalogs = readdirSync(schemaDir).filter(n => /^metrics\..+\.json$/.test(n)).sort();
      if (catalogs.length) { catalog = join(schemaDir, catalogs[0]); break; }
    }
    // Claude Code reads <root>/.mcp.json; other hosts read <host>/mcp.json (registered from inside)
    const conf = targets[t] === "claude" ? resolve(process.cwd(), ".mcp.json") : join(hostDir, "mcp.json");
    for (const name of servers) {
      const srcdir = join(ROOT, "mcp", name);
      if (!existsSync(srcdir)) { console.error(`  ! mcp/${name} not found in repo`); continue; }
      const entry = (JSON.parse(readFileSync(join(srcdir, "server.json"), "utf8")).entry) || "server.py";
      const dest = join(hostDir, "mcp", name);
      console.log(`→ MCP '${name}' -> ${dest}  (python: ${py})  config: ${conf}`);
      if (o.dry) { console.log(`   [dry-run] ${o.symlink ? "symlink" : "copy"} mcp/${name} + write ${conf}`); continue; }
      mkdirSync(join(hostDir, "mcp"), { recursive: true });
      rmSync(dest, { recursive: true, force: true });
      if (o.symlink) symlinkSync(srcdir, dest); else cpSync(srcdir, dest, { recursive: true, dereference: true });
      let data = {};
      if (existsSync(conf)) { try { data = JSON.parse(readFileSync(conf, "utf8")); } catch { data = {}; } }
      const env = { BRAIN_SKILLS: skillsDir };
      if (db) env.BRAIN_DB = db;
      if (assets) env.BRAIN_ASSETS = assets;
      if (catalog) env.BRAIN_CATALOG = catalog;
      (data.mcpServers ||= {})[name] = { command: py, args: [join(dest, entry), "--transport", "stdio"], env };
      writeFileSync(conf, JSON.stringify(data, null, 2));
      console.log(`   ✓ wrote ${conf} (mcpServers.${name})`);
    }
  }
}

// --deps: an isolated venv for the skills' Python deps, next to skills/ inside the
// host dir (<root>/.claude/venv, …) — a separate env from the project's own. Scope
// follows the skills: default per-project, or with --user ONE shared ~/.claude/venv
// (use when the ~1.3 GB deps are too big to copy per project). Uses uv; needs a bundle.
function installDeps(o, bases) {
  const req = join(BUNDLES, o.bundle || "", "requirements.txt");
  if (!o.bundle || !existsSync(req)) {
    console.error(`error: --deps needs --bundle <name> with a requirements.txt (looked for ${req})`); process.exit(2);
  }
  if (spawnSync("uv", ["--version"], { stdio: "ignore" }).status !== 0) {
    console.error("error: uv not found — install it: https://astral.sh/uv"); process.exit(1);
  }
  const venvs = o.venv ? [o.venv] : bases.map(b => join(dirname(b), "venv"));
  const scope = o.user ? "shared (--user)" : "per-project";
  for (const venv of venvs) {
    console.log(`→ deps venv (isolated, ${scope}): ${venv}`);
    if (o.dry) { console.log(`   [dry-run] uv venv "${venv}" && uv pip install --python "${venv}" -r "${req}"`); continue; }
    if (spawnSync("uv", ["venv", "--allow-existing", venv], { stdio: "inherit" }).status !== 0) process.exit(1);
    if (spawnSync("uv", ["pip", "install", "--python", venv, "-r", req], { stdio: "inherit" }).status !== 0) process.exit(1);
    console.log(`   ✓ installed requirements.txt into ${venv}`);
    console.log(`   run brain scripts with:  "${join(venv, "bin", "python")}" <script>   (BRAIN_PY)`);
  }
  if (!o.dry) console.log(`(zero-install alt, no venv: uv run --with-requirements "${req}" python <script>)`);
}

// Resolve the ordered skill list for a named bundle from its factory.json.
function bundleSkills(name, withOptional) {
  const manifest = join(BUNDLES, name, "factory.json");
  if (!existsSync(manifest)) {
    const avail = existsSync(BUNDLES) ? readdirSync(BUNDLES).filter(n => statSync(join(BUNDLES, n)).isDirectory()) : [];
    console.error(`error: bundle '${name}' not found (bundles/${name}/factory.json). available: ${avail.join(", ") || "none"}`);
    process.exit(2);
  }
  const f = JSON.parse(readFileSync(manifest, "utf8"));
  const list = [...(f.skills || []), ...(withOptional ? (f.optionalSkills || []) : [])];
  console.log(`bundle: ${f.id} — ${f.title}`);
  return list;
}

function main() {
  const o = parseArgs(process.argv.slice(2));
  if (!existsSync(SRC)) { console.error(`error: skills/ not found at ${SRC}`); process.exit(1); }
  for (const t of o.targets) if (!HOSTS[t]) { console.error(`error: unknown target '${t}' (claude|dsh|copilot|codex)`); process.exit(2); }

  const allSkills = readdirSync(SRC).filter(n => statSync(join(SRC, n)).isDirectory());
  const want = o.bundle ? bundleSkills(o.bundle, o.optional) : o.skills;
  const skills = want ? allSkills.filter(n => want.includes(n)) : allSkills;
  if (want) {                                          // surface any manifest name that has no skill dir
    const missing = want.filter(n => !allSkills.includes(n));
    if (missing.length) { console.error(`error: bundle references unknown skill(s): ${missing.join(", ")}`); process.exit(1); }
  }
  if (!skills.length) { console.error("error: no matching skills"); process.exit(1); }

  let n = 0;
  const bases = [];
  for (const t of o.targets) {
    const base = o.user ? HOSTS[t].user : resolve(process.cwd(), HOSTS[t].project);
    bases.push(base);
    console.log(`→ ${base}`);
    for (const name of skills) {
      const src = join(SRC, name), dest = join(base, name);
      if (o.dry) { console.log(`   [dry-run] ${o.symlink ? "symlink" : "copy"} ${name}`); continue; }
      mkdirSync(base, { recursive: true });
      rmSync(dest, { recursive: true, force: true });
      if (o.symlink) symlinkSync(src, dest);
      else cpSync(src, dest, { recursive: true, dereference: true });
      console.log(`   ✓ ${name}`); n++;
    }
  }
  if (!o.dry) console.log(`done: ${n} skill install(s) (${o.symlink ? "symlink" : "copy"}). Restart the host session to load.`);
  if (o.deps) installDeps(o, bases);
  if (o.mcp) installMcp(o, o.targets, bases);
}
main();
