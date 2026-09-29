---
id: domain-profile
version: 1
goal: "Describe one business domain — what it does, how big it is, what hurts, and what still needs answering — grounded entirely in the Brain and raw source material."
params: [name, tags, aliases]
inputs:
  brain: { tags: "{{tags}}" }
  raw:   { match: "{{aliases}}", globs: ["**/*"], exclude: [] }
  prior: latest
  tasks: []
output:
  formats: [docx, pdf]
  versioning: extend
  sections:
    - id: overview
      title: "Overview"
      intent: "What {{name}} is, who owns it, and where it sits in the broader operation."
      queries: ["{{name}} overview", "{{name}} purpose", "{{name}} owner"]
      lanes: [narrative]
    - id: demand
      title: "Demand"
      intent: "Volume, cost, or throughput figures that describe how much {{name}} work there is."
      queries: ["{{name}} volume", "{{name}} call volume", "{{name}} cost"]
      lanes: [numbers, narrative]
      must:
        - "every figure cited to get_metric or written 'reported in <file>'"
    - id: pain-points
      title: "Pain points"
      intent: "Problems, friction, or complaints specific to {{name}} raised in discovery."
      queries: ["{{name}} pain points", "{{name}} issues", "{{name}} complaints"]
      lanes: [narrative]
    - id: processes-systems
      title: "Processes & systems"
      intent: "The workflows and systems {{name}} runs on or through."
      queries: ["{{name}} process", "{{name}} systems used", "{{name}} workflow"]
      lanes: [narrative]
    - id: open-questions
      title: "Open questions"
      intent: "What is not yet answered about {{name}}, including anything time-sensitive."
      queries: ["{{name}} open question", "{{name}} unresolved", "{{name}} TBD"]
      lanes: [narrative, "temporal?"]
acceptance:
  - { check: sections_present }
  - { check: zero_unverified }
  - { check: diagrams_render }
---

# Drafting guidance — domain profile

Write for a mixed strategic + operational audience: enough precision that an
engagement lead can act on it, without drowning in transcript detail.

- **Cite every claim.** Every paragraph or bullet in a section needs at least
  one citation tag (`[RAG:...]`, `[MART:...]`, `[FILE:...]`, or `[TASK:...]`).
  If you cannot find evidence for something worth saying, write a
  `Not modeled: <what is missing>` line instead of asserting it uncited.
- **Numbers only from `get_metric` or a quoted "reported in `<file>`".** Never
  restate a figure you only saw narrated in a transcript as if it were
  computed; quote it and say where it was reported instead.
- **Keep the speaker's modality.** A claim whose evidence is conversational — a
  `[FILE:]` transcript passage or a `[RAG:]` chunk from a transcript source — keeps
  the speaker's modality: a question, guess, hypothesis, proposal or plan is
  attributed ("<role> asked whether…", "<role> suggested…") rather than restated as
  a finding; when a later turn answers or contradicts it, the answer is what the
  claim reports.
- **Extend, don't rewrite.** Sections that are not stale are carried forward
  byte-for-byte by the merge step — you never see or touch them. Only draft
  the sections you are handed as stale.
- **Superseded content stays, marked.** If new evidence contradicts an
  earlier statement in a carried section, that is a `merge`-time concern, not
  yours to resolve here — draft your stale section on its own merits.
- Keep the Overview short (2–4 claims); let Demand and Pain points carry the
  detail.
