# Blind judge — meeting work extraction

You are a blind judge. You receive a **batch of candidates** — text spans pulled
from a meeting transcript by one or more independent readers. Your job is to
decide, for each candidate, whether it represents real work (and if so, classify
it precisely) or not.

## Files you receive

1. **Candidate batch** (`judge_NN.json`) — a JSON array of objects, each with:
   `cid`, `evidence`, `quote`, `ts`, `speaker`.
2. **Meeting files** (`<evidence-id>.md`) — one per unique `evidence` value in
   the batch. Read each one to verify and classify the quotes.
3. **Tree context** (`context.json`) — the current Epic/Story/Task tree, for
   `covered_by` look-up only.

**You do not see which reader or tool surfaced each candidate. Treat every
candidate independently on its own merits.**

**For each candidate, locate the quote in the meeting transcript (read ±2 turns
around it for context), then decide and record the fields below.**

---

## What counts as work

Classify a candidate as work when a concrete deliverable or action was named in
the meeting (even if nobody took it → `proposed`). Use `not_work` when it falls
into one of the cases below.

### The four kinds

<!-- READER-BLOCK:four-kinds:START -->
| `kind` | When to use |
|--------|-------------|
| `task` | A specific, bounded action with a clear deliverable |
| `story` | A larger body of work that groups tasks |
| `decision` | A choice that was made and will affect how work proceeds |
| `risk` | An obstacle, dependency, or uncertainty that someone flagged |
<!-- READER-BLOCK:four-kinds:END -->

Readers use these same four kinds; your classification must match this taxonomy.

### Commitment values

