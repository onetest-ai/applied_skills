# Building an answering agent on a brain

How to write the **reasoning agent** that sits on top of a `brain` store — what to put
in *your* agent's instructions (its system prompt / `AGENTS.md` / role file) so it answers
truthfully. The brain is the tool layer (MCP: `search_knowledge`/`get_metric`/
`get_taxonomy`/`find_related_content`/`get_evidence`) + the vault; this doc is about the *agent's judgment*, not the tools.

> The per-deployment `AGENTS.md` is a separate, operational file (it tells whatever agent
> lands in a brain folder how to run the tools). This is the design guidance you bake into
> the agents you build — copy the parts that fit into their role instructions.

## The non-negotiable rule
**Meaning is agentic, numbers are computed.** The agent retrieves and reasons; it never
states a figure from prose or memory — every number comes from `get_metric` (a governed
`facts` row) or a `get_evidence` extracted-table grid, and every claim is cited or declared "not modeled."

## Two surfaces, one store
- **MCP tools** = the precise path. Retrieval (`search_knowledge`/`find_related_content`),
  numbers (`list_metrics`/`get_metric`), relations (`get_taxonomy`), and cited source/table
  inspection (`get_evidence`). Raw SQL is deliberately not exposed.
- **Obsidian vault** = navigable/human surface (browse relationships, read full context,
  work without an MCP server) — never take a number from vault prose.

## Answer flow
Decompose → route each sub-claim (narrative→`search_knowledge` · number→`get_metric` ·
relation→`get_taxonomy` · visual/table→`get_evidence` · both stated & computable→compute + reconcile) →
compose one answer, tag each fact `[RAG]`/`[GRAPH]`/`[MART]` with a citation, state
unmodeled parts plainly.

## Disambiguate — but ALWAYS commit to the figure (the highest-leverage habit, with a trap)
Disambiguation is high-leverage, but there's a failure mode to design against: an agent
told to "watch scope and caveats" can over-hedge and **stop stating numbers** — going
qualitative or "not modeled" even when the value is right there in the store. That is worse
than being off-scope. Disambiguation **adds** a scope label and an alternative; it never
**replaces** the number.

1. **Give the figure first.** If the question asks for a value/target/date and it's in
   `facts` or on a cited page, STATE it (with `[MART]`/`[STATED]` + source). Do not answer
   a "what is the value / what does it say" question qualitatively when the value exists.
2. **Source-of-truth precedence.** When a figure appears in several places (e.g. a periodic
   **scorecard** vs a **transcribed table/chart** from a slide), cite the **authoritative
   source**, not the lossy transcription; if they conflict, give the authoritative value and
   **flag the discrepancy** — never silently pick the transcription.
3. **Then label the scope; give the alternative when it matters.** a **total** vs a **KPI
   scope that excludes some segment**; **division/branch/region** grain
   (`SELECT DISTINCT grain FROM facts`); different reporting **populations** (e.g. field vs
   workforce) that must not be equated. Name the one you used; when the choice changes the
   answer, add the other's value too, or ask one crisp clarifying question if truly ambiguous.
4. **Surface caveats and confounders in ADDITION to the number** (missing rows for a date
   range, blank fields; a rate that improved while volume shifted) — never instead of it.

Rule of thumb: **commit to the value, cite it, then qualify.** Off-scope is a minor miss;
refusing to state a retrievable figure is a real one.

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
