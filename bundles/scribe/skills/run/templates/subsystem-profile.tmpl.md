---
id: subsystem-profile
version: 1
goal: "Describe one subsystem or tool — what it integrates with, how it's used, what hurts, and what's still open — grounded entirely in the Brain and raw source material."
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
      intent: "What {{name}} is and the role it plays."
      queries: ["{{name}} overview", "{{name}} purpose", "{{name}} owner"]
      lanes: [narrative]
    - id: context
      title: "Context diagram"
      kind: diagram
      intent: "A Mermaid flowchart of {{name}}'s integrations and call flow — what feeds it, what it calls, what depends on it."
      queries: ["{{name}} integration", "{{name}} call flow", "{{name}} architecture"]
      lanes: [narrative]
    - id: usage
      title: "Usage"
      intent: "How much {{name}} is used and by whom, in figures."
      queries: ["{{name}} usage volume", "{{name}} adoption"]
      lanes: [numbers]
      must:
        - "every figure cited to get_metric or written 'reported in <file>'"
    - id: pain-points
      title: "Pain points"
      intent: "Problems, limitations, or complaints specific to {{name}}."
      queries: ["{{name}} pain points", "{{name}} limitations", "{{name}} issues"]
      lanes: [narrative]
    - id: open-questions
      title: "Open questions"
      intent: "What is not yet answered about {{name}}, including anything time-sensitive."
      queries: ["{{name}} open question", "{{name}} unresolved"]
      lanes: [narrative, "temporal?"]
acceptance:
  - { check: sections_present }
  - { check: zero_unverified }
  - { check: diagrams_render }
---

# Drafting guidance — subsystem profile

Write for a mixed strategic + operational audience, but the Context diagram
is the load-bearing section: a reader should be able to see, at a glance,
what {{name}} talks to.

- **Cite every claim.** Every paragraph or bullet needs at least one citation
  tag. Anything you cannot ground, write as `Not modeled: <what is missing>`.
- **The context diagram is a fenced ```mermaid``` flowchart, not a claim** —
  it carries no citation tags itself, but every system/integration node name
  in it should be traceable to something said in the Overview or Usage
  sections (which do carry citations).
- **Numbers only from `get_metric` or a quoted "reported in `<file>`".**
- **Keep the speaker's modality.** A claim drawn from a meeting transcript keeps
  the speaker's modality: a question, guess, hypothesis or proposal is attributed
  ("<role> asked whether…", "<role> suggested…") or moved to Open questions, never
  restated as a finding.
- **Extend, don't rewrite.** Non-stale sections are carried forward
  byte-for-byte by `merge`; only draft the sections you are handed as stale.
- Keep node/edge labels in the diagram short — this renders to both docx and
  pdf at document width.
