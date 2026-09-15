# `kb` Knowledge-Worker Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `kb`, a lightweight Claude Code plugin that lets a person cowork with the Brain — interrogate it and co-author cited Markdown deliverables — while enforcing the Brain's truth contract at the composition layer.

**Architecture:** `kb` is a second plugin in the existing `onetest-ai` marketplace. It ships **no MCP server of its own**; it consumes the Brain's governed FastMCP tools (model-driven) for interrogation/authoring, and a shell hook reads the local `knowledge.sqlite` directly for a cheap once-per-session health line. Truthfulness is enforced by a shared doctrine every skill references, a read-only `verifier` subagent, and a human gate on every file write.

**Tech Stack:** Claude Code plugin format (JSON manifests, Markdown SKILL/agent files, `hooks.json`), POSIX shell + `sqlite3` for hooks, Python 3 **stdlib `unittest`** for tests (matches `mcp/brain/test_semantic_mcp.py`). No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-09-15-kb-cowork-plugin-design.md`

## Global Constraints

- **Plugin name:** `kb`. Skills surface as `/kb:<name>`.
- **Boundary (invariant):** `kb` calls only the seven governed Brain FastMCP tools (`list_metrics`, `get_metric`, `search_knowledge`, `get_taxonomy`, `find_related_content`, `get_evidence`, `health`). No writes to `knowledge.sqlite`, no raw SQL surface, no build/venv scripts.
- **No fabricated numbers:** no number appears in any `kb` output that the Brain did not compute via `get_metric`; every number renders with its `source_file`. On `status:"not_modeled"` or empty results, state the gap plainly — never fill from model priors.
- **Brain-namespace resolution:** the Brain may answer as `mcp__brain__*` (standalone) or `mcp__plugin_<plugin>_brain__*` (via plugin). Skills detect the answering namespace via `health` and document both.
- **`limit` argument** on any Brain tool is an integer 1..100.
- **Human is the final gate:** file-writes are never auto-approved; the verifier is an automated pre-check whose verdict is surfaced to the human.
- **Hook performance invariant:** no hook calls a Brain MCP tool on a per-tool-use or per-prompt cadence. The only per-session check is a local `sqlite3` read of `knowledge.sqlite` (zero MCP round-trips). *(This refines spec §5.5, which described a `health`-tool call; a shell hook cannot invoke an MCP tool, so a direct local read is used — strictly cheaper and dependency-free. Behaviour — a one-line lane-count summary or a "no brain detected" message — is unchanged.)*
- **Citation tags (draft form):** `[RAG:<chunk_id>]`, `[MART:<metric>@<grain>]`, `[GRAPH:<node>]`; visual/table facts cite `get_evidence`.
- **Test runner:** `python3 -m unittest discover -s kb/tests -v` (stdlib only).

---

### Task 1: Scaffold the `kb` plugin and register it in the marketplace

**Files:**
- Create: `kb/.claude-plugin/plugin.json`
- Create: `kb/README.md`
- Modify: `.claude-plugin/marketplace.json` (add a second `plugins[]` entry)
- Create: `kb/tests/test_plugin_structure.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: the plugin root `kb/` with a valid manifest; a reusable test helper `load_json(path)` and the `KB_ROOT` path constant in `test_plugin_structure.py` that later tasks extend.

- [ ] **Step 1: Write the failing structure test**

Create `kb/tests/test_plugin_structure.py`:

```python
from __future__ import annotations

import json
import unittest
from pathlib import Path

KB_ROOT = Path(__file__).resolve().parents[1]          # .../kb
REPO_ROOT = KB_ROOT.parent                             # repo root


def load_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


class TestPluginManifest(unittest.TestCase):
    def test_plugin_json_exists_and_names_kb(self):
        manifest = load_json(KB_ROOT / ".claude-plugin" / "plugin.json")
        self.assertEqual(manifest["name"], "kb")
        self.assertIn("description", manifest)
        self.assertIn("version", manifest)
        # kb ships no MCP server of its own (it consumes the Brain's)
        self.assertNotIn("mcpServers", manifest)

    def test_marketplace_lists_kb(self):
        market = load_json(REPO_ROOT / ".claude-plugin" / "marketplace.json")
        names = {p["name"] for p in market["plugins"]}
        self.assertIn("kb", names)
        kb_entry = next(p for p in market["plugins"] if p["name"] == "kb")
        self.assertEqual(kb_entry["source"], "./kb")

    def test_readme_exists(self):
        self.assertTrue((KB_ROOT / "README.md").is_file())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python3 -m unittest kb.tests.test_plugin_structure -v` (from repo root)
Expected: FAIL — `kb/.claude-plugin/plugin.json` does not exist (FileNotFoundError).

