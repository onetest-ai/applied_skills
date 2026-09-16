import { describe, it, before, after } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const buildZipScript = join(dirname(fileURLToPath(import.meta.url)), "..", "scripts", "build-zip.mjs");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function scaffold(base) {
  // skills/brain-librarian/SKILL.md
  const skillDir = join(base, "skills", "brain-librarian");
  mkdirSync(skillDir, { recursive: true });
  writeFileSync(join(skillDir, "SKILL.md"), [
    "---",
    "name: brain-librarian",
    "description: Generic brain skill",
    "---",
    "",
    "# Brain Librarian",
  ].join("\n"));

  // docs/ — include a nested .env to exercise the copyDir exclusion filter
  const docsDir = join(base, "docs");
  mkdirSync(docsDir, { recursive: true });
  writeFileSync(join(docsDir, "ONBOARDING.md"), "# Onboarding\n");
  writeFileSync(join(docsDir, "SECURITY.md"), "# Security\n");
  writeFileSync(join(docsDir, "ACCEPTANCE_TEST.md"), "# Acceptance test\n");
  writeFileSync(join(docsDir, ".env"), "NESTED_SECRET=leak\n"); // must be excluded from ZIP

  // README.md
  writeFileSync(join(base, "README.md"), "# Brain Cowork Plugin\n");

  // scripts/ — must never appear in ZIP
  const scriptsDir = join(base, "scripts");
  mkdirSync(scriptsDir, { recursive: true });
  writeFileSync(join(scriptsDir, "install.mjs"), "// installer\n");

  // .env (must never appear in ZIP)
  writeFileSync(join(base, ".env"), "MY_KEY=secret\n");

  // .env.local variant (must also never appear in ZIP)
  writeFileSync(join(base, ".env.local"), "MY_KEY=local-secret\n");

  // brain.config.json (must never appear in ZIP)
  writeFileSync(join(base, "brain.config.json"), JSON.stringify({
    brainName: "my-test-brain",
    displayName: "My Test Brain",
    mcpEndpoint: "https://example.com/mcp",
    apiKeyEnvVar: "MY_KEY",
    codemie: { gatewayUrl: "https://codemie.example.com" },
  }));
}

function buildZip({ pluginDir, brainName = "my-test-brain", displayName = "My Test Brain", outDir }) {
  const pluginJsonPath = join(outDir, "plugin.json");
  writeFileSync(pluginJsonPath, JSON.stringify({
    "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
    name: brainName, displayName, version: "1.0.0",
    description: `Brain knowledge assistant for ${displayName}.`,
    author: { name: "Applied AI" },
  }, null, 2) + "\n");

  const zipPath = join(outDir, `${brainName}-1.0.0.zip`);
  execFileSync("node", [
    buildZipScript,
    "--plugin-dir", pluginDir,
    "--brain-name", brainName,
    "--plugin-json", pluginJsonPath,
    "--out", zipPath,
  ]);
  return zipPath;
}

function zipEntries(zipPath) {
  const result = spawnSync("unzip", ["-Z1", zipPath], { encoding: "utf8" });
  return result.stdout.trim().split("\n").filter(Boolean);
}

