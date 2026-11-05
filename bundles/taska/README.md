# taska

A manager's **Epic → Story → Task** tree, kept current from the evidence a project already produces: meeting
transcripts and recordings (video or audio), calendar changes and, later, files and pull requests. Built for work
that senior people never move in a tracker: every change in the tree cites the words that caused it, every claimed
change is checked by a second agent, and what went quiet becomes a ranked list of questions to ask.

| Skill | What it does |
|---|---|
| `/taska:onboard` | Writes `taska.toml` with the user: plan → epics, people (client or internal), sources; checks each source; first backfill. |
| `/taska:run` | Syncs sources, transcribes recordings without a transcript, extracts changes per evidence item, reads each meeting a second time for decisions and the work the first read skipped, adds dependencies, verifies, refreshes the workbench. Four kinds of subagent: extraction, second read, dependencies, verification. `--due` for unattended nightly runs. |
| `/taska:workbench` | Local, editable workbench: tree/board, calendar playback, acceptance criteria with quotes, dependencies, asks. Edits pin fields. `--share` exports read-only HTML. |
| `/taska:prep` | A cited Markdown pack for the next status meeting. |

The weekly-report engine, as `taska.py` commands a project's own scripts or schedules call (every value a client
would recognise comes from `[report]` and `[[schedules]]` in `taska.toml`):