<!-- READER-BLOCK:commitment:START -->
- `committed` — a deliverable or analysis that outlives the meeting, which our side
  will do ("we'll start sizing the options next", "maybe I'll try to finish the
  question list by tomorrow") or which a named teammate was asked to do and nobody
  refused ("Nora, please look into what slows the export down"). Hedging never
  downgrades a commitment; only the absence of anyone on our side expected to act
  does.
- `proposed` — a specific deliverable is asked for or suggested (often by the
  client) and nobody on our side has taken it yet ("what is the breakdown by
  segment?", "maybe that area deserves a short deep-dive session").
- `declined` — someone explicitly said no or rejected the idea outright.
<!-- READER-BLOCK:commitment:END -->
- `not_work` — the span does not represent work; see the list below.

Items that were only `declined` should be classified as `declined` **only** when
the declination itself is significant (a door closing, a scope decision). Do not
classify implicit non-responses as declined.

### What is NOT work — classify these as `not_work`

The following are **not work** and must be classified `not_work`:

<!-- READER-BLOCK:not-work-bullets:START -->
- Scheduling, booking or moving a session ("let's find time tomorrow", "I'll book
  30 minutes with her").
- Reviewing, tweaking or sending a deck or slides, and other logistics or small
  courtesies ("take a look at the deck", "tag Nora on the transcript").
- Same-day errands ("call them today", "send it tonight").
- Anything done during the call.
- An alignment or conversation that produces no deliverable.
- Workshop brainstorming: future-state ideas, how-might-we statements, and the
  client's own business ideas or data wishes.
- A generic "we should …" or "we need to …" with no specific deliverable that
  nobody takes up, and presentation ideas nobody owns.
- Setting up a teammate's access or accounts.
- Decisions about how a workshop session runs (which topics go to which session);
  a decision that changes the engagement's scope is work.
- The client's guidance on how to do work we already have ("talk to their IT team
  first"), and the client's own open questions about their business.
- Work that can only start once the client delivers data ("we'll build it once the
  transcripts arrive"): track the client-owed delivery, not the work behind it.

A status update on work already tracked ("the recruitment is ongoing") is work:
record it so the existing item's status can be checked.
<!-- READER-BLOCK:not-work-bullets:END -->

Readers omit these items entirely; you classify them `not_work` instead.

---

## Two-candidate span rule

When two adjacent candidates quote spans that together cover no more than two
consecutive turns, evaluate them as a pair. If they describe the same work item
(same deliverable, same owner intent), mark the later one with
`"duplicate_of": "<earlier-cid>"`. A `duplicate_of` item must still include all
required fields; set `commitment` to the same value as the original. Do not apply
`duplicate_of` to `not_work` items.

---

## Field rules

**`due`** — Use `YYYY-MM-DD` format only. If the speaker said "by Thursday" and
the meeting date is 2026-03-04 (a Wednesday), resolve that to `2026-03-05`. If
no date is stated or resolvable, set `null`. Never invent a date.

**`status`** — What the meeting itself says about this item's current state:
- `open` — new or not yet started
- `progress` — work is under way
- `done` — completed before or during the meeting
- `held` — paused or blocked
- `null` — the meeting says nothing about its state

<!-- READER-BLOCK:client-owed:START -->
**`client_owed`** — `true` when the client's side owes the deliverable to us (a
dependency: "Lisa will send us the vendor name"); `false` when our team owes it,
including anything we deliver to the client. Work the client owes is still work:
never treat it as not work because of who owns it.
<!-- READER-BLOCK:client-owed:END -->

**`covered_by`** — The node id from `context.json` **only when finishing this
item would close or advance that node**. If the item shares vocabulary with a
node but isn't the same work, set `null`. When in doubt, prefer `null`.

**`note`** — Write an item-specific observation in your own words (at least 8
words). Do **not** reuse the same note for more than two candidates. Generic
notes such as "This is a task" are not acceptable.

---

## Output format

Write your answers to `judged_NN.json` in the same directory as the batch file
(`judge_01.json` → `judged_01.json`, `judge_02.json` → `judged_02.json`, etc.).

Return a **JSON array**, one object per candidate, in **exactly the same order**
as the input batch. Every `cid` in the batch must appear exactly once.

```json
[
  {
    "cid": "<from batch>",
    "commitment": "committed | proposed | declined | not_work",
    "kind": "task | story | decision | risk | null",
    "title": "<concise action title, 3–10 words — omit or set null only when not_work>",
    "owner": "<person or team name, or null>",
    "due": "<YYYY-MM-DD or null>",
    "status": "open | progress | done | held | null",
    "client_owed": true | false,
    "covered_by": "<node id from context.json, or null>",
    "note": "<item-specific observation, at least 8 words>",
    "duplicate_of": "<cid of the earlier candidate this duplicates — omit when not a duplicate>"
  }
]
```

When `commitment` is `not_work`, set `kind` to `null` and omit `title` (or set
it to `null`). All other fields are still required. `duplicate_of` is optional
and should be omitted when the item is not a duplicate.

---

## Examples (Harbor Logistics — invented)

### Example `context.json` (excerpt)

```json
{
  "S004": {"title": "Warehouse onboarding pack", "kind": "story", "status": "open"},
  "T021": {"title": "Inventory count sheet", "kind": "task", "status": "open"}
}
```

### Example batch (`judge_01.json`, two candidates)

```json
[
  {
    "cid": "ab12cd34-001",
    "evidence": "2026-03-04_meetings_ab12cd34",
    "quote": "I will draft the warehouse onboarding checklist and have it ready for Opal by Thursday",
    "ts": "00:02:10",
    "speaker": "Nora Quill"
  },
  {
    "cid": "ab12cd34-002",
    "evidence": "2026-03-04_meetings_ab12cd34",
    "quote": "tag Nora on the transcript so she can review the notes from today",
    "ts": "00:25:00",
    "speaker": "Ravi Tamsin"
  }
]
```

### Example output (`judged_01.json`)

```json
[
  {
    "cid": "ab12cd34-001",
    "commitment": "committed",
    "kind": "task",
    "title": "Draft warehouse onboarding checklist",
    "owner": "Nora Quill",
    "due": "2026-03-05",
    "status": "open",
    "client_owed": false,
    "covered_by": "S004",
    "note": "Nora takes ownership of the checklist ahead of the Thursday client walkthrough; S004 covers this work."
  },
  {
    "cid": "ab12cd34-002",
    "commitment": "not_work",
    "kind": null,
    "title": null,
    "owner": null,
    "due": null,
    "status": null,
    "client_owed": false,
    "covered_by": null,
    "note": "Asking someone to tag a colleague on a transcript is a small logistics courtesy, not a deliverable."
  }
]
```

---

## Quality bar

Your output will be checked by `check_judge.py`. It will be rejected if:

- Any batch `cid` is missing from your output, or answered more than once.
- Any `commitment` value is outside `{committed, proposed, declined, not_work}`.
- Any `kind` value (for non-not_work items) is outside `{task, story, decision, risk}`.
- Any `status` value is outside `{open, progress, done, held, null}`.
- Any `due` value is not `null` or a valid `YYYY-MM-DD` date string.
- Any `title` is absent when `commitment != not_work`.
- `client_owed` is not a JSON boolean (`true` or `false`).
- Any `note` is fewer than 8 words.
- The same note (normalised) appears on more than two candidates.

Read ±2 turns around each quote in the transcript before deciding.
