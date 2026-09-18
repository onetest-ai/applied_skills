# Shared Truth Doctrine

This doctrine governs how knowledge workers in the kb plugin retrieve, cite, and reason about facts from the Brain — ensuring every answer is verifiable, traced to source, and honest about gaps.

## The Non-Negotiable Rule

**Meaning is agentic, numbers are computed.** 
- Meaning comes from `search_knowledge` and `get_taxonomy` (reasoning and relation).
- Every number comes from `get_metric` (a governed `facts` row) or a `get_evidence` extracted table.
- Every claim is cited or declared "not modeled."

## Two Lanes, One Truth

The Brain has two surfaces:
- **MCP tools** — the precise path. Retrieval (`search_knowledge`/`find_related_content`), numbers (`list_metrics`/`get_metric`), relations (`get_taxonomy`), and cited source/table inspection (`get_evidence`).
- **Obsidian vault** — navigable human surface (browse relationships, read full context, work without an MCP server).

**Rule:** Never take a number from vault prose. Retrieved meaning and computed numbers are the single source of truth.

## Citation Tags

Every fact must carry a citation tag in one of these forms:

- **[RAG:<chunk_id>]** — A passage retrieved from `search_knowledge`; cite the chunk_id and recheck with `get_evidence`.
- **[MART:<metric>@<grain>]** — A metric from `get_metric`; include the grain (e.g., division, quarter) to disambiguate scope.
- **[GRAPH:<node>]** — A node or relation from `get_taxonomy`; verify with a second call to confirm existence.

Visual facts and table data cite `get_evidence` with the evidence document and grid row or section.

### Citations are internal anchors

Citation tags are **internal anchors**, not reader-facing references. The chunk_id / metric /
node lets you re-resolve evidence (`get_evidence`, `get_metric`, `get_taxonomy`) and lets the
`verifier` recheck a draft — but a raw id means nothing to a reader. Every `search_knowledge`
hit returns a `source` (file name) and section title next to its `chunk_id`; every `facts` /
`get_evidence` row returns a `source_file`. Never print `[RAG:<chunk_id>]`, `[MART:…]`, or
`[GRAPH:…]` to the user; the reader-facing reference is a **numbered footnote** (below). If you
hold a chunk_id but not its source, call `get_evidence(chunk_id=…)` to recover the `source`
before citing.

## Answer format

Interactive answers (`/kb:ask`, `/kb:explore`, `/kb:challenge`) follow one house style so
responses read consistently:

1. **Lead with the answer.** Open with a 1–2 sentence direct answer — the figure, the finding,
   the verdict — before any supporting detail. Never bury it under setup.
2. **Then the support, shaped to fit.** Use short bullets or a few small sections when the
   answer has parts; stay in prose when it's a single point. Don't force structure onto a
   one-line answer. Scale depth to `about.audience`: executives want the figure and the "so
   what"; analysts want grain, method, and caveats.
3. **Cite with numbered footnotes.** Mark each supported claim with `[1]`, `[2]`, … at the end
   of the sentence or bullet it backs. Reuse a number when the same source recurs.
4. **Close with Sources.** End with a `**Sources**` list mapping each number to its file and
   section — `1. source_file.md — "Section title"` (a figure shows its metric `source_file`).
   Omit the footer only when the answer cites nothing (e.g. a pure "not modeled").
5. **Surface gaps inline.** Render anything unsupported as an explicit "Not modeled: …" note,
   never as silence.

**Authored deliverables** (`/kb:brief`, `/kb:report`) use the same numbered-footnote Sources
convention but keep their own pipeline and audit sidecar — see `authoring.md`.

## Commit, Cite, Then Qualify

Answer flow: give the figure first, cite it, then qualify scope and caveats.

1. **Give the figure first.** If the question asks for a value, target, or date and it is in `facts` or a cited source, **state it** with a citation tag. Do not answer qualitatively when the value exists.
2. **Source-of-truth precedence.** When a figure appears in several places (scorecard vs. transcribed chart), cite the **authoritative source** (e.g., official scorecard over a slide transcription). If they conflict, give the authoritative value and **flag the discrepancy**.
3. **Then label the scope.** Disambiguate: total vs. KPI (segment-excluded), division/branch/region grain, reporting population (field vs. workforce). Name the one you used.
4. **Surface caveats in addition to the number** — missing rows for a date range, blank fields, rate vs. volume shifts. Never instead of the number.

**Rule of thumb:** Commit to the value, cite it, then qualify. Off-scope is a minor miss; refusing to state a retrievable figure is a real one.

## Gaps Beat Fabrication

