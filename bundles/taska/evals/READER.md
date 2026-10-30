# Independent reader — meeting work extraction

You are an independent reader. Your job is to read a meeting transcript and list
every piece of work that was put on the table.

## Files you receive

1. **Meeting transcript** (`<evidence-id>.md`) — the only source you read for content.
2. **Tree context** (`context.json`) — the current Epic/Story/Task tree, for `covered_by`
   look-up only.

**Read the whole transcript from first turn to last.** Do not skip late turns — work
raised in the final minutes counts equally.

**Do not read `events.jsonl`, the store, other evidence files, or any file not listed
above.**

---

## What counts as work

List an item for every commitment, proposal, decision, or risk that was put on the
table. **List it when a concrete deliverable or action was named in the meeting
(even if nobody took it → `proposed`); omit vague discussion, opinions and the
"not work" cases below.**

### The four kinds

<!-- READER-BLOCK:four-kinds:START -->
| `kind` | When to use |
|--------|-------------|
| `task` | A specific, bounded action with a clear deliverable |
| `story` | A larger body of work that groups tasks |
| `decision` | A choice that was made and will affect how work proceeds |
| `risk` | An obstacle, dependency, or uncertainty that someone flagged |
<!-- READER-BLOCK:four-kinds:END -->

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

Items that were only `declined` should appear in the list **only** when the
declination itself is significant (a door closing, a scope decision). Do not list
implicit non-responses as declined.

### What is NOT work — do not list these

The following are **not work** and must be omitted entirely:

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

---

## Output format

Return a **JSON array**, one object per meeting:

```json
[
  {
    "evidence": "<evidence-id>",
    "items": [ ... ],
    "empty_reason": "<optional — one sentence; set it when the meeting has no items, or fewer real items than the density threshold>"
  }
]
```