| Command | What it does |
|---|---|
| `report select --week W --out F` / `report apply --prose P --sel F` / `report build --week W --prose P --out X.docx` | What goes in the weekly report, decided from the tree; the writer's text kept only for open slots; the checked docx. The decisions table lists every decision of the week (the highlights stay capped). `build` prints `withheld` beside `failed`: sentences left out because they are, or cite, a people matter. They are not errors, and the count stays in the check report and the gates, never in the docx. |
| `gate WEEK [--docx F]` | The pre-send gate: `presend.json` and `presend.html` under `reports/WEEK/`; exit 1 when a blocking gate fails. |
| `deliver WEEK --to ADDR [--schedule NAME]` | `deliver.json` and a ready-to-send `mail.md` (subject, To, Cc, body, the attachments the schedule includes). |
| `schedules [--set rows.json] [--register] [--force] [--agents-dir D]` | The project's `[[schedules]]` with each one's next run and what is registered with the OS. `--set` replaces them from a JSON list (saved to `taska.toml`, previous file kept as `taska.toml.bak`) and registers; `--register` writes one job per schedule with the OS scheduler — a launchd agent on a Mac, a hidden Task Scheduler task on Windows that runs once at wake when its time was missed — each running `taska run-schedule <name>`. One machine runs a project (`.taska/runner.json`); `--force` takes the schedules over from a machine that is gone. A schedule mails the week's report to its To and Cc, at `when = "Mon 14:00 Europe/Kyiv"` (days, time and an IANA zone, converted to the machine's clock when registered). A `send` to only the owner is the review copy; a `send` to anyone else sends that copy unchanged, with the owner in cc, or is HELD to the owner when the tree changed since; keep it `on = false` until you trust it. The Schedules view lists every job that runs the project on this Mac, reconciled with launchd: active, not registered, not loaded, off, a job left behind by a deleted schedule, or a job outside `taska.toml` (which can be copied into a schedule or retired: unloaded and moved to `.taska/retired-jobs/`, never deleted). Delete removes a schedule and its job together; Run now first shows what the run will do (`GET /api/schedules/preview`: week, command, steps from `[run].describe`, To, Cc, attachments). Schedules are operations, not content: the workbench does not show them. A manager manages them by asking Claude (`/taska:onboard` uses `schedules`, `--set`, `--delete NAME`, `--retire LABEL`, `--preview NAME`); `http://127.0.0.1:<port>/schedules` is the same as a page for a control panel to frame (`?theme=dark|light`): each job as a card (state, what it does, who gets it, when it fires in this machine's time), **Run now** with the preview first. |
| `run-schedule NAME [--week W] [--dry-run]` | One scheduled run: the project's `[[run.steps]]` for the schedule's kind (`{week} {dir} {project} {kind} {name} {python} {taska}` expanded), then for a preview or send the read-only workbench copy, the docx (when `[report]` has a template and `reports/WEEK/prose.json` exists), the gate and the delivery. A send whose required step or gate failed is **HELD** to the owner (`[report].owner`); the owner stays in Cc of a send. A send to anyone but the owner runs no step: it sends the owner's review copy of the week unchanged, or is HELD. |
| `serve [--port 8766] [--agents-dir D]` | The workbench at `/` (content: Recap, Tree, Board, Deliverables) and the schedules page at `/schedules`; `--agents-dir` is where that page registers jobs (default `~/Library/LaunchAgents`). |
| `run-log begin\|step\|finish\|failed WEEK_DIR` | A run's step record, written so an interrupted run never erases the last finished one. |
| `render-evidence` | Rewrite every evidence `.md` from its `.json` in the current, line-limited format. |
| `doctor` | Tools, sources, portability and handover checks, and `reads`: per read the same gap counts as `pending`'s `gaps`, `ok` when none is waiting or outside the window; `deprecated` names `[run].decisions_since` when it is still set. |
| `pending` | Rows in time order, each with `need`: `extract` (not yet processed) or `decisions` (processed, not yet given its second read), and for `decisions` a `why`: `extracted` (just extracted), `backfill` (an older meeting missing the read, inside the window; at most `[run].backfill_max` per run, oldest first) or `requested` (`backfill`). `gaps` counts, per read, the meetings missing it: `in_window`, `waiting` (held by the cap), `outside_window` (only counted, never pending by themselves) and the `oldest` date. |
| `backfill --read decisions (--since YYYY-MM-DD \| --days N \| --evidence ID…) [--dry-run]` | Asks for a read on processed meetings whatever the window and cap: one `backfill` event; they are then pending with `why: requested` until their read passes. Prints `requested` (the ids asked for) and `skipped` (id → why: `not processed`, `not a meeting`, `read at version N`, `already requested`); `--days` counts back from the newest processed item; `--dry-run` lists them and writes nothing. |
| `leftovers --evidence ID` | Per third of a meeting, the sentences that sound like work (will, needs to, by Friday, agreed, a risk) and that no applied quote covers, with a token for the current state; and under `decisions`, every sentence with a decision or risk cue (decided, go with, locked in, rather than, the rationale, my concern, a bug, depends on) that no applied quote covers; these are hints only, shown beside how many decisions are already recorded in each third (a recorded risk accounts for none). |
| `done --evidence ID [--ack WHY --quote WORDS…] [--leftovers TOKEN] [--no-decisions WORDS…]` | Marks an item processed. A meeting is then pending its second read (`pending` lists it with `need: decisions`); when that pass's `done` succeeds, the `processed` event records `reads: {"decisions": 1}` (the read's name and version; an older `decisions_read` counts as version 1, and a reprocess clears both); `done` on that pass checks only that each third has a live agent decision quoted from it, or a `--no-decisions` quote of 8+ contiguous words from that third (repeatable; the script finds the third), else `status: decisions` lists the thirds. On extraction, a coverage refusal is acknowledged only with a quote of 8+ words the script finds in each acknowledged part (the refusal says at which `ts` each part starts); a long meeting with leftovers needs the token `leftovers` printed after the last apply — proof the list was produced then, not that each sentence was decided (the trajectory shows whether an apply followed). |
| `evals/items_csv.py --store S --key K --meta M --out items.csv` | One row per key item and bucket, plus each node a run created in key meetings: the quote, the expected item, what the run produced, the verdict (hit, miss, invented) and why, the plain bucket name, and how the key was built (READER.md, JUDGE.md, with their hashes) and the run graded (code). |
| `evals/trajectory.py SESSION.jsonl [--evidence-dir D] [--out J] [--html H]` | What a headless run did, from its Claude Code session file and subagent files: per agent turns, tools, errors, tokens; per evidence lines read, proposals, `apply` answers, `done` refusals and acknowledgements without a re-read. |
| `ai [--probe] -- <claude args>` | Reach Claude for an unattended run: a stored token, then CodeMie, then Bedrock if allowed. |

