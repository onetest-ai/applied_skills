---
id: weekly-digest
version: 1
goal: "A short weekly digest of {{name}} — what happened, what's decided, what's still open — grounded entirely in the Brain and raw source material."
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
    - id: this-week
      title: "This week"
      intent: "What happened in {{name}} this week — meetings held, material reviewed, work completed."
      queries: ["{{name}} this week", "{{name}} update", "{{name}} progress"]
      lanes: [narrative, "temporal?"]
    - id: decisions
      title: "Decisions"
      intent: "Decisions made about {{name}} this week. An overtaken statement is never deleted — it stays, marked '**Superseded (<date>):**', pointing at what replaced it."
      queries: ["{{name}} decision", "{{name}} decided", "{{name}} agreed"]
      lanes: [narrative, "temporal?"]
    - id: open-questions
      title: "Open questions"
      intent: "Questions raised this week that are still unresolved, including anything time-sensitive."
      queries: ["{{name}} open question", "{{name}} unresolved", "{{name}} TBD"]
      lanes: [narrative, "temporal?"]
    - id: next-week
      title: "Next week"
      intent: "What's planned for next week and who owns it."
      queries: ["{{name}} next steps", "{{name}} planned", "{{name}} upcoming"]
      lanes: [narrative]
acceptance:
  - { check: sections_present }
  - { check: zero_unverified }
  - { check: diagrams_render }
---

# Drafting guidance — weekly digest

Internal audience: engagement team members already know the domain, so skip
scene-setting and get straight to what changed this week. Keep it short —
this is a digest, not a report; a reader should be able to finish it in
under two minutes.

- **Cite every claim.** Every paragraph or bullet needs at least one
  citation tag (`[RAG:...]`, `[FILE:...]`, or `[TASK:...]`). Anything you
  cannot ground, write as `Not modeled: <what is missing>` rather than
  asserting it uncited.
- **Never delete an overtaken decision.** When a decision is superseded,
  keep the original statement and prefix it `**Superseded (<date>):**`,
  with a citation pointing at whatever replaced it. Deleting history here is
  a drafting error, not a cleanup.
- **Numbers only from `get_metric` or a quoted "reported in `<file>`".**
- **Never state an inference as a finding.** If something looks like it
  happened this week but the evidence doesn't confirm the timing, put it in
  Open questions rather than This week.
- **Extend, don't rewrite.** Sections that are not stale are carried
  forward byte-for-byte by the merge step — you never see or touch them.
  Only draft the sections you are handed as stale.