When a number or fact is absent:
- Prefer **"not modeled in this corpus"** over an unsupported claim.
- A gap beats a guess.
- Don't sum or combine figures with different scopes/horizons/populations (flag double-count risk).

Every `source_file` in a `facts` row is the source of truth for that number. If the source_file field is blank or the field is absent, the fact is not modeled and must be declared as such.

## Source Precedence

Numbers stated on a slide are **reported** (cite as such); numbers from `facts` are **computed** (authoritative).
- When both exist: compute and reconcile, flagging any discrepancy.
- Numbers from `get_evidence` carry the `source_file` — always cite the source.
- Discrepancies between a scorecard and a transcription are red flags; escalate with both values.

## Audience & goal

On connect, `health` returns an `about` block: `{name, goal, audience}` (read from the Brain's
durable `meta` table). Use it to set altitude, not content:

- **Read it from the first `health` call.** `about.goal` is the analytical scope; `about.audience` is who consumes the KB (e.g. "call-center ops managers and workforce planners").
- **Match the audience.** Every answer's altitude and vocabulary — and every authored artifact's tone, depth, and section emphasis — should suit that audience, kept within the goal's scope. Executives want the figure and the "so what"; analysts want grain, method, and caveats.
- **Never let it override truth.** Audience tunes delivery, not facts, citations, or the "not modeled" honesty rule.
- **Empty is normal.** If `about.audience` (or the whole `about`) is empty — older store or unset — proceed normally with no persona.

## Brain Discovery

A **Brain** is any MCP server in your available tools that exposes the Brain tool surface —
identify it by that surface, never by its server name, since the name is chosen by whoever
registered it and carries no guarantee. `health` is already called to read `about` for
altitude, so disambiguation costs no extra round-trip beyond probing each candidate. The
ordered resolution steps live once, below, in the delimited contract block — not restated
here, so this file never states the order twice.

## The Brain contract (non-negotiable)

This is the canonical text of the contract every answering skill and the `verifier` carry
inline in their own file — the Agent Skills format only guarantees same-directory/
subdirectory references resolve, so a `SKILL.md` cannot depend on this file for its rules.
This copy is what CI checks every inlined copy against; edit it here, then propagate.

<!-- BRAIN-CONTRACT:START -->
**Resolve one Brain per invocation.** A Brain is any MCP server exposing the tool surface
`health`, `search_knowledge`, `get_metric`, `get_taxonomy`, `get_evidence`,
`find_related_content`, `list_metrics` — identify it by that surface, never by server name.

1. **Pinned.** If the project instructions (`CLAUDE.md`, `AGENTS.md`, or the project
   instructions surfaced in Cowork) name the Brain this project uses, resolve that one and
   say which you used. **If it is named but not reachable, stop and say so** — a pin is an
   explicit instruction, and answering from a different store would put the project's own
   citations behind numbers it never sanctioned. Do not fall through to discovery. List the
   Brains that ARE reachable and give the corrected line to paste.
2. **Override.** If the user named a Brain in the request, match it case-insensitively
   against each candidate's server-name segment and its `about.goal`. Use that Brain.
3. **Discover.** Scan available tools for servers carrying the surface and call `health`
   on each candidate.
4. **One healthy Brain.** Use it. Name it in one short line, then answer.
5. **Several.** Ask the user which, listing each as `server-name — about.goal` (prefer an
   advertised `about.name` over the goal when the Brain provides one). Do not guess.
6. **None.** Say so — no Brain answered — and name in one sentence what registering one
   takes on this surface: a custom connector in Cowork, or an `mcpServers` entry in
   `.mcp.json` for the CLI. Once one is reachable, pin it in the project's instructions so
   future invocations skip discovery, e.g.:
   ```
   This project's Brain is `acme-brain`.
   ```

**One invocation binds to one Brain.** Once resolved, every call in this invocation goes to
that same server. Never blend results from two Brains into one cited answer — a mixed
answer is unverifiable, and its citations point at stores the reader cannot reconcile.

**Numbers only from `get_metric`.** Never assert a figure from narrative; a number comes
only from `get_metric` (a governed `facts` row) or a `get_evidence` extracted table.

**Every claim is cited, or declared "Not modeled: …".** A gap beats a guess.

**Answer format.** Lead with a 1–2 sentence direct answer. Cite each supported claim with a
numbered footnote `[1]`, `[2]`, … Close with a `**Sources**` list mapping each number to
`source_file.md — "Section"`. Keep `[RAG:]`/`[MART:]`/`[GRAPH:]` as internal anchors only —
never print them to the user.
<!-- BRAIN-CONTRACT:END -->
