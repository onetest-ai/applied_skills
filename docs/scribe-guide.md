# Scribe — a guide for the document owner

This guide is for the person a Scribe document is written for: an engagement lead, a domain
owner, whoever reads (and sometimes edits) the docx Scribe produces. It covers what to expect
from a Scribe document, what happens when you edit it, and how to review or trace what's in it.
For setting up a Scribe project (`scribe.toml`, tasks, scheduling), see
[`bundles/scribe/README.md`](../bundles/scribe/README.md).

## What a Scribe document is

Each Scribe document (a "task") is a docx (plus a pdf) rebuilt on a schedule from a Brain and a
synced folder of raw evidence. It isn't written once — every run redrafts only the sections whose
evidence has actually changed since the last publish, keeps the rest exactly as they were, and
never asserts anything the evidence doesn't support. Every sentence in the body is one of:

- a **cited claim** — a paragraph or bullet ending in a citation tag you'll never see printed as
  raw text; it renders as a numbered footnote in the docx/pdf, with a Sources section at the end
  mapping each number back to a document, a metric, or a raw file and page/paragraph.
- a **`Not modeled: …`** line — the honest alternative to a claim the evidence doesn't support.
  If a section reads thin, this is why: better a visible gap than a filled-in guess.
- a diagram (mermaid), for sections whose purpose is a process/flow picture rather than prose.

## Reading the Changes block

Every regenerated version carries a `## Changes in this version` section, auto-written — never
edit it. It lists, section by section, what changed since the last published version: new claims
added, claims superseded (kept, not deleted, with the reason), and claims retracted to
`Not modeled:` because their evidence no longer supports them. If the Brain wasn't refreshed the
night this version was built, the Changes block says so too, so a stale-looking answer is never
silently indistinguishable from an up-to-date one.

A superseded claim is never deleted — you'll see the old text prefixed
`**Superseded (<date>):**` immediately followed by the claim that replaced it, both cited. That
history stays in the document; nothing that was once asserted just disappears.

## Editing the docx in Word — what survives, what's refused

You can edit the published docx directly (in Word, or anything that round-trips through it), and
the next scheduled run reads your edits back as the new starting point:

- **Corrections** — fixing a sentence's wording — are kept: the next run treats your version as
  the human-authored claim, and it is never rewritten, re-cited, or turned into `Not modeled:` by
  a later automated redraft, even if the underlying evidence later disagrees (Scribe assumes
  you're correcting the source, not the other way around).
- **Added paragraphs** you write are picked up as new human-authored claims the same way.
- **Deletions** — removing a claim entirely — are respected: it's gone, and later runs won't
  silently re-add it.
- **Renamed or restructured headings are refused, not silently accepted.** Scribe's sections are
  identified by their heading, so renaming a section heading, deleting one, or adding a heading
  that isn't a recognized section heading (a new `H3`, a second title, text typed above the first
  section) makes the *next* automated run fail outright rather than guess what you meant — the
  published document is left exactly as you left it, and the failure names the offending
  heading(s) so you know what to undo before the next scheduled run can proceed.
- **Diagrams are not editable.** A mermaid diagram section is regenerated from its source data
  each time it's stale; hand-edits to the rendered diagram image don't survive a redraft.

In short: edit the prose freely, don't touch the section headings, and don't expect a diagram
edit to stick.

## Propose / review — when a document doesn't publish itself automatically

A task can be configured `publish: auto` (every due run publishes straight to the stable docx) or
`publish: propose` (every due run stages a candidate version instead, and nothing changes until a
human approves it). For a `propose` task, run `/scribe:review`:

1. It lists every pending proposal — task, version, and a diff of the proposed Markdown against
   what's currently published.
2. For each one, you approve, reject (with a one-line reason), or skip. Nothing is applied
   without an explicit answer for that specific task.
3. **Approve** publishes it through the same versioned path an automated run would use.
   **Reject** discards the staged proposal; nothing changes, and the reason is recorded.

If a proposal is marked stale (a newer version was published in between, e.g. because the same
task was also run manually), approving it is refused — ask for the task to be re-run instead so
a fresh proposal is built against the current state.

## "What depends on this file?"

If a raw file or a Brain document changes and you want to know which published documents cite it
before touching anything:

```
scribe.py index --query "<raw-relative path, or a Brain document id>"
```

It returns every task/version/section/claim that currently cites that source — including
transitively through another task's `[TASK:]` citation of it — so you can see the blast radius of
a source change before the next scheduled run redrafts anything.
