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

## Namespace Detection

The Brain may be deployed in the agent's own namespace or in a plugin namespace:
- Call `health` first to discover the active namespace.
- Use whichever namespace answers: `mcp__brain__*` (shared) or `mcp__plugin_<plugin>_brain__*` (scoped).

This doctrine applies uniformly regardless of namespace.

The `verifier` subagent is dispatched with whichever active Brain MCP tools are available (`mcp__brain__*` or `mcp__plugin_applied-skills_brain__*`) and operates read-only.