## Requirements
- Python 3.11+ (standard library only; nothing to install). `taska doctor` names the install command (`brew` / `winget`) for any tool that is missing.
- Optional, reported by `taska.py doctor`: `ffmpeg` + `whisper-cli` and a ggml model for recordings that came
  without a transcript (a model cached by brain's video lane is found automatically); `node` + the user's
  `msgraph` skill for Teams transcripts and calendar (found where Claude Code or CodeMie installs it,
  e.g. `~/.codemie/claude-plugin/skills/msgraph/scripts/msgraph.js`; `[sources.<key>].cli` or `$MSGRAPH_CLI`
  override).
- Evals only: the `claude` CLI on PATH for the planted end-to-end run (`evals/planted/run.sh`).
- Unattended runs (`taska schedules --register`, the onboarding skill, or the `/schedules` page; a Mac or Windows) reach Claude through `taska ai`: a token in `~/.config/taska/claude` (from
  `claude setup-token`, chmod 600), else CodeMie while signed in; Bedrock only with `TASKA_ALLOW_BEDROCK=1`.
  `taska ai --probe` says which works.

## Model, in one paragraph
Everything is an append-only event in `<project>/.taska/events.jsonl`; the tree is a fold. Sources become
normalised **evidence items** (`.taska/evidence/<id>.json` + `.md`), deduplicated across sources (the same meeting
from a synced folder and from Teams is one item). Agents propose `create/status/revise/ac_met/link/waiting/
suggest_*` with a verbatim quote; the script rejects anything whose quote is not in the item, that breaks a level
rule (a story needs 2–6 acceptance criteria, a task needs a story and an owner/date/type), or that touches a node
before it existed. Status changes, criteria met, dependencies and waits apply only after an independent verifier
says `supports`. Humans edit in the workbench; a field they set is pinned. Staleness, asks and the pack are
computed, never judged.

## Decisions and people matters
Each meeting is read twice: extraction, then a second read of its own for decisions and the work extraction left
out (`pending` lists it with `need: decisions` until that pass's `done` succeeds; `leftovers` shows it, per third,
the nodes already recorded from the meeting, and `apply` answers a non-verbatim quote with its `closest` span).
Each read has a name and a version (`taska_lib/reads.py`), and a processed meeting records the reads it has had. A
meeting missing a current read is listed by itself when it falls within `[run].backfill_days` (default 14) of the
newest processed item, at most `[run].backfill_max` (default 5, 0 turns it off) per run, oldest first; older ones are
only counted (`pending`'s `gaps`, `doctor`'s `reads`) until `taska backfill` asks for them. `[run].decisions_since` is
deprecated: while it is set it alone decides (meetings on or after it are `requested`, window backfill is off) and
`doctor` says to remove it. Under `--due`, `[run].max_items` (default 10) counts
decision rows and extraction rows together, so a newly extracted meeting costs two of them.

People matters are never tracked or reported. This covers staffing, allocation, extensions, roll-offs, rates,
overtime, utilisation and a person's performance. `sensitive.people_matter` is the one check. A person is a name or
alias from `[[people]]` or a pronoun. The ledger refuses an agent `create`, `revise` or `propose` that is a people
matter (`why: people_matter`). The weekly report, the deck data (`pack --json`) and the shared workbench export leave
out the same nodes. The report also withholds any sentence that is a people matter, and the report and the export
blank any such quote. The owner's live workbench still shows everything.

See `docs/taska-guide.md` for setup and daily use.