- [ ] **Step 3: Create the plugin manifest**

Create `kb/.claude-plugin/plugin.json`:

```json
{
  "name": "kb",
  "description": "Cowork with the Brain: interrogate the knowledge base and co-author cited Markdown deliverables, with every claim traceable or honestly 'not modeled'.",
  "version": "0.1.0",
  "author": { "name": "onetest-ai" },
  "homepage": "https://github.com/onetest-ai/applied_skills",
  "keywords": ["knowledge-base", "rag", "citations", "analyst", "brain", "mcp"]
}
```

- [ ] **Step 4: Add the marketplace entry**

In `.claude-plugin/marketplace.json`, append to the `plugins` array (after the existing `applied-skills` entry):

```json
    {
      "name": "kb",
      "source": "./kb",
      "description": "Knowledge-worker companion over the Brain: cited answers and Markdown deliverables.",
      "version": "0.1.0"
    }
```

- [ ] **Step 5: Create the README skeleton**

Create `kb/README.md` with: what `kb` is (one paragraph), the prerequisite (a reachable Brain MCP — link to `bundles/brain/README.md`), the skill list (`/kb:ask`, `/kb:explore`, `/kb:challenge`, `/kb:brief`, `/kb:report`, `/kb:mode`, `/kb:connect`), and a "truth contract" note (cited or not-modeled; numbers from marts). Include an install line:

```bash
claude plugin marketplace add onetest-ai/applied_skills
claude plugin install kb@onetest-ai
```

- [ ] **Step 6: Run the test and verify it passes**

Run: `python3 -m unittest kb.tests.test_plugin_structure -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add kb/.claude-plugin/plugin.json kb/README.md kb/tests/test_plugin_structure.py .claude-plugin/marketplace.json
git commit -m "feat(kb): scaffold kb plugin and register in marketplace"
```

---

### Task 2: Shared doctrine and the `verifier` subagent

**Files:**
- Create: `kb/skills/_shared/doctrine.md`
- Create: `kb/agents/verifier.md`
- Modify: `kb/tests/test_plugin_structure.py` (add doctrine + agent assertions)

**Interfaces:**
- Consumes: `load_json`, `KB_ROOT` from Task 1.
- Produces: `kb/skills/_shared/doctrine.md` (referenced by every skill), `kb/agents/verifier.md` (a subagent named `verifier`), and a reusable test helper `read_text(path)` and `parse_frontmatter(text) -> dict` added to `test_plugin_structure.py`.

- [ ] **Step 1: Write the failing tests**

Add to `kb/tests/test_plugin_structure.py`:

```python
import re

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def parse_frontmatter(text: str) -> dict:
    """Minimal YAML-frontmatter parser: top-level `key: value` pairs only."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


class TestDoctrineAndVerifier(unittest.TestCase):
    def test_doctrine_covers_truth_rules(self):
        text = read_text(KB_ROOT / "skills" / "_shared" / "doctrine.md")
        for token in ["[RAG:", "[MART:", "[GRAPH:", "not modeled", "source_file"]:
            self.assertIn(token, text, f"doctrine missing {token!r}")

    def test_verifier_agent_frontmatter(self):
        text = read_text(KB_ROOT / "agents" / "verifier.md")
        fm = parse_frontmatter(text)
        self.assertEqual(fm.get("name"), "verifier")
        self.assertIn("description", fm)
        # read-only: no Write/Edit in the tools allowlist
        self.assertIn("tools", fm)
        self.assertNotIn("Write", fm["tools"])
        self.assertNotIn("Edit", fm["tools"])
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python3 -m unittest kb.tests.test_plugin_structure.TestDoctrineAndVerifier -v`
Expected: FAIL — doctrine.md and verifier.md do not exist.

- [ ] **Step 3: Write the doctrine**

Create `kb/skills/_shared/doctrine.md` — distilled from `bundles/brain/BUILDING-AGENTS.md`. It MUST contain these sections and the exact tokens the test checks:

- **Two lanes, one truth** — meaning from `search_knowledge`/`get_taxonomy` (never a figure); every number from `get_metric` with its `source_file`.
- **Citation tags** — draft form `[RAG:<chunk_id>]`, `[MART:<metric>@<grain>]`, `[GRAPH:<node>]`; visual/table facts cite `get_evidence`.
- **Commit, cite, then qualify** — give the retrievable value and cite it; disambiguate scope/grain/population after; never refuse a figure that exists.
- **Gaps beat fabrication** — on `not_modeled`/empty, say "not modeled" plainly.
- **Source precedence** — authoritative scorecard over lossy transcription; flag discrepancies.
- **Namespace detection** — call `health` first; use whichever namespace (`mcp__brain__*` or `mcp__plugin_<plugin>_brain__*`) answers.

