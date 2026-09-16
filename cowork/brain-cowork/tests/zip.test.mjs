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

  // skills/brain-plugin-builder/SKILL.md
  const builderDir = join(base, "skills", "brain-plugin-builder");
  mkdirSync(builderDir, { recursive: true });
  writeFileSync(join(builderDir, "SKILL.md"), [
    "---",
    "name: brain-plugin-builder",
    "description: Guided ZIP builder for Brain MCP plugins",
    "---",
    "",
    "# Brain Plugin Builder",
    "",
    "Never ask for or store API keys. Use ${user_config.apiKey} only.",
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
const srcSkillPath = () => join(pluginDir, "skills", "brain-librarian", "SKILL.md"); // source template — always stays brain-librarian

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
  it("ZIP skill folder is named after brainName, not the template placeholder", () => {
    const entries = zipEntries(zipPath);
    assert.ok(
      entries.some(e => e.startsWith("skills/my-test-brain/")),
      `Skill folder not renamed to brainName — entries: ${entries.join(", ")}`,
    );
    assert.ok(
      !entries.some(e => e.startsWith("skills/brain-librarian/")),
      "Template folder brain-librarian still present in ZIP",
    );
  });

  it("ZIP contains SKILL.md stamped with brainName", () => {
    const content = zipRead(zipPath, "skills/my-test-brain/SKILL.md");
    assert.match(content, /^name: my-test-brain$/m);
  });

  it("ZIP SKILL.md does not contain the placeholder name", () => {
    const content = zipRead(zipPath, "skills/my-test-brain/SKILL.md");
    assert.doesNotMatch(content, /^name: brain-librarian$/m);
  });

  it("ZIP SKILL.md does not contain hardcoded allowed-tools", () => {
    const content = zipRead(zipPath, "skills/my-test-brain/SKILL.md");
    assert.doesNotMatch(content, /^allowed-tools:/m,
      "SKILL.md must not hardcode allowed-tools — tools are discovered at runtime");
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

  it("plugin.json contains mcpServers when brain.config.json has mcpEndpoint", () => {
    const content = zipRead(zipPath, ".claude-plugin/plugin.json");
    const manifest = JSON.parse(content);
    assert.ok(manifest.mcpServers, "mcpServers missing from plugin.json");
    const server = manifest.mcpServers["my-test-brain"];
    assert.ok(server, "mcpServers entry for brain name missing");
    assert.equal(server.type, "http");
    assert.equal(server.url, "https://example.com/mcp");
    assert.equal(server.headers["X-API-Key"], "${user_config.apiKey}");
  });

  it("plugin.json contains userConfig.apiKey as sensitive", () => {
    const content = zipRead(zipPath, ".claude-plugin/plugin.json");
    const manifest = JSON.parse(content);
    assert.ok(manifest.userConfig, "userConfig missing from plugin.json");
    assert.ok(manifest.userConfig.apiKey, "userConfig.apiKey missing");
    assert.equal(manifest.userConfig.apiKey.sensitive, true);
    assert.ok(manifest.userConfig.apiKey.description, "userConfig.apiKey.description missing");
  });

  it("plugin.json does not contain a literal API key value", () => {
    const content = zipRead(zipPath, ".claude-plugin/plugin.json");
    // The scaffold .env has MY_KEY=secret — must never appear in ZIP
    assert.doesNotMatch(content, /secret/, "Literal secret found in plugin.json");
  });

  it("plugin.json mcpServers omitted when brain.config.json has no mcpEndpoint", () => {
    const noMcpDir = mkdtempSync(join(tmpdir(), "brain-cowork-nomcp-"));
    const noMcpOut = mkdtempSync(join(tmpdir(), "brain-cowork-nomcp-out-"));
    try {
      scaffold(noMcpDir);
      // Overwrite brain.config.json without mcpEndpoint
      writeFileSync(join(noMcpDir, "brain.config.json"), JSON.stringify({
        brainName: "no-mcp-brain",
        displayName: "No MCP Brain",
        apiKeyEnvVar: "MY_KEY",
        codemie: { gatewayUrl: "https://codemie.example.com" },
      }));
      const pluginJsonPath = join(noMcpOut, "plugin.json");
      writeFileSync(pluginJsonPath, JSON.stringify({
        "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
        name: "no-mcp-brain", displayName: "No MCP Brain", version: "1.0.0",
        description: "test", author: { name: "Applied AI" },
      }, null, 2) + "\n");
      const zipOut = join(noMcpOut, "no-mcp-brain-1.0.0.zip");
      execFileSync("node", [buildZipScript,
        "--plugin-dir", noMcpDir, "--brain-name", "no-mcp-brain",
        "--plugin-json", pluginJsonPath, "--out", zipOut,
      ]);
      const content = spawnSync("unzip", ["-p", zipOut, ".claude-plugin/plugin.json"], { encoding: "utf8" }).stdout;
      const manifest = JSON.parse(content);
      assert.ok(!manifest.mcpServers, "mcpServers should be absent when mcpEndpoint not set");
      assert.ok(!manifest.userConfig, "userConfig should be absent when mcpEndpoint not set");
    } finally {
      rmSync(noMcpDir, { recursive: true, force: true });
      rmSync(noMcpOut, { recursive: true, force: true });
    }
  });

  it("ZIP contains brain-plugin-builder SKILL.md", () => {
    const entries = zipEntries(zipPath);
    assert.ok(
      entries.some(e => e === "skills/brain-plugin-builder/SKILL.md"),
      `brain-plugin-builder/SKILL.md missing — entries: ${entries.join(", ")}`
    );
  });

  it("brain-plugin-builder SKILL.md does not contain a literal API key pattern", () => {
    const content = zipRead(zipPath, "skills/brain-plugin-builder/SKILL.md");
    // Must not contain anything that looks like a bearer token or base64 key (20+ non-space chars)
    assert.doesNotMatch(content, /[A-Za-z0-9+/]{20,}={0,2}(?!\})/,
      "brain-plugin-builder SKILL.md appears to contain a literal credential");
  });

  it("brain-plugin-builder SKILL.md references ${user_config.apiKey} not a literal key", () => {
    const content = zipRead(zipPath, "skills/brain-plugin-builder/SKILL.md");
    assert.match(content, /\$\{user_config\.apiKey\}/,
      "brain-plugin-builder SKILL.md must reference ${user_config.apiKey}");
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

  // --- Regression tests for confirmed findings ---

  it("build-zip.mjs fails with non-zero exit when brain.config.json has invalid JSON", () => {
    const badDir = mkdtempSync(join(tmpdir(), "brain-cowork-badjson-"));
    const badOut = mkdtempSync(join(tmpdir(), "brain-cowork-badjson-out-"));
    try {
      scaffold(badDir);
      writeFileSync(join(badDir, "brain.config.json"), "{ broken json,, }");
      const pluginJsonPath = join(badOut, "plugin.json");
      writeFileSync(pluginJsonPath, JSON.stringify({ name: "x", displayName: "X", version: "1.0.0" }));
      const result = spawnSync("node", [buildZipScript,
        "--plugin-dir", badDir, "--brain-name", "x",
        "--plugin-json", pluginJsonPath, "--out", join(badOut, "x.zip"),
      ], { encoding: "utf8" });
      assert.notEqual(result.status, 0, "Expected non-zero exit for invalid brain.config.json JSON");
      assert.match(result.stderr, /brain\.config\.json/);
    } finally {
      rmSync(badDir, { recursive: true, force: true });
      rmSync(badOut, { recursive: true, force: true });
    }
  });

  it("build-zip.mjs fails with non-zero exit when mcpEndpoint is not https://", () => {
    const badDir = mkdtempSync(join(tmpdir(), "brain-cowork-badscheme-"));
    const badOut = mkdtempSync(join(tmpdir(), "brain-cowork-badscheme-out-"));
    try {
      scaffold(badDir);
      writeFileSync(join(badDir, "brain.config.json"), JSON.stringify({
        brainName: "x", displayName: "X", mcpEndpoint: "file:///etc/passwd",
        apiKeyEnvVar: "MY_KEY", codemie: { gatewayUrl: "https://codemie.example.com" },
      }));
      const pluginJsonPath = join(badOut, "plugin.json");
      writeFileSync(pluginJsonPath, JSON.stringify({ name: "x", displayName: "X", version: "1.0.0" }));
      const result = spawnSync("node", [buildZipScript,
        "--plugin-dir", badDir, "--brain-name", "x",
        "--plugin-json", pluginJsonPath, "--out", join(badOut, "x.zip"),
      ], { encoding: "utf8" });
      assert.notEqual(result.status, 0, "Expected non-zero exit for non-https mcpEndpoint");
      assert.match(result.stderr, /https/i);
    } finally {
      rmSync(badDir, { recursive: true, force: true });
      rmSync(badOut, { recursive: true, force: true });
    }
  });

  it("build-zip.mjs fails with non-zero exit for invalid --brain-name (path traversal)", () => {
    const pluginJsonPath = join(outDir, "plugin.json");
    const result = spawnSync("node", [buildZipScript,
      "--plugin-dir", pluginDir, "--brain-name", "../../evil",
      "--plugin-json", pluginJsonPath, "--out", join(outDir, "evil.zip"),
    ], { encoding: "utf8" });
    assert.notEqual(result.status, 0, "Expected non-zero exit for path-traversal brain-name");
  });

  it("build-zip.mjs does not merge into existing ZIP — output is always a fresh archive", () => {
    const freshDir = mkdtempSync(join(tmpdir(), "brain-cowork-fresh-"));
    const freshOut = mkdtempSync(join(tmpdir(), "brain-cowork-fresh-out-"));
    try {
      scaffold(freshOut); // put a different scaffold there to pollute if merge happens
      const pluginJsonPath = join(freshOut, "plugin.json");
      writeFileSync(pluginJsonPath, JSON.stringify({ name: "my-test-brain", displayName: "My Test Brain", version: "1.0.0", description: "test", author: { name: "Applied AI" } }, null, 2));
      const zipOut = join(freshOut, "my-test-brain-1.0.0.zip");

      // First build into the target path
      scaffold(freshDir);
      execFileSync("node", [buildZipScript,
        "--plugin-dir", freshDir, "--brain-name", "my-test-brain",
        "--plugin-json", pluginJsonPath, "--out", zipOut,
      ]);
      const entries1 = zipEntries(zipOut);

      // Second build with different source — should produce clean archive, not merged
      const freshDir2 = mkdtempSync(join(tmpdir(), "brain-cowork-fresh2-"));
      scaffold(freshDir2);
      const pluginJsonPath2 = join(freshOut, "plugin2.json");
      writeFileSync(pluginJsonPath2, JSON.stringify({ name: "second-brain", displayName: "Second Brain", version: "1.0.0", description: "test", author: { name: "Applied AI" } }, null, 2));
      execFileSync("node", [buildZipScript,
        "--plugin-dir", freshDir2, "--brain-name", "second-brain",
        "--plugin-json", pluginJsonPath2, "--out", zipOut,
      ]);
      const entries2 = zipEntries(zipOut);

      // If merge happened, entries from first build would appear alongside second build entries.
      // After the folder-rename fix, first build produces skills/my-test-brain/SKILL.md;
      // second build should produce skills/second-brain/SKILL.md and NOT contain my-test-brain.
      assert.ok(
        entries2.some(e => e.startsWith("skills/second-brain/")),
        `Second build missing skills/second-brain/ — entries: ${entries2.join(", ")}`,
      );
      assert.ok(
        !entries2.some(e => e.startsWith("skills/my-test-brain/")),
        "ZIP contains stale skills/my-test-brain/ folder from previous build (merge not prevented)",
      );
      // Read the stamped SKILL.md from the second build and confirm it has the right brainName
      const content = zipRead(zipOut, "skills/second-brain/SKILL.md");
      assert.doesNotMatch(content, /^name: my-test-brain$/m, "Stale brainName stamp from first build found in second build");
      rmSync(freshDir2, { recursive: true, force: true });
    } finally {
      rmSync(freshDir, { recursive: true, force: true });
      rmSync(freshOut, { recursive: true, force: true });
    }
  });
});
