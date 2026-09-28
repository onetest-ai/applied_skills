---
id: ai-opportunity-map
version: 1
goal: "Map candidate AI/automation opportunities for {{name}} against effort and value, grounded entirely in the Brain and raw source material."
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
    - id: map
      title: "Opportunity map"
      kind: diagram
      intent: "A Mermaid quadrant chart (or flowchart, if quadrant positioning isn't groundable) plotting candidate opportunities for {{name}} by effort and value."
      queries: ["{{name}} automation opportunity", "{{name}} AI candidate", "{{name}} effort value"]
      lanes: [narrative]
    - id: opportunities
      title: "Opportunities"
      intent: "Each candidate opportunity for {{name}}, what it would change, and the evidence behind it."
      queries: ["{{name}} automation opportunity", "{{name}} AI candidate", "{{name}} manual process"]
      lanes: ["numbers?", narrative]
      must:
        - "every figure cited to get_metric or written 'reported in <file>'"
    - id: feasibility
      title: "Feasibility considerations"
      intent: "What would make each opportunity harder or easier to build — data availability, system access, process variability."
      queries: ["{{name}} feasibility", "{{name}} data availability", "{{name}} constraint"]
      lanes: [narrative]
    - id: open-questions
      title: "Open questions"
      intent: "What is not yet known about the opportunities' viability, including anything time-sensitive."
      queries: ["{{name}} open question", "{{name}} unresolved", "{{name}} TBD"]
      lanes: [narrative, "temporal?"]
acceptance:
  - { check: sections_present }
  - { check: zero_unverified }
  - { check: diagrams_render }
---

# Drafting guidance — AI opportunity map

Write for a mixed strategic + technical audience deciding what to invest in
next. The Opportunity map is the load-bearing section: a reader should be
able to see, at a glance, which candidates look cheap-and-valuable versus
expensive-and-uncertain.

- **Cite every claim.** Every paragraph or bullet in Opportunities,
  Feasibility considerations and Open questions needs at least one citation
  tag (`[RAG:...]`, `[MART:...]`, `[FILE:...]`, or `[TASK:...]`). Anything
  you cannot ground, write as `Not modeled: <what is missing>`.
- **The opportunity map is a fenced ```mermaid``` diagram, not a claim** —
  it carries no citation tags itself, but every opportunity it places should
  be traceable to an entry in the Opportunities section (which does carry
  citations). Use a quadrant chart when effort and value are both
  groundable; fall back to a flowchart grouping opportunities by theme when
  they aren't.
- **Numbers only from `get_metric` or a quoted "reported in `<file>`".**
  An opportunity's estimated value is a claim like any other — if no figure
  is groundable, describe the opportunity qualitatively instead of inventing
  a number to place it on the map.
- **Never state an inference as a finding.** An opportunity you infer from
  a pain point but that nobody proposed as automatable belongs in Open
  questions, not the map.
- **Extend, don't rewrite.** Sections that are not stale are carried
  forward byte-for-byte by the merge step — you never see or touch them.
  Only draft the sections you are handed as stale.
