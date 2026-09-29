---
id: discovery-digest
version: 1
goal: "Roll up decisions, open questions, and workstream progress across the engagement's other Scribe tasks into one internal digest."
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
    - id: decisions
      title: "Decisions"
      intent: "Decisions and plan changes made so far. An overtaken statement is never deleted — it stays, marked '**Superseded (<date>):**', pointing at what replaced it."
      queries: ["{{name}} decision", "{{name}} decided", "{{name}} plan change"]
      lanes: [narrative, "temporal?"]
    - id: open-questions
      title: "Open questions"
      intent: "Questions raised across the engagement that are still unresolved."
      queries: ["{{name}} open question", "{{name}} unresolved", "{{name}} TBD"]
      lanes: [narrative, "temporal?"]
    - id: workstream-highlights
      title: "Workstream highlights"
      intent: "What changed in the other Scribe documents this cycle, cited back to the claims that changed."
      queries: ["{{name}} update", "{{name}} progress", "{{name}} highlight"]
      lanes: [narrative, tasks]
    - id: next-steps
      title: "Next steps"
      intent: "What happens next and who owns it."
      queries: ["{{name}} next steps", "{{name}} action item", "{{name}} follow up"]
      lanes: [narrative]
acceptance:
  - { check: sections_present }
  - { check: zero_unverified }
  - { check: diagrams_render }
---

# Drafting guidance — discovery digest

Internal audience: engagement team members already know the domain, so skip
scene-setting and get straight to what changed and what's open.

- **Cite every claim.** Narrative claims cite `[RAG:...]`/`[FILE:...]` as
  usual; a claim pulled from another Scribe task's document cites
  `[TASK:<task id>#c:<claim id>]` instead of re-deriving it from raw evidence.
- **Never delete an overtaken decision.** When a decision is superseded,
  keep the original statement and prefix it `**Superseded (<date>):**`,
  with a citation pointing at whatever replaced it. Deleting history here is
  a drafting error, not a cleanup.
- **Workstream highlights come from upstream tasks' `Changes in this version`
  sections**, not from re-reading the Brain — cite the specific upstream
  claim you are summarizing with `[TASK:]`.
- **Gaps as `Not modeled:`.** If a workstream has nothing to report this
  cycle, say so plainly rather than inventing filler.
- **Keep the speaker's modality.** A claim whose evidence is conversational — a
  `[FILE:]` transcript passage or a `[RAG:]` chunk from a transcript source — keeps
  the speaker's modality: a question, guess, hypothesis, proposal or plan is
  attributed ("<role> asked whether…", "<role> suggested…") rather than restated as
  a finding; when a later turn answers or contradicts it, the answer is what the
  claim reports.
- **Extend, don't rewrite.** Non-stale sections are carried forward
  byte-for-byte by `merge`; only draft the sections you are handed as stale.
