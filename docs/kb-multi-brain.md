# kb against multiple Brains

**Date:** 2026-09-18
**Status:** implemented

## Problem

Every `kb` skill binds to a Brain by literal MCP server name — `mcp__brain__*` or
`mcp__plugin_brain_brain__*`. Users who have more than one Brain, which is now the
common case, cannot use kb against a Brain registered under any other name.

Today's documented workaround makes that explicit. `skills/connect/SKILL.md` tells the
user to **rename** their connector to `brain`, and `docs/cowork-setup.md` tells a
two-project user to **disable the other project's connector** so exactly one connector
named `brain` is active. Both are unworkable when Brains are genuinely plural.

### What actually blocks, and what does not

Three distinct mechanisms hide behind one symptom. They need different fixes, and one of
them turns out not to be a blocker at all.

**`allowed-tools:` in a SKILL.md is not a restriction.** Per the Claude Code docs it
"grants permission for the listed tools during the turn that invokes the skill... It does
not restrict which tools are available: every tool remains callable, and your permission
settings still govern tools that are not listed." The literal server names in the six kb
skills are pre-approval for the wrong names. Removing them costs auto-approval, not
reachability.

**`tools:` in a subagent IS a restriction.** Per the docs it is an allowlist — a subagent
so configured "can't edit files, write files, or use any MCP tools" outside it, and
"inherits every tool available to subagents if omitted". `agents/verifier.md` grants
exactly the two `brain` namespaces, so against any other Brain the verifier launches with
no evidence access at all. This is a hard block.

**The prose blocks hardest.** The skills instruct the model to call specific namespaces,
and `agents/verifier.md` step 2 says: *"If neither `mcp__brain__health` nor
`mcp__plugin_brain_brain__health` answers, STOP."* A healthy Brain under any other name
produces a hard refusal that no deliverable can get past.

### The escape hatch is closed

A wildcard allow-rule cannot solve this. Per the docs: *"Allow rules accept tool-name
globs only after a literal `mcp__<server>__` prefix. The server segment must be glob-free
so the rule names a specific server you configured."* `mcp__*__search_knowledge` is
skipped with a warning. Any design that needs to name servers ahead of time is dead.

## Constraints

**Cowork is the primary surface, and it has no local access.** Hooks do not fire there,
CLI plugin state does not sync, and kb cannot rely on a per-project directory surviving
between tasks. Anything file-based is a CLI-only nicety and cannot carry the design.

**Brains and kb have different owners on different machines.** Someone builds and deploys
a Brain; kb users elsewhere connect to endpoints. A kb user facing two anonymous Brains
cannot run `onboard.py`, edit `meta`, or redeploy. **kb's correctness must not depend on
any brain-side change**, or the people this is meant to unblock stay blocked waiting on
another team.

**What is available everywhere:** the model's own tool list, and `health`. A connector
named `acme-brain` surfaces `mcp__acme-brain__search_knowledge` etc. *in context*, and
`health` already returns `about: {goal, audience}` from the durable `meta` table in every
currently deployed Brain (`mcp/brain/semantic_core.py:505`). That is sufficient to
identify and disambiguate Brains with zero coordination.

## Design

Two parts, deliberately sequenced. **Part A is self-contained and ships alone.** Part B
improves how Part A reads and ships on whatever schedule each Brain's owner keeps. Part A
must never require Part B.

### Part A — kb (no brain-side dependency)

#### A1. The discovery contract

Replaces the "Namespace Detection" section of `skills/_shared/doctrine.md`.

> A **Brain** is any MCP server in your available tools exposing the Brain surface —
> `health`, `search_knowledge`, `get_metric`, `get_taxonomy`, `get_evidence`,
> `find_related_content`, `list_metrics`. Identify it by that surface, never by its server
> name. `mcp__brain__*`, `mcp__plugin_brain_brain__*`, `mcp__acme-brain__*` and
> `mcp__knowledge__*` are all equally valid.

Resolution, once per skill invocation:

1. **Pinned** (Task 14) — if the project's own instructions (`CLAUDE.md`, `AGENTS.md`, or
   the project instructions Cowork surfaces) name the Brain this project uses, resolve
   that one and say which. **If it is named but not reachable, stop** rather than falling
   through to discovery — a pin is an explicit instruction, and answering from a different
   store would put the project's own citations behind numbers it never sanctioned. List
   the Brains that ARE reachable and give the corrected pin line to paste. This step
   supersedes the persisted-pointer idea this doc originally cut (see **Out of scope**):
   the "pointer" turns out to already exist — it's the project's own instructions file,
   which nobody has to build or maintain a mechanism for.
