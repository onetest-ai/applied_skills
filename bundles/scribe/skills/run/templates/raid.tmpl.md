---
id: raid
version: 1
goal: "A RAID log for {{name}} — risks, assumptions, issues and dependencies — grounded entirely in the Brain and raw source material."
params: [name, aliases]
inputs:
  brain: { tags: [] }
  raw:   { match: "{{aliases}}", globs: ["**/*"], exclude: [] }
  prior: latest
  tasks: []
output:
  formats: [docx, pdf]
  versioning: extend
  sections:
    - id: risks
      title: "Risks"
      intent: "Things that could go wrong for {{name}} that have not happened yet, and how likely or severe they look."
      queries: ["{{name}} risk", "{{name}} could fail", "{{name}} concern"]
      lanes: [narrative]
    - id: assumptions
      title: "Assumptions"
      intent: "Things being taken as given for {{name}} that have not been verified."
      queries: ["{{name}} assumption", "{{name}} assuming", "{{name}} taken as given"]
      lanes: [narrative]
    - id: issues
      title: "Issues"
      intent: "Problems affecting {{name}} right now, distinct from risks that have not materialized yet."
      queries: ["{{name}} issue", "{{name}} problem", "{{name}} blocker"]
      lanes: [narrative, "temporal?"]
    - id: dependencies
      title: "Dependencies"
      intent: "What {{name}} depends on to proceed — people, systems, decisions, or other workstreams."
      queries: ["{{name}} dependency", "{{name}} depends on", "{{name}} waiting on"]
      lanes: [narrative]
acceptance:
  - { check: sections_present }
  - { check: zero_unverified }
  - { check: diagrams_render }
---

# Drafting guidance — RAID log

Write as a scannable log, one entry per risk/assumption/issue/dependency,
not prose paragraphs. Each entry should be a self-contained claim a reader
can act on without cross-referencing the others.

- **Cite every claim.** Every entry needs at least one citation tag
  (`[RAG:...]`, `[FILE:...]`, or `[TASK:...]`). Anything you cannot ground,
  write as `Not modeled: <what is missing>` rather than asserting it uncited.
- **Keep the four categories distinct.** A Risk has not happened; an Issue
  is happening now. An Assumption is unverified; if it turns out to be
  false, that becomes an Issue, not a retroactively corrected Assumption —
  the merge step handles supersession, not you.
- **Numbers only from `get_metric` or a quoted "reported in `<file>`".**
- **Never state an inference as a finding.** A risk you infer from context
  but nobody named is worth flagging — write it as an open question inside
  the Risks section (`Not modeled: whether <X> was actually raised as a
  risk`), not as a confirmed entry.
- **Keep the speaker's modality.** A claim whose evidence is conversational — a
  `[FILE:]` transcript passage or a `[RAG:]` chunk from a transcript source — keeps
  the speaker's modality: a question, guess, hypothesis, proposal or plan is
  attributed ("<role> asked whether…", "<role> suggested…") rather than restated as
  a finding; when a later turn answers or contradicts it, the answer is what the
  claim reports.
- **Extend, don't rewrite.** Sections that are not stale are carried
  forward byte-for-byte by the merge step — you never see or touch them.
  Only draft the sections you are handed as stale.