function zipRead(zipPath, entry) {
  const result = spawnSync("unzip", ["-p", zipPath, entry], { encoding: "utf8" });
  return result.stdout;
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

let pluginDir;
let outDir;
let zipPath;
const srcSkillPath = () => join(pluginDir, "skills", "brain-librarian", "SKILL.md");

before(() => {
  pluginDir = mkdtempSync(join(tmpdir(), "brain-cowork-test-"));
  outDir = mkdtempSync(join(tmpdir(), "brain-cowork-out-"));
  scaffold(pluginDir);
  zipPath = buildZip({ pluginDir, outDir });
});

after(() => {
  rmSync(pluginDir, { recursive: true, force: true });
  rmSync(outDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("brain-cowork ZIP e2e", () => {
  it("ZIP contains SKILL.md stamped with brainName", () => {
    const content = zipRead(zipPath, "skills/brain-librarian/SKILL.md");
    assert.match(content, /^name: my-test-brain$/m);
  });

  it("ZIP SKILL.md does not contain the placeholder name", () => {
    const content = zipRead(zipPath, "skills/brain-librarian/SKILL.md");
    assert.doesNotMatch(content, /^name: brain-librarian$/m);
  });

  it("source SKILL.md is untouched after build", () => {
    const content = readFileSync(srcSkillPath(), "utf8");
    assert.match(content, /^name: brain-librarian$/m);
  });

  it("ZIP contains docs/ONBOARDING.md", () => {
    const entries = zipEntries(zipPath);
    assert.ok(entries.some(e => e === "docs/ONBOARDING.md"), `ONBOARDING.md missing — entries: ${entries.join(", ")}`);
  });

  it("ZIP contains docs/SECURITY.md", () => {
    const entries = zipEntries(zipPath);
    assert.ok(entries.some(e => e === "docs/SECURITY.md"), `SECURITY.md missing`);
  });

  it("ZIP contains docs/ACCEPTANCE_TEST.md", () => {
    const entries = zipEntries(zipPath);
    assert.ok(entries.some(e => e === "docs/ACCEPTANCE_TEST.md"), `ACCEPTANCE_TEST.md missing`);
  });

  it("ZIP contains README.md", () => {
    const entries = zipEntries(zipPath);
    assert.ok(entries.some(e => e === "README.md"), `README.md missing`);
  });

  it("ZIP contains .claude-plugin/plugin.json with correct name", () => {
    const content = zipRead(zipPath, ".claude-plugin/plugin.json");
    const manifest = JSON.parse(content);
    assert.equal(manifest.name, "my-test-brain");
    assert.equal(manifest.displayName, "My Test Brain");
  });

  it("ZIP does not contain any .env file", () => {
    const entries = zipEntries(zipPath);
    const envFiles = entries.filter(e => e === ".env" || e.endsWith("/.env"));
    assert.deepEqual(envFiles, [], `ZIP contains .env files: ${envFiles.join(", ")}`);
  });

  it("ZIP does not contain brain.config.json", () => {
    const entries = zipEntries(zipPath);
    const found = entries.filter(e => e.includes("brain.config.json"));
    assert.deepEqual(found, [], `ZIP contains brain.config.json: ${found.join(", ")}`);
  });

  it("ZIP does not contain scripts/ directory", () => {
    const entries = zipEntries(zipPath);
    const found = entries.filter(e => e.startsWith("scripts/"));
    assert.deepEqual(found, [], `ZIP contains scripts/: ${found.join(", ")}`);
  });

  it("ZIP does not contain credentials.env", () => {
    const entries = zipEntries(zipPath);
    const found = entries.filter(e => e.includes("credentials.env"));
    assert.deepEqual(found, [], `ZIP contains credentials: ${found.join(", ")}`);
  });

  it("ZIP does not contain nested .env inside docs/", () => {
    const entries = zipEntries(zipPath);
    const found = entries.filter(e => e === ".env" || e.endsWith("/.env") || e.includes("/.env."));
    assert.deepEqual(found, [], `ZIP contains .env files: ${found.join(", ")}`);
  });

  it("ZIP does not contain scripts/ directory (scaffold has scripts/install.mjs)", () => {
    const entries = zipEntries(zipPath);
    const found = entries.filter(e => e.startsWith("scripts/"));
    assert.deepEqual(found, [], `ZIP contains scripts/: ${found.join(", ")}`);
  });

  it("build-zip.mjs fails with non-zero exit when SKILL.md lacks name: brain-librarian", () => {
    const badDir = mkdtempSync(join(tmpdir(), "brain-cowork-bad-"));
    const badOutDir = mkdtempSync(join(tmpdir(), "brain-cowork-bad-out-"));
    try {
      const skillDir = join(badDir, "skills", "brain-librarian");
      mkdirSync(skillDir, { recursive: true });
      writeFileSync(join(skillDir, "SKILL.md"), "---\nname: something-else\n---\n");
      const pluginJsonPath = join(badOutDir, "plugin.json");
      writeFileSync(pluginJsonPath, JSON.stringify({ name: "x", displayName: "X", version: "1.0.0" }));
      const result = spawnSync("node", [
        buildZipScript,
        "--plugin-dir", badDir,
        "--brain-name", "test-brain",
        "--plugin-json", pluginJsonPath,
        "--out", join(badOutDir, "test.zip"),
      ], { encoding: "utf8" });
      assert.notEqual(result.status, 0, "Expected non-zero exit for corrupted SKILL.md");
      assert.match(result.stderr, /brain-librarian/);
    } finally {
      rmSync(badDir, { recursive: true, force: true });
      rmSync(badOutDir, { recursive: true, force: true });
    }
  });

  it("build-zip.mjs fails with non-zero exit when required --brain-name arg is missing", () => {
    const result = spawnSync("node", [
      buildZipScript,
      "--plugin-dir", pluginDir,
      "--plugin-json", join(outDir, "plugin.json"),
      "--out", join(outDir, "test.zip"),
    ], { encoding: "utf8" });
    assert.notEqual(result.status, 0, "Expected non-zero exit for missing --brain-name");
    assert.match(result.stderr, /--brain-name/);
  });
});
