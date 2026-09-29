---
id: glossary
version: 1
goal: "A glossary of terms, acronyms and systems used across {{name}}, grounded entirely in the Brain and raw source material."
params: [name, tags]
inputs:
  brain: { tags: "{{tags}}" }
  raw:   { match: [], globs: ["**/*"], exclude: [] }
  prior: latest
  tasks: []
output:
  formats: [docx, pdf]
  versioning: extend
  sections:
    - id: terms
      title: "Terms"
      intent: "Domain and business terms used in {{name}} discovery, defined the way people there use them."
      queries: ["{{name}} term", "{{name}} definition", "{{name}} means"]
      lanes: [narrative]
    - id: acronyms
      title: "Acronyms"
      intent: "Acronyms and initialisms used in {{name}} discovery, spelled out."
      queries: ["{{name}} acronym", "{{name}} abbreviation", "{{name}} stands for"]
      lanes: [narrative]
    - id: systems
      title: "Systems"
      intent: "Named systems, tools and platforms referenced in {{name}} discovery, with a one-line description of what each does."
      queries: ["{{name}} system", "{{name}} tool", "{{name}} platform"]
      lanes: [narrative]
acceptance:
  - { check: sections_present }
  - { check: zero_unverified }
  - { check: diagrams_render }
---

# Drafting guidance — glossary

Write short, dictionary-style entries: one term, one definition, one
citation. Alphabetize within each section. Do not editorialize about
whether a term is well-chosen — just capture how it's actually used.

- **Cite every claim.** Every entry needs at least one citation tag
  (`[RAG:...]`, `[FILE:...]`, or `[TASK:...]`) pointing at where the term
  was used or defined. Anything you cannot ground, write as
  `Not modeled: <what is missing>` rather than asserting it uncited.
- **Define usage, not textbook meaning.** If a term is used in a
  nonstandard or domain-specific way, capture that usage, not the dictionary
  definition — cite the span where it was actually used that way.
- **Numbers only from `get_metric` or a quoted "reported in `<file>`"** —
  a glossary entry that includes a figure (e.g. "typically processed within
  N days") is still a claim like any other.
- **Never state an inference as a finding.** If you're not sure what an
  acronym expands to, don't guess — write `Not modeled: <acronym> seen but
  not expanded anywhere in evidence`.
- **Keep the speaker's modality.** A claim drawn from a meeting transcript keeps
  the speaker's modality: a question, guess, hypothesis or proposal is attributed
  ("<role> asked whether…", "<role> suggested…") or moved to Open questions, never
  restated as a finding.
- **Extend, don't rewrite.** Sections that are not stale are carried
  forward byte-for-byte by the merge step — you never see or touch them.
  Only draft the sections you are handed as stale.