2. **Override** — the user named a Brain in the request ("ask the acme brain..."). Match
   case-insensitively against the server segment and against each candidate's
   `about.goal`. Use it.
3. **Discover** — scan available tools for servers carrying the surface; call `health` on
   each candidate.
4. **Exactly one healthy Brain** — use it, name it in one short line, proceed.
5. **Several** — ask the user, listing each as `connector-name — about.goal`. The
   connector name is meaningful because the user chose it when adding the connector; the
   goal distinguishes two similarly named stores. If a candidate advertises a name (Part
   B), prefer it over the goal in the label; kb never requires one.
6. **None** — no Brain answered; name in one sentence what registering one takes on this
   surface (a custom connector in Cowork, or an `mcpServers` entry in `.mcp.json` for the
   CLI — no repo paths, they don't resolve in Cowork) and, once one is reachable, pin it in
   the project's instructions so future invocations skip discovery.

`health` is already called for `about`, which doctrine uses to set altitude, so
disambiguation adds no round-trip beyond probing additional candidates.

**One invocation binds to one Brain.** Every subsequent call in that invocation uses the
resolved server. Blending lanes across two Brains inside one cited answer is the failure
mode that silently corrupts a deliverable; doctrine forbids it explicitly.

#### A2. Answering skills

`ask`, `explore`, `challenge`, `brief`, `report`:

- Step 1 stops naming namespaces and defers to the doctrine contract.
- `allowed-tools` drops both literal server lists. Consequence, accepted: Brain calls
  prompt for permission rather than being pre-approved. kb writes no permission rules and
  no settings files; a user who wants silence adds `mcp__<their-server>` to their own
  `permissions.allow`. This guidance originally lived in `skills/connect/SKILL.md`'s "On
  approvals" paragraph; Task 14 withdrew that skill (see A3) and relocated the guidance to
  `bundles/kb/README.md` (CLI: `.claude/settings.json`) and `bundles/kb/docs/cowork-setup.md`
  (Cowork: no equivalent file is surfaced there, so the doc says so plainly instead).

#### A3. `connect` — withdrawn (Task 14)

This section originally planned to *fix* `connect` — stop it being a renaming
instruction, have it discover and report every Brain it finds. Task 14 withdraws the skill
instead: registering a Brain **is** adding an MCP server, which is platform plumbing kb
cannot perform. A skill whose body reads "Customize → Connectors → Add custom connector"
is a documentation page wearing a SKILL.md, not something an agent executes. The
discover-and-report half of `connect`'s job (find every reachable Brain, report its goal)
is exactly what the contract's own **Discover**/**Several** steps already do at answer
time — there was no work left for a standalone skill once the pinned step (A1 step 1)
removed the need to check connectivity ahead of asking a question. `skills/connect/` is
deleted; every reference to `/kb:connect` in prose becomes inline guidance (see A1 step 6)
or a pointer to `/kb:ask` for verification.

#### A4. The verifier

`agents/verifier.md`:

```yaml
# before — hard allowlist, blind to any other Brain
tools: Read, Grep, mcp__brain__health, mcp__brain__get_evidence, ...
# after — inherits every MCP tool; read-only enforced by the platform
disallowedTools: Write, Edit, NotebookEdit, Bash, Task, SlashCommand
```

This is not uniformly stronger than today — it is stronger on file mutation ("never edits
files" moves from a prose promise to a platform guarantee) but weaker on blast radius (an
allowlist-less subagent inherits every other tool, including `Task` and `SlashCommand`,
unless explicitly disallowed). The verifier's `disallowedTools` list adds `Task` and
`SlashCommand` alongside `Write`, `Edit`, `NotebookEdit`, `Bash` for exactly this reason:
Brain access stops depending on a name, without handing the verifier the ability to spawn
further agents or invoke slash commands.

Step 2's hard stop is kept but re-aimed: it fires when **no Brain-shaped server answers at
all**, not on a name mismatch. A verifier without evidence access must never return
`verified`.

**The dispatching skill passes the resolved Brain's identity in the dispatch prompt.**
Without it a verifier facing two Brains could re-resolve to the wrong one and "verify" a
draft against a store it did not come from — a silent wrong answer, the worst outcome
here.

#### A5. Documentation

`docs/cowork-setup.md`: name the connector anything meaningful, keep every project's
connector enabled, pick per question. The disable-the-other-connector workaround is
deleted. The Cowork caveat that remains, stated plainly: with several Brains live, kb asks
once per conversation, because there is nowhere to remember the answer. Answering in
advance ("ask the acme brain about X") skips it.

### Part B — brain (optional, separate, no kb dependency)

Every Brain currently self-describes identically — `name="Semantic Knowledge Brain"` and a
static `INSTRUCTIONS` (`mcp/brain/fastmcp_server.py:106`). Nothing names the project, so
two deployed Brains are indistinguishable in context. Part B fixes that at the source.

- `meta` gains an optional `name` key: the project/Brain name.
- `knowledge-pipeline`'s `onboard.py` prompts for it alongside goal and audience, and
  records it in the scaffolded `BRAIN.md`, so it is chosen by whoever knows the corpus.
- `brain-maintenance` carries it forward on refresh and **warns** when it is empty — a
  Brain anonymous to a multi-Brain client — without failing. Carrying it forward means
  `brain_sync.write_meta` re-reads `name.txt` like it re-reads `goal.txt`, but UPSERTs
  `name` **only when that file is non-empty**: no existing project has a `name.txt`, and
  an unconditional write would clear any `meta.name` an operator set by hand.
- `fastmcp_server.py` derives its identity from `meta` at startup rather than constants:
  server `name`, a leading identity paragraph in `INSTRUCTIONS`, and the project name
  prefixed to each tool description. Identity goes in **both** instructions and tool
  descriptions: whether a client surfaces server `instructions` into context is
  client-dependent, while tool descriptions are always in context.
- `health`'s `about` gains `name`.
- Fallback chain, never an error: `meta.name` → first clause of `meta.goal` → unnamed.

kb prefers an advertised name when present and never requires one.

## Testing

Part A:

- `tests/test_skills.py::TestAnswerSkillsBrainNamespace` currently asserts the opposite of
  this design (it requires `mcp__brain__` in every answering skill). It inverts: **no
  literal MCP server name appears in any kb skill or in the verifier.**
- `tests/test_plugin_structure.py` verifier assertions (lines 75–76) become: no `tools:`
  allowlist; `disallowedTools` present and covering `Write`, `Edit`, `NotebookEdit`, `Bash`,
  `Task`, `SlashCommand`.
- New: doctrine states the discovery contract and the one-Brain-per-invocation rule;
  `connect` contains no renaming instruction; `cowork-setup.md` contains no
  disable-a-connector instruction.

Part B:

- The `meta.name` → `goal` → unnamed fallback chain, including a store with no `meta`
  table at all.
- `health.about` carries `name`.
- Server `instructions` and every tool description carry the project name when `meta.name`
  is set, and remain valid when it is not.

## Out of scope

- Writing `.claude/settings.json` permission rules from kb.
- Any **kb-maintained** persisted "current Brain" pointer (a `kb.toml` or similar
  registry). Considered and cut: it is CLI-only, and the CLI is not the primary surface —
  and nobody maintains a pointer mechanism, least of all in Cowork. **Superseded by the
  pinned step (Task 14, A1 step 1):** the project's own instructions file (`CLAUDE.md`,
  `AGENTS.md`, or Cowork's project instructions) already exists, is already read by every
  agent, and needs no new mechanism — naming the Brain there once is the whole of the
  work. kb never writes that file; the user names the Brain in it themselves.
- Querying two Brains inside one answer. One invocation, one Brain.

## Finding: skills must not depend on `../_shared/` for their rules (Task 13)

Confirmed while inlining the contract: the Agent Skills format only guarantees
same-directory/subdirectory file references resolve for a `SKILL.md` at runtime — a
`../_shared/doctrine.md` reference from `skills/ask/SKILL.md` is not guaranteed to
resolve. A rule that only applies when a sibling-of-parent file happens to resolve is not
a rule. The fix: the non-negotiable contract lives once, canonically, in a delimited
`<!-- BRAIN-CONTRACT:START -->` … `<!-- BRAIN-CONTRACT:END -->` block inside
`skills/_shared/doctrine.md`, and every answering skill (`ask`, `explore`, `challenge`,
`brief`, `report`) carries that block inlined **byte-identically** in its own `SKILL.md`.
A drift test (`tests/test_skills.py::TestBrainContractIsInlined`) diffs each copy against
the canonical block on every run, so an edit to the contract that is not propagated to all
five copies fails CI. `agents/verifier.md` carries a deliberate, reduced subset of the
same resolution rules (it is not itself an answering skill and the answer-format half does
not apply to it) — consistent with the contract, but not byte-pinned by the drift test.
Depth and worked examples may still live in `_shared/`; the rules that gate behavior may
not.
