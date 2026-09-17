# kb in Cowork — design

**Date:** 2026-09-17
**Status:** Approved (design), pending implementation plan
**Supersedes:** PR #14 (`feat/generic-brain-cowork-plugin`) approach

## Problem

We want the `kb` knowledge-worker plugin ("the librarian") usable inside
**Claude Cowork** (Claude Desktop), not just the Claude Code CLI, so that
100+ project teams can interrogate their own Brain from Cowork.

PR #14 attempted this with a separate `cowork/brain-cowork/` template built
around a **localhost bridge** (`http://127.0.0.1:<port>/mcp`) plus a macOS
installer (`launchctl` + plist), a config file + `.env`, and a ZIP builder.

That approach is **architecturally wrong for Cowork** and must be replaced.

## Findings that drive the design

Researched against official docs (docs.claude.com, code.claude.com/docs,
support.claude.com) and confirmed by local inspection.

1. **Cowork MCP connections originate from Anthropic's cloud, not the local
   machine** — even on Desktop/Cowork. Therefore a `127.0.0.1` bridge is
   unreachable from where Cowork connects. The bridge is not a
   macOS-portability problem; it is the wrong mechanism. Source:
   support.claude.com custom-connectors article.
2. **Cowork natively supports remote MCP servers** as *custom connectors*:
   the user pastes a remote HTTPS MCP URL and configures auth (Advanced
   settings expose OAuth client id/secret). Transport is Streamable HTTP.
3. **The Brain MCP endpoint already supports remote auth** — Entra OAuth and
   `X-API-Key` (per project owner). Entra OAuth matches Cowork's connector UI
   and needs no distributed static secret.
4. **CLI plugins do NOT sync to Cowork** (separate install state; bug #40600).
   A plugin must be installed into Cowork separately — via a marketplace added
   by GitHub URL, or a ZIP upload (≤50 MB).
5. **Hooks do not fire in Cowork** (#47993, #63360). `kb`'s `SessionStart`
   health-line and `UserPromptSubmit` ambient-reminder will not run there.
6. **Endpoints are per-project**; one person may belong to two projects (so a
   user may hold multiple Brain connectors, but works one project per task).

## Decision

Ship **one `kb` plugin that works on both CLI and Cowork.** Do not create a
separate Cowork bundle. Keep the `kb` name. Delete PR #14's machinery.

Chosen approach (was "Approach A"): **marketplace-installed skills + a
per-project remote custom connector configured in Cowork's UI.** Config lives
in exactly one place — the connector — which is where Cowork supports it.

Rejected alternatives:
- **Thin ZIP template with a declared connector** — reintroduces per-team ZIP
  builds (the redundancy we are removing); only wins for a handful of
  endpoints, not 100+.
- **Single declared connector for a shared endpoint** — invalid; endpoints are
  per-project.

## Architecture

Two independently-understandable pieces, no local runtime:

### Skills (the librarian)
The existing `bundles/kb` skills, installed into Cowork from the `applied-ai`
GitHub marketplace. Identical for every project; nothing per-team is built.
ZIP upload remains a documented fallback but we build no tooling for it.

### Brain connector (the only per-project config)
Each project's Brain is added in Cowork as a **remote custom connector**:
- URL: that project's HTTPS MCP endpoint.
- Auth: **Entra OAuth** (preferred; no distributed secret). `X-API-Key` is the
  fallback where the connector UI allows a custom header.
- **Naming convention:** name the connector `brain`, so its tools resolve as
  `mcp__brain__*` — exactly what the skills already allow.
- Multi-project user: keep one connector per project; enable the one named
  `brain` for the current task (one active Brain per Cowork session).

## Component changes

### `bundles/kb/skills/connect` — make surface-aware
Branch by environment:
- **Cowork:** guide the user to add a custom connector named `brain`
  (paste URL, authorize Entra OAuth), enable it, then re-run health.
- **CLI:** keep today's `./brain mcp-config` → `.mcp.json` flow.
- Keep the existing two-namespace health probe
  (`mcp__brain__health`, `mcp__plugin_brain_brain__health`).

### `bundles/kb/skills/mode` — document Cowork limitation
Ambient mode depends on the `UserPromptSubmit` hook, which does not fire in
Cowork. State this plainly in the skill; in Cowork, grounding is driven by
skill instructions rather than the hook. No crash — honest degradation.

### Answer skills (`ask`, `brief`, `challenge`, `explore`, `report`)
No behavior change expected when the connector is named `brain`. Audit each
`allowed-tools` line for any local-only assumption; generalize only if needed.

### Hooks
Left in the plugin (harmless no-op in Cowork). Not relied upon for Cowork
behavior. `health-line.sh` reads a local `knowledge.sqlite` and is inherently
CLI-only; documented as such.

### Docs
Add one Cowork setup doc under `bundles/kb/` (or repo `docs/`): add marketplace
→ install `kb` → add the `brain` connector (Entra OAuth, per project) → verify
with `/kb:connect`. Include the multi-project connector convention. No per-OS
instructions — nothing local runs.

### Removals (from PR #14)
Delete the entire `cowork/brain-cowork/` tree — bridge, `install.mjs`,
`build-zip.mjs`, `make-zip.mjs`, plist generation, `brain.config*.json`,
`brain-librarian` SKILL, `docs/`, and `tests/zip.test.mjs` — and the third
`brain-cowork` entry in `.claude-plugin/marketplace.json`.

## Data flow

```
Cowork task
  → skill (/kb:ask, …) calls mcp__brain__* tools
    → Cowork routes the MCP call from Anthropic's cloud
      → project's remote Brain MCP endpoint (Entra OAuth)
        → cited results back to the skill → cited answer
```

No local process, no bridge, no secret on the user's machine (OAuth token
held by Cowork's connector layer).

## Error handling / honesty

- `/kb:connect` reports clearly when no `brain` connector answers, and gives
  the Cowork-specific remediation (add/enable the connector).
- Unsupported-in-Cowork features (ambient hook, local health-line) are stated
  as limitations, never silently assumed to work.
- Existing `not_modeled` / citation discipline in the answer skills is
  unchanged.

## Testing

- Remove `tests/zip.test.mjs` (its subject is deleted).
- Extend `bundles/kb/tests/` (`test_skills.py`, `test_plugin_structure.py`) to
  assert: `connect` contains both a Cowork and a CLI branch; `mode` documents
  the Cowork ambient limitation; plugin structure remains valid.
- No installer/bridge to test.

## Cross-platform

Achieved by construction: the only artifacts are Markdown skills and a
marketplace entry; the connector runs in Anthropic's cloud. All macOS-specific
concerns (`launchctl`, plist, `~/Library/...`) disappear.

## Open items to verify during implementation

- Confirm a Cowork custom connector namespaces its tools by connector name
  (so `brain` → `mcp__brain__*`). If Cowork uses a fixed prefix instead,
  generalize the skills' `allowed-tools` accordingly.
- Confirm whether the connector UI permits a static `X-API-Key` header, or if
  Entra OAuth is the only in-UI auth path.