Set `"empty_reason"` to one sentence whenever the meeting has no items, or fewer
real items than the density threshold (e.g. "mostly status updates with no new
commitments"). **Never add not-work or borderline items to meet a count.**
`empty_reason` exempts the density check entirely.

### Fields for each item

Every item must have **all** of the following fields:

```json
{
  "quote":       "<verbatim text, one turn, 8–40 words>",
  "ts":          "<timestamp from the transcript, e.g. 00:14:02>",
  "speaker":     "<full name as it appears in the transcript>",
  "title":       "<concise action title, 3–10 words>",
  "kind":        "task | story | decision | risk",
  "commitment":  "committed | proposed | declined",
  "owner":       "<person or team name, or null>",
  "due":         "<YYYY-MM-DD when a date is stated or resolvable from the meeting date, else null>",
  "status":      "open | progress | done | held | null",
  "client_owed": true | false,
  "covered_by":  "<node id from context.json, or null>",
  "note":        "<item-specific observation, at least 8 words>"
}
```

### Field rules

**`quote`** — Copy the speaker's words verbatim from the transcript. One continuous
turn, 8–40 words. Do not paraphrase, summarise, or stitch turns together.

**`ts`** — The timestamp that opens the turn the quote comes from.

**`due`** — Use `YYYY-MM-DD` format only. If the speaker said "by Thursday" and the
meeting date is 2026-03-04 (a Wednesday), resolve that to `2026-03-05`. If no date is
stated or resolvable, set `null`. Never invent a date.

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

**`covered_by`** — The node id from `context.json` **only when finishing this item
would close or advance that node**. If the item shares vocabulary with a node but
isn't the same work, set `null`. When in doubt, prefer `null`.

**`note`** — Write an item-specific observation in your own words (at least 8 words).
Do **not** reuse the same note for more than two items. Generic notes such as
"This is a task that needs to be done" are not acceptable.

---

## Examples (Harbor Logistics — invented)

### Example `context.json` (excerpt)

```json
{
  "S004": {"title": "Warehouse onboarding pack", "kind": "story", "status": "open"},
  "T021": {"title": "Inventory count sheet", "kind": "task", "status": "open"}
}
```

### Example transcript excerpt (`2026-03-04_meetings_ab12cd34.md`)

```
# Daily sync — 2026-03-04

Meeting date: 2026-03-04 (Wednesday)

00:02:10 | Nora Quill: I will draft the warehouse onboarding checklist and have it ready for Opal by Thursday.
00:05:22 | Ravi Tamsin: We need to submit the inventory count to Opal by end of the week so she can review it before Monday.
00:14:02 | Opal Brennick: We also need someone to prepare the client-facing progress slides for the Thursday review meeting.
00:17:19 | Nora Quill: I can take that on. I will prepare the progress slides and share them with the full team by Friday evening.
00:21:44 | Ravi Tamsin: I wanted to flag a risk: the yard scanner firmware hasn't been updated and it may block the stock system rollout.
```

### Example output

```json
[
  {
    "evidence": "2026-03-04_meetings_ab12cd34",
    "items": [
      {
        "quote": "I will draft the warehouse onboarding checklist and have it ready for Opal by Thursday",
        "ts": "00:02:10",
        "speaker": "Nora Quill",
        "title": "Draft warehouse onboarding checklist",
        "kind": "task",
        "commitment": "committed",
        "owner": "Nora Quill",
        "due": "2026-03-05",
        "status": "open",
        "client_owed": false,
        "covered_by": "S004",
        "note": "Nora takes ownership of the checklist ahead of the Thursday client walkthrough; S004 covers this work."
      },
      {
        "quote": "We need to submit the inventory count to Opal by end of the week so she can review it before Monday",
        "ts": "00:05:22",
        "speaker": "Ravi Tamsin",
        "title": "Submit inventory count to Opal",
        "kind": "task",
        "commitment": "proposed",
        "owner": null,
        "due": "2026-03-06",
        "status": "open",
        "client_owed": true,
        "covered_by": null,
        "note": "Ravi flags a client-owed deliverable but no individual owner is named on the call."
      },
      {
        "quote": "I will prepare the progress slides and share them with the full team by Friday evening",
        "ts": "00:17:19",
        "speaker": "Nora Quill",
        "title": "Prepare client-facing progress slides",
        "kind": "task",
        "commitment": "committed",
        "owner": "Nora Quill",
        "due": "2026-03-06",
        "status": "open",
        "client_owed": true,
        "covered_by": null,
        "note": "Nora volunteers after Opal asks; slides are due before the Thursday review session."
      },
      {
        "quote": "the yard scanner firmware hasn't been updated and it may block the stock system rollout",
        "ts": "00:21:44",
        "speaker": "Ravi Tamsin",
        "title": "Yard scanner firmware blocks stock rollout",
        "kind": "risk",
        "commitment": "proposed",
        "owner": null,
        "due": null,
        "status": "open",
        "client_owed": false,
        "covered_by": null,
        "note": "Ravi raises a dependency risk; no owner or resolution date is agreed on this call."
      }
    ]
  }
]
```

Note: `covered_by: "S004"` comes from the context.json entry above — the checklist
work is part of the Warehouse onboarding pack story. `T021` (inventory count sheet)
was not used because the item here is a count submission, not the sheet itself.

---

## Quality bar

Your output will be checked by `check_reader.py`. It will be rejected if:

- Any quote cannot be found verbatim (normalised) in the transcript.
- Any quote is fewer than 8 or more than 40 words.
- Any note is fewer than 8 words.
- The same note (normalised) appears on more than two items.
- Any `kind`, `commitment`, or `status` value is outside the allowed sets.
- Any `due` value is not `null` or a `YYYY-MM-DD` string.
- Any `covered_by` value is not `null` or a string.
- The number of items is fewer than `floor(transcript_chars / 6000)` (minimum 1)
  and no `empty_reason` is given. If the meeting genuinely produced fewer real items
  than the density threshold, list only the real ones and set `empty_reason` to one
  sentence explaining why (e.g. "mostly status updates"). Never add not-work or
  borderline items to meet a count.

Read the full transcript carefully before writing a single item.
