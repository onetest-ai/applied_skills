---
description: Use when a claim needs adversarial testing against the Brain — finds contradicting evidence, verifies metrics at the stated grain, and surfaces silent gaps marked as "not modeled".
arguments: [claim]
---

Adversarially test **$claim** against the Brain. The Brain contract below governs; `../_shared/doctrine.md` holds worked examples and depth.

1. **Resolve the Brain.** Use the contract's resolution order: identify candidate servers by tool surface, `health` each, use the override if the user named one, ask if several answer, and follow the contract's guidance if none does.

2. **Decompose the claim.** Break it into sub-claims: narratives, numbers, relations, and visual references.

3. **Search for contradictions.** Use `search_knowledge` to find sections that contradict or refine each claim, and `get_evidence` to pull a specific cited section directly. Look for caveats, conditions, and dissenting evidence.

4. **Verify all numbers.** For each metric claim, call `get_metric` to confirm it exists at the stated **grain**. Flag any grain mismatches (e.g., claimed quarterly but only monthly available).

5. **Surface silent gaps.** Mark anything unsupported as "Not modeled: …" — gaps are as important as confirmations.

6. **Render a verdict per sub-claim.** Lead each with its verdict — CONFIRMED, REFINED, CONTRADICTED, or NOT MODELED — then the evidence, cited with a numbered footnote `[1]`, `[2]`, … Close with a `**Sources**` list mapping each number to its **source file (and section) or metric `source_file`** — never the raw chunk_id. Follow the contract's answer format below.

## The Brain contract (non-negotiable)

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

See `../_shared/doctrine.md` → **Brain Discovery** for the full discussion and worked
examples.