- [ ] **Step 4: Write the verifier agent**

Create `kb/agents/verifier.md`:

```markdown
---
name: verifier
description: Read-only truth-check of a draft answer or artifact against the Brain — re-resolves every citation and re-computes every number, returning a per-claim verdict. Use before emitting any authored deliverable.
tools: Read, Grep
model: sonnet
permissionMode: auto
---

You independently verify a draft against the Brain. You never edit files.

Follow `kb/skills/_shared/doctrine.md`. Steps:
1. Extract every citation tag (`[RAG:*]`, `[MART:*]`, `[GRAPH:*]`) and every numeric claim.
2. Call `health` to find the answering Brain namespace.
3. For each `[RAG:id]`: call `get_evidence(chunk_id=id)` — does the section exist and support the sentence?
4. For each `[MART:metric@grain]` / numeric claim: call `get_metric(...)` — does that value exist at that grain, with a `source_file`?
5. For each `[GRAPH:node]`: call `get_taxonomy(label=node)` — does the node exist?

Return a structured verdict, one line per claim:
`verified | unsupported | grain-mismatch | uncited-number — <claim> — <evidence or gap>`

Then a summary: counts per verdict, and whether the draft is safe to emit.
```

Note: `verifier` must be granted the Brain MCP tools at invocation. Because a subagent's `tools` allowlist restricts to listed tools, invoking skills pass the Brain tools through; document in the doctrine that the verifier is dispatched with `mcp__brain__*` / `mcp__plugin_*_brain__*` available. (Keep `Write`/`Edit` out — read-only.)

- [ ] **Step 5: Run the tests and verify they pass**

