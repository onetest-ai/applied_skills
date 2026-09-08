# Building an answering agent on a brain

How to write the **reasoning agent** that sits on top of a `brain` store — what to put
in *your* agent's instructions (its system prompt / `AGENTS.md` / role file) so it answers
truthfully. The brain is the tool layer (MCP: `search`/`sql`/`metric`/`graph`/`related`/
`page`) + the vault; this doc is about the *agent's judgment*, not the tools.

> The per-deployment `AGENTS.md` is a separate, operational file (it tells whatever agent
> lands in a brain folder how to run the tools). This is the design guidance you bake into
> the agents you build — copy the parts that fit into their role instructions.

## The non-negotiable rule
**Meaning is agentic, numbers are computed.** The agent retrieves and reasons; it never
states a figure from prose or memory — every number comes from `sql`/`metric` (a `facts`
row) or a `page` extracted-table grid, and every claim is cited or declared "not modeled."

## Two surfaces, one store
- **MCP tools** = the precise path. Retrieval (`search`/`related`), numbers
  (`sql`/`metric`), relations (`graph`), full visual-page content (`page`).
- **Obsidian vault** = navigable/human surface (browse relationships, read full context,
  work without an MCP server) — never take a number from vault prose.

## Answer flow
Decompose → route each sub-claim (narrative→`search` · number→`metric`/`sql` ·
relation→`graph` · visual/table→`page` · both stated & computable→compute + reconcile) →
compose one answer, tag each fact `[RAG]`/`[GRAPH]`/`[MART]` with a citation, state
unmodeled parts plainly.

## Disambiguate before answering (the highest-leverage habit)
A figure usually exists at more than one **scope / grain / population**, and the choice
changes the answer. Bake this into the agent:

1. **Look for a scope/grain fork first.** Common ones: a **total** vs a **KPI scope that
   excludes some segment**; **division vs branch vs region** grain
   (`SELECT DISTINCT grain FROM facts`); which time range; two reporting systems that cover
   **different populations** (e.g. field vs workforce) and must not be equated.
2. **If the choice materially changes the answer and the question doesn't pin it down —
   ask one short clarifying question** before committing. When asking isn't practical,
   **state the assumption explicitly and give the alternative's value**
   ("at total: X; at the segment-excluded KPI scope: Y").
3. **Always name the grain/scope/population used**, and **surface every data-quality caveat
   the source flags** (missing rows for a date range, blank fields, excluded segments) —
   a caveat that weakens a claim belongs in the answer, never dropped.
4. **Call out confounders.** A rate that improved while the underlying volume shifted is
   not a clean win — say so.

## Honesty guardrails to instruct
- Prefer **"not modeled in this corpus"** over an unsupported claim; a gap beats a guess.
- Don't sum or combine figures with different scopes/horizons/populations (flag overlap/
  double-count risk) — reconcile, don't add.
- Numbers stated on a slide are **reported** (cite, label as such); numbers from `facts`
  are **computed** (authoritative). When both exist, compute and reconcile, flagging any
  discrepancy.
- Cite provenance every time: the document + section (browsable in the vault) or the
  `facts` row's `source_file` / the extracted table grid.

## Why this matters (from real scoring)
Grading a brain's answers against a gold set, the clean wins were where the agent named
its scope and cited the row; the partials were almost all **scope/grain mismatches** (a
total where the gold used a segment-excluded KPI) and **dropped caveats** (a missing-data
warning that weakened a causal claim). None were arithmetic errors. Teaching the agent to
disambiguate and to surface caveats is what turns those partials into full marks.
