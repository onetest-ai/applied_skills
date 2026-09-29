---
id: engagement-summary
version: 1
goal: "A single executive-facing summary of the engagement — scope, what's been learned, where value looks likely, and what's still open — grounded entirely in the Brain and raw source material."
params: [name]
inputs:
  brain: { tags: [] }
  raw:   { match: [], globs: ["**/*"], exclude: [] }
  prior: latest
  tasks: []
output:
  formats: [docx, pdf]
  versioning: extend
  sections:
    - id: scope
      title: "Scope"
      intent: "What {{name}} covers, who's involved, and what's explicitly out of scope."
      queries: ["{{name}} scope", "{{name}} objective", "{{name}} out of scope"]
      lanes: [narrative]
    - id: headline-findings
      title: "Headline findings"
      intent: "The two or three things discovery has surfaced that matter most, at a glance."
      queries: ["{{name}} finding", "{{name}} summary", "{{name}} key takeaway"]
      lanes: [narrative]
    - id: by-the-numbers
      title: "By the numbers"
      intent: "The figures that ground the engagement's scale — volumes, costs, counts."
      queries: ["{{name}} volume", "{{name}} cost", "{{name}} metric"]
      lanes: [numbers, narrative]
      must:
        - "every figure cited to get_metric or written 'reported in <file>'"
    - id: value-signals
      title: "Value signals"
      intent: "Where discovery points toward measurable value, without committing to a number that isn't computed."
      queries: ["{{name}} opportunity", "{{name}} value", "{{name}} improvement"]
      lanes: [narrative]
    - id: open-questions
      title: "Open questions"
      intent: "What is still unresolved and needs an answer before the next phase, including anything time-sensitive."
      queries: ["{{name}} open question", "{{name}} unresolved", "{{name}} TBD"]
      lanes: [narrative, "temporal?"]
acceptance:
  - { check: sections_present }
  - { check: zero_unverified }
  - { check: diagrams_render }
---

# Drafting guidance — engagement summary

Write for an executive audience: someone who has not read the transcripts
and will not read the other Scribe documents. Every section should stand
alone; assume the reader skims.

- **Cite every claim.** Every paragraph or bullet needs at least one
  citation tag (`[RAG:...]`, `[MART:...]`, `[FILE:...]`, or `[TASK:...]`).
  Anything you cannot ground, write as `Not modeled: <what is missing>`
  rather than asserting it uncited.
- **Numbers only from `get_metric` or a quoted "reported in `<file>`".**
  Never restate a figure you only saw narrated in a transcript as if it
  were computed; quote it and say where it was reported instead.
- **Never state an inference as a finding.** If the evidence suggests but
  does not confirm something, put it in Open questions, not Headline
  findings or Value signals.
- Keep Headline findings to the two or three claims that would change a
  decision if wrong; let By the numbers and Value signals carry supporting
  detail.
- **Keep the speaker's modality.** A claim drawn from a meeting transcript keeps
  the speaker's modality: a question, guess, hypothesis or proposal is attributed
  ("<role> asked whether…", "<role> suggested…") or moved to Open questions, never
  restated as a finding.
- **Extend, don't rewrite.** Sections that are not stale are carried
  forward byte-for-byte by the merge step — you never see or touch them.
  Only draft the sections you are handed as stale.
