---
id: open-questions-decisions
version: 1
goal: "Track the engagement's live questions and the decisions that resolve them, grounded entirely in the Brain and raw source material."
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
    - id: open-questions
      title: "Open questions"
      intent: "Questions raised about {{name}} that are still unresolved, including anything time-sensitive."
      queries: ["{{name}} open question", "{{name}} unresolved", "{{name}} TBD"]
      lanes: [narrative, "temporal?"]
    - id: decisions
      title: "Decisions"
      intent: "Decisions made about {{name}} so far. An overtaken statement is never deleted — it stays, marked '**Superseded (<date>):**', pointing at what replaced it."
      queries: ["{{name}} decision", "{{name}} decided", "{{name}} agreed"]
      lanes: [narrative, "temporal?"]
    - id: pending-decisions
      title: "Pending decisions"
      intent: "Decisions that need to be made but haven't been, and who owns making them."
      queries: ["{{name}} decision needed", "{{name}} pending", "{{name}} to decide"]
      lanes: [narrative]
    - id: escalations
      title: "Escalations"
      intent: "Questions or decisions that have been raised to someone above the working group, and their status."
      queries: ["{{name}} escalation", "{{name}} raised to", "{{name}} sign off"]
      lanes: [narrative]
acceptance:
  - { check: sections_present }
  - { check: zero_unverified }
  - { check: diagrams_render }
---

# Drafting guidance — open questions & decisions

Write as a running log, not a narrative: short, dated entries a reader can
scan for status at a glance.

- **Cite every claim.** Every entry needs at least one citation tag
  (`[RAG:...]`, `[FILE:...]`, or `[TASK:...]`). Anything you cannot ground,
  write as `Not modeled: <what is missing>` rather than asserting it uncited.
- **Never delete an overtaken decision.** When a decision is superseded,
  keep the original statement and prefix it `**Superseded (<date>):**`, with
  a citation pointing at whatever replaced it. Deleting history here is a
  drafting error, not a cleanup.
- **Numbers only from `get_metric` or a quoted "reported in `<file>`".**
- **Never state an inference as a finding.** If something looks decided but
  was never actually confirmed, it belongs in Open questions, not Decisions.
- **Keep the speaker's modality.** A claim whose evidence is conversational — a
  `[FILE:]` transcript passage or a `[RAG:]` chunk from a transcript source — keeps
  the speaker's modality: a question, guess, hypothesis, proposal or plan is
  attributed ("<role> asked whether…", "<role> suggested…") rather than restated as
  a finding; when a later turn answers or contradicts it, the answer is what the
  claim reports.
- **Extend, don't rewrite.** Sections that are not stale are carried
  forward byte-for-byte by the merge step — you never see or touch them.
  Only draft the sections you are handed as stale.