Run: `python3 -m unittest kb.tests.test_plugin_structure -v`
Expected: PASS (all tests, including Task 1's).

- [ ] **Step 6: Commit**

```bash
git add kb/skills/_shared/doctrine.md kb/agents/verifier.md kb/tests/test_plugin_structure.py
git commit -m "feat(kb): add shared truth doctrine and read-only verifier subagent"
```

---

### Task 3: `/kb:ask` — the core interrogation skill

**Files:**
- Create: `kb/skills/ask/SKILL.md`
- Create: `kb/tests/test_skills.py`

**Interfaces:**
- Consumes: doctrine (Task 2); `parse_frontmatter`, `read_text`, `KB_ROOT` (import from `test_plugin_structure`).
- Produces: `kb/tests/test_skills.py` with a reusable `SKILL_DIRS` list and a `assert_skill_contract(self, name, required_tokens)` helper that later tasks extend.

- [ ] **Step 1: Write the failing test**

Create `kb/tests/test_skills.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path

from test_plugin_structure import KB_ROOT, read_text, parse_frontmatter


class SkillContractMixin:
    def assert_skill(self, name, required_tokens):
        path = KB_ROOT / "skills" / name / "SKILL.md"
        self.assertTrue(path.is_file(), f"missing skill {name}")
        text = read_text(path)
        fm = parse_frontmatter(text)
        self.assertIn("description", fm, f"{name}: no description")
        for token in required_tokens:
            self.assertIn(token, text, f"{name}: missing {token!r}")


class TestAskSkill(unittest.TestCase, SkillContractMixin):
    def test_ask_contract(self):
        self.assert_skill(
            "ask",
            required_tokens=[
                "_shared/doctrine.md",   # references the doctrine
                "health",                # detects the brain first
                "search_knowledge",
                "get_metric",
                "get_taxonomy",
                "get_evidence",
                "not modeled",
                "[RAG:",
            ],
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python3 -m unittest kb.tests.test_skills -v` (run with `PYTHONPATH=kb/tests` or from `kb/tests`; see Step 6)
Expected: FAIL — `kb/skills/ask/SKILL.md` missing.

- [ ] **Step 3: Write the skill**

Create `kb/skills/ask/SKILL.md`:

```markdown
---
description: Answer a question from the Brain with a fully cited response — decompose into sub-claims, route each to the right lane, and mark anything the Brain cannot support as "not modeled". Use whenever the user asks a factual/analytical question that the knowledge base should ground.
allowed-tools: mcp__brain__search_knowledge mcp__brain__get_metric mcp__brain__get_taxonomy mcp__brain__find_related_content mcp__brain__get_evidence mcp__brain__list_metrics mcp__brain__health mcp__plugin_applied-skills_brain__search_knowledge mcp__plugin_applied-skills_brain__get_metric mcp__plugin_applied-skills_brain__get_taxonomy mcp__plugin_applied-skills_brain__find_related_content mcp__plugin_applied-skills_brain__get_evidence mcp__plugin_applied-skills_brain__list_metrics mcp__plugin_applied-skills_brain__health
arguments: [question]
---

Answer **$question** grounded in the Brain. Follow `../_shared/doctrine.md`.

1. **Detect the Brain.** Call `health`. If no Brain answers, tell the user and suggest `/kb:connect`; stop.
2. **Decompose** the question into sub-claims. Classify each: narrative, number, relation, or visual/table.
3. **Route each sub-claim:**
   - narrative → `search_knowledge`
   - number → `get_metric` (never assert a figure from narrative)
   - relation/classification → `get_taxonomy`
   - a specific section / page figure / table → `get_evidence`
4. **Compose one answer.** Every claim carries its tag — `[RAG:<chunk_id>]`, `[MART:<metric>@<grain>]`, or `[GRAPH:<node>]`. Numbers show `source_file`. Anything unsupported is stated as "Not modeled: …".
5. **Commit, cite, then qualify** — give the value first, disambiguate after; never refuse a retrievable figure.
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd kb/tests && python3 -m unittest test_skills -v`
Expected: PASS.

- [ ] **Step 5: Confirm no regressions**

Run: `cd kb/tests && python3 -m unittest discover -s . -v`
Expected: PASS (all structure + skill tests).

- [ ] **Step 6: Commit**

```bash
git add kb/skills/ask/SKILL.md kb/tests/test_skills.py
git commit -m "feat(kb): add /kb:ask cited-answer skill"
```

---

### Task 4: `/kb:explore` and `/kb:challenge`

**Files:**
- Create: `kb/skills/explore/SKILL.md`
- Create: `kb/skills/challenge/SKILL.md`
- Modify: `kb/tests/test_skills.py` (add two test classes)

**Interfaces:**
- Consumes: `SkillContractMixin` (Task 3).
- Produces: two more skills; no new helpers.

- [ ] **Step 1: Write the failing tests**

Add to `kb/tests/test_skills.py`:

```python
class TestExploreSkill(unittest.TestCase, SkillContractMixin):
    def test_explore_contract(self):
        self.assert_skill(
            "explore",
            required_tokens=[
                "../_shared/doctrine.md", "health",
                "find_related_content", "get_taxonomy", "chunk_id",
            ],
        )


class TestChallengeSkill(unittest.TestCase, SkillContractMixin):
    def test_challenge_contract(self):
        self.assert_skill(
            "challenge",
            required_tokens=[
                "../_shared/doctrine.md", "health",
                "get_metric", "get_evidence", "not modeled", "grain",
            ],
        )
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `cd kb/tests && python3 -m unittest test_skills.TestExploreSkill test_skills.TestChallengeSkill -v`
Expected: FAIL — both SKILL.md missing.

- [ ] **Step 3: Write `/kb:explore`**

Create `kb/skills/explore/SKILL.md` with frontmatter mirroring Task 3's `allowed-tools`, `arguments: [topic]`. Body follows `../_shared/doctrine.md`: (1) `health`; (2) anchor on a `get_taxonomy(label=...)` node or the top `search_knowledge` hit; (3) walk `find_related_content` and `get_taxonomy` subclasses across documents, iterating; (4) end with a navigable, cited summary that lists `chunk_id`s / vault paths. Note it may run the gather phase in a forked context to keep tool output off the main thread.

- [ ] **Step 4: Write `/kb:challenge`**

Create `kb/skills/challenge/SKILL.md` with the same `allowed-tools`, `arguments: [claim]`. Body: adversarially test **$claim** — (1) `health`; (2) `search_knowledge` for contradicting sections; (3) for any number, `get_metric` to confirm it exists at the stated **grain** (flag `grain` mismatches); (4) surface silent gaps as "Not modeled". Output: a verdict per sub-claim with citations.

- [ ] **Step 5: Run the tests and verify they pass**

Run: `cd kb/tests && python3 -m unittest discover -s . -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kb/skills/explore/SKILL.md kb/skills/challenge/SKILL.md kb/tests/test_skills.py
git commit -m "feat(kb): add /kb:explore and /kb:challenge interrogation skills"
```

---

### Task 5: `/kb:brief` and `/kb:report` — the authoring pipeline

**Files:**
- Create: `kb/skills/brief/SKILL.md`
- Create: `kb/skills/report/SKILL.md`
- Create: `kb/skills/_shared/authoring.md` (the shared gather→draft→verify→gate→emit pipeline + sources-sidecar format)
- Modify: `kb/tests/test_skills.py` (add tests)

**Interfaces:**
- Consumes: `SkillContractMixin`; doctrine (Task 2); verifier (Task 2).
- Produces: `kb/skills/_shared/authoring.md` referenced by both authoring skills; the `<slug>.sources.json` sidecar convention.

- [ ] **Step 1: Write the failing tests**

Add to `kb/tests/test_skills.py`:

```python
class TestAuthoringShared(unittest.TestCase):
    def test_authoring_pipeline_documented(self):
        from test_plugin_structure import KB_ROOT, read_text
        text = read_text(KB_ROOT / "skills" / "_shared" / "authoring.md")
        for token in ["gather", "verify", "verifier", "human", "sources.json", "source_file"]:
            self.assertIn(token, text, f"authoring.md missing {token!r}")


class TestBriefSkill(unittest.TestCase, SkillContractMixin):
    def test_brief_contract(self):
        self.assert_skill(
            "brief",
            required_tokens=["_shared/authoring.md", "verifier", "docs/kb/", "Sources"],
        )


class TestReportSkill(unittest.TestCase, SkillContractMixin):
    def test_report_contract(self):
        self.assert_skill(
            "report",
            required_tokens=["_shared/authoring.md", "verifier", "docs/kb/",
                             "Table of Contents", "Sources"],
        )
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `cd kb/tests && python3 -m unittest test_skills.TestAuthoringShared test_skills.TestBriefSkill test_skills.TestReportSkill -v`
Expected: FAIL — files missing.

- [ ] **Step 3: Write the shared authoring pipeline**

Create `kb/skills/_shared/authoring.md`. It MUST document (and contain the tokens the test checks):

- **Pipeline:** `gather` (Brain calls per `../_shared/doctrine.md`) → `draft` (compose with inline `[RAG:*]`/`[MART:*]`/`[GRAPH:*]` tags) → `verify` (dispatch the `verifier` subagent; block or demote flagged claims) → **human gate** (show verdict; do not write without approval) → `emit`.
- **Emit format:** Markdown with a numbered **Sources** footnote section; every number shows `source_file`; unmodeled areas render as an explicit "Not modeled: …" callout.
- **Sidecar:** alongside `<slug>.md`, write `<slug>.sources.json` — an array of `{tag, kind, chunk_id?, metric?, grain?, source_file?}` so every claim is re-traceable.
- **Output path:** default `docs/kb/<slug>.md`, per-project configurable; pointing at the Brain's Obsidian vault is a documented option.

- [ ] **Step 4: Write `/kb:brief`**

Create `kb/skills/brief/SKILL.md`, `arguments: [subject]`, `allowed-tools` = the Brain read tools (as Task 3) **but NOT** `Write` (the human gate approves the write). Body: a short cited memo on **$subject** following `../_shared/authoring.md`; output to `docs/kb/<slug>.md` with a **Sources** section; run the `verifier` before proposing the write.

- [ ] **Step 5: Write `/kb:report`**

Create `kb/skills/report/SKILL.md`, `arguments: [subject]`, same posture. Body: a long-form structured document with a **Table of Contents** and sections; otherwise identical pipeline (`../_shared/authoring.md`, `verifier`, `docs/kb/`, **Sources**, sidecar).

- [ ] **Step 6: Run the tests and verify they pass**

Run: `cd kb/tests && python3 -m unittest discover -s . -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add kb/skills/_shared/authoring.md kb/skills/brief/SKILL.md kb/skills/report/SKILL.md kb/tests/test_skills.py
git commit -m "feat(kb): add authoring pipeline with /kb:brief and /kb:report"
```

---

### Task 6a: Ambient health-line hook script

**Files:**
- Create: `kb/hooks/scripts/health-line.sh`
- Create: `kb/tests/test_hooks.py` (with a sqlite fixture builder)

**Interfaces:**
- Consumes: `sqlite3` CLI.
- Produces: `health-line.sh` (reads `$BRAIN_DB` or searches known locations; prints one status line; always exits 0) and `kb/tests/test_hooks.py` with `build_fixture_db(path)` reused by 6b/6c.

- [ ] **Step 1: Write the failing tests**

Create `kb/tests/test_hooks.py`:

```python
from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

KB_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = KB_ROOT / "hooks" / "scripts"


def build_fixture_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE chunks(id INTEGER PRIMARY KEY, source TEXT, ord INT, title TEXT, text TEXT);
        CREATE TABLE graph_nodes(id TEXT PRIMARY KEY, label TEXT, kind TEXT, parent TEXT);
        CREATE TABLE facts(family TEXT, metric TEXT, grain TEXT, entity TEXT, month TEXT, value REAL, source_file TEXT);
        INSERT INTO chunks(id, source, ord, title, text) VALUES (1,'a.md',0,'t','body');
        INSERT INTO graph_nodes(id,label,kind,parent) VALUES ('n1','L1','intent',NULL);
        INSERT INTO facts VALUES ('f','aht','branch','A','2025-06',12.0,'x.xlsx');
        """
    )
    con.commit()
    con.close()


def run_script(script: Path, env_extra=None, stdin=""):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["sh", str(script)], input=stdin, env=env,
        capture_output=True, text=True,
    )


class TestHealthLine(unittest.TestCase):
    def test_reports_lane_counts_when_db_present(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "knowledge.sqlite"
            build_fixture_db(db)
            res = run_script(SCRIPTS / "health-line.sh", {"BRAIN_DB": str(db)})
            self.assertEqual(res.returncode, 0)
            out = res.stdout.lower()
            self.assertIn("brain", out)
            self.assertIn("chunks", out)
            self.assertIn("1", res.stdout)   # one chunk

    def test_quiet_message_when_no_db(self):
        with tempfile.TemporaryDirectory() as d:
            res = run_script(
                SCRIPTS / "health-line.sh",
                {"BRAIN_DB": str(Path(d) / "missing.sqlite")},
            )
            self.assertEqual(res.returncode, 0)   # never breaks the session
            self.assertIn("kb:connect", res.stdout)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `cd kb/tests && python3 -m unittest test_hooks.TestHealthLine -v`
Expected: FAIL — `health-line.sh` missing.

- [ ] **Step 3: Write the script**

Create `kb/hooks/scripts/health-line.sh`:

```sh
#!/bin/sh
# SessionStart hook: print one cheap status line about the local Brain store.
# Reads knowledge.sqlite directly (no MCP round-trip). Always exits 0.
set -u

find_db() {
  if [ -n "${BRAIN_DB:-}" ] && [ -f "${BRAIN_DB}" ]; then printf '%s\n' "$BRAIN_DB"; return 0; fi
  for d in \
    "${CLAUDE_PROJECT_DIR:-.}/schema" \
    "${CLAUDE_PROJECT_DIR:-.}/.claude" \
    "$(pwd)/schema" "$(pwd)/.claude" "$(pwd)"; do
    if [ -f "$d/knowledge.sqlite" ]; then printf '%s\n' "$d/knowledge.sqlite"; return 0; fi
  done
  return 1
}

DB="$(find_db)" || { echo "kb: no Brain detected — run /kb:connect to register one."; exit 0; }

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "kb: Brain store found ($DB) but sqlite3 is unavailable for a health line."
  exit 0
fi

CHUNKS=$(sqlite3 "$DB" "SELECT count(*) FROM chunks;" 2>/dev/null || echo "?")
FACTS=$(sqlite3 "$DB" "SELECT count(*) FROM facts;" 2>/dev/null || echo "?")
NODES=$(sqlite3 "$DB" "SELECT count(*) FROM graph_nodes;" 2>/dev/null || echo "?")
echo "kb: Brain ready — chunks=$CHUNKS facts=$FACTS graph_nodes=$NODES ($DB)"
exit 0
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `cd kb/tests && python3 -m unittest test_hooks.TestHealthLine -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kb/hooks/scripts/health-line.sh kb/tests/test_hooks.py
git commit -m "feat(kb): add session-start Brain health-line hook script"
```

---

### Task 6b: Ambient reminder hook script

**Files:**
- Create: `kb/hooks/scripts/ambient-reminder.sh`
- Modify: `kb/tests/test_hooks.py` (add `TestAmbientReminder`)

**Interfaces:**
- Consumes: nothing new (state is a plain JSON file read; no `jq` dependency — grep-based).
- Produces: `ambient-reminder.sh` reading `.claude/kb/state.json`; emits the reminder only when `"ambient": true`.

- [ ] **Step 1: Write the failing tests**

Add to `kb/tests/test_hooks.py`:

```python
class TestAmbientReminder(unittest.TestCase):
    def _run_with_state(self, ambient: bool):
        with tempfile.TemporaryDirectory() as d:
            state_dir = Path(d) / ".claude" / "kb"
            state_dir.mkdir(parents=True)
            (state_dir / "state.json").write_text(
                '{"ambient": %s}' % ("true" if ambient else "false"),
                encoding="utf-8",
            )
            return run_script(
                SCRIPTS / "ambient-reminder.sh",
                {"CLAUDE_PROJECT_DIR": d},
            )

    def test_emits_reminder_when_on(self):
        res = self._run_with_state(True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Brain", res.stdout)
        self.assertIn("not modeled", res.stdout.lower())

    def test_silent_when_off(self):
        res = self._run_with_state(False)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "")

    def test_silent_when_no_state_file(self):
        with tempfile.TemporaryDirectory() as d:
            res = run_script(SCRIPTS / "ambient-reminder.sh", {"CLAUDE_PROJECT_DIR": d})
            self.assertEqual(res.returncode, 0)
            self.assertEqual(res.stdout.strip(), "")
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `cd kb/tests && python3 -m unittest test_hooks.TestAmbientReminder -v`
Expected: FAIL — `ambient-reminder.sh` missing.

- [ ] **Step 3: Write the script**

Create `kb/hooks/scripts/ambient-reminder.sh`:

```sh
#!/bin/sh
# UserPromptSubmit hook: when ambient mode is on, append a short grounding
# reminder. Plain local file read only — no MCP call, no LLM. Always exits 0.
set -u

STATE="${CLAUDE_PROJECT_DIR:-.}/.claude/kb/state.json"
[ -f "$STATE" ] || exit 0

# grep-based check to avoid a jq dependency
if grep -Eq '"ambient"[[:space:]]*:[[:space:]]*true' "$STATE"; then
  cat <<'EOF'
kb ambient mode: ground factual and numeric claims in the Brain (search_knowledge / get_metric / get_taxonomy) and cite them; prefer "not modeled" over a guess. Numbers come only from get_metric with their source_file.
EOF
fi
exit 0
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `cd kb/tests && python3 -m unittest test_hooks -v`
Expected: PASS (health-line + ambient-reminder).

- [ ] **Step 5: Commit**

```bash
git add kb/hooks/scripts/ambient-reminder.sh kb/tests/test_hooks.py
git commit -m "feat(kb): add ambient-mode reminder hook script"
```

---

### Task 6c: Wire hooks; add `/kb:mode` and `/kb:connect`

**Files:**
- Create: `kb/hooks/hooks.json`
- Create: `kb/skills/mode/SKILL.md`
- Create: `kb/skills/connect/SKILL.md`
- Modify: `kb/tests/test_hooks.py` (add `TestHooksManifest`); `kb/tests/test_skills.py` (add mode/connect contracts)

**Interfaces:**
- Consumes: the two scripts (6a, 6b); `SkillContractMixin` (Task 3).
- Produces: `hooks/hooks.json` binding `SessionStart`→`health-line.sh` and `UserPromptSubmit`→`ambient-reminder.sh`; `/kb:mode` (writes `.claude/kb/state.json`) and `/kb:connect`.

- [ ] **Step 1: Write the failing tests**

Add to `kb/tests/test_hooks.py`:

```python
import json

class TestHooksManifest(unittest.TestCase):
    def test_hooks_json_wires_both_events(self):
        data = json.loads((KB_ROOT / "hooks" / "hooks.json").read_text())
        hooks = data["hooks"]
        self.assertIn("SessionStart", hooks)
        self.assertIn("UserPromptSubmit", hooks)
        blob = json.dumps(data)
        self.assertIn("health-line.sh", blob)
        self.assertIn("ambient-reminder.sh", blob)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", blob)  # portable pathing
```

Add to `kb/tests/test_skills.py`:

```python
class TestModeSkill(unittest.TestCase, SkillContractMixin):
    def test_mode_contract(self):
        self.assert_skill("mode", required_tokens=["state.json", "ambient", "on", "off", "status"])


class TestConnectSkill(unittest.TestCase, SkillContractMixin):
    def test_connect_contract(self):
        self.assert_skill("connect", required_tokens=["health", "mcp-config", "brain"])
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `cd kb/tests && python3 -m unittest test_hooks.TestHooksManifest test_skills.TestModeSkill test_skills.TestConnectSkill -v`
Expected: FAIL — files missing.

- [ ] **Step 3: Write `hooks/hooks.json`**

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command", "command": "sh ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/health-line.sh", "timeout": 10 }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "sh ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/ambient-reminder.sh", "timeout": 5 }
        ]
      }
    ]
  }
}
```

- [ ] **Step 4: Write `/kb:mode`**

Create `kb/skills/mode/SKILL.md`, `arguments: [action]`, `allowed-tools: Read Write(*/.claude/kb/state.json) Bash(mkdir *)`. Body: for **$action** —
- `on` → write `.claude/kb/state.json` = `{"ambient": true, "since": "<iso8601>"}` (create `.claude/kb/` if needed).
- `off` → write `{"ambient": false}`.
- `status` → read and report current `ambient` value, and mention the SessionStart health line reflects the Brain.
Explain that ambient mode changes take effect on the next prompt (UserPromptSubmit) / session (SessionStart), and that it is project-scoped.

- [ ] **Step 5: Write `/kb:connect`**

Create `kb/skills/connect/SKILL.md`, `allowed-tools` including both Brain `health` namespaces + `Bash`. Body: (1) call `health` in each namespace to detect a reachable Brain; (2) if found, report which namespace and the lane counts; (3) if not, walk the user through registering it — run `./brain mcp-config` from the brain project to print the `mcpServers` JSON, add it to `.mcp.json`, and reload plugins — linking `bundles/brain/README.md`.

- [ ] **Step 6: Run all tests and verify they pass**

Run: `cd kb/tests && python3 -m unittest discover -s . -v`
Expected: PASS (structure + skills + hooks).

- [ ] **Step 7: Commit**

```bash
git add kb/hooks/hooks.json kb/skills/mode/SKILL.md kb/skills/connect/SKILL.md kb/tests/test_hooks.py kb/tests/test_skills.py
git commit -m "feat(kb): wire ambient hooks and add /kb:mode and /kb:connect"
```

---

### Task 7: Companion — reposition `applied-skills` → `brain`

**Files:**
- Modify: `.claude-plugin/marketplace.json` (rename first plugin `applied-skills` → `brain`, tighten description)
- Modify: `.claude-plugin/plugin.json` (rename + description)
- Modify: `README.md` (document the two-plugin split)
- Modify: `kb/tests/test_plugin_structure.py` (assert both plugins present)

**Interfaces:**
- Consumes: `load_json`, `REPO_ROOT` (Task 1).
- Produces: a `brain` plugin id alongside `kb` in the marketplace.

**Decision gate (from spec §7 / §9):** confirm with the user whether to (a) clean-rename `applied-skills` → `brain`, (b) keep `applied-skills` as an alias, or (c) defer. This task assumes (a); adjust if the user chose otherwise before starting.

- [ ] **Step 1: Write the failing test**

Add to `kb/tests/test_plugin_structure.py`:

```python
class TestBrainRepositioning(unittest.TestCase):
    def test_marketplace_has_brain_and_kb(self):
        market = load_json(REPO_ROOT / ".claude-plugin" / "marketplace.json")
        names = {p["name"] for p in market["plugins"]}
        self.assertIn("brain", names)
        self.assertIn("kb", names)

    def test_root_plugin_json_is_brain(self):
        manifest = load_json(REPO_ROOT / ".claude-plugin" / "plugin.json")
        self.assertEqual(manifest["name"], "brain")
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `cd kb/tests && python3 -m unittest test_plugin_structure.TestBrainRepositioning -v`
Expected: FAIL — plugin still named `applied-skills`.

- [ ] **Step 3: Rename in the manifests**

In `.claude-plugin/plugin.json`: set `"name": "brain"` and tighten `"description"` to a build/maintain/deploy phrasing (e.g. "Build, maintain, and deploy the Brain — a local, truthful knowledge engine (RAG + taxonomy graph + deterministic marts) served over a governed MCP.").

In `.claude-plugin/marketplace.json`: rename the first entry `"name": "applied-skills"` → `"brain"` and align its description. Leave the `kb` entry from Task 1 intact.

- [ ] **Step 4: Update the README**

In the root `README.md`, add a short "Two plugins" note: `brain` (build/maintain/deploy) and `kb` (interrogate/author). Keep existing install instructions; add the `kb` install line.

- [ ] **Step 5: Run all tests and verify they pass**

Run: `cd kb/tests && python3 -m unittest discover -s . -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add .claude-plugin/plugin.json .claude-plugin/marketplace.json README.md kb/tests/test_plugin_structure.py
git commit -m "refactor(brain): reposition applied-skills plugin as the brain builder/maintainer"
```

---

## Final verification

- [ ] Run the full suite: `cd kb/tests && python3 -m unittest discover -s . -v` — all green.
- [ ] Validate the plugin loads: `claude plugin validate ./kb` (or the marketplace `claude plugin marketplace add .` in a scratch checkout) — manifest, skills, agent, and hooks parse; `/kb:*` skills appear.
- [ ] Manual smoke (needs a real Brain): `/kb:connect` detects it; `/kb:ask "<known question>"` returns a cited answer; `/kb:mode on` then a new session shows the ambient reminder and the SessionStart health line.

## Self-review notes

- **Spec coverage:** §4 boundary → Global Constraints + Task 3–5 skill bodies; §5.1 skills → Tasks 3,4,5,6c; §5.2 doctrine → Task 2; §5.3 authoring/citation → Task 5; §5.4 verifier → Task 2 (+ used in Task 5); §5.5 ambient → Tasks 6a/6b/6c (health via local read — deviation documented in Global Constraints); §6 packaging → Task 1; §7 companion → Task 7; §8 testing → tests in every task + Final verification.
- **Deviation:** SessionStart health uses a local `sqlite3` read rather than the MCP `health` tool (a shell hook cannot call MCP). Documented in Global Constraints; behaviour preserved.
- **Open decision carried to execution:** the §7 rename (alias vs clean vs defer) — Task 7 has a decision gate.
