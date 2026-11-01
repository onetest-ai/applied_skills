# taska

A manager's **Epic → Story → Task** tree, kept current from the evidence a project already produces: meeting
transcripts and recordings (video or audio), calendar changes and, later, files and pull requests. Built for work
that senior people never move in a tracker: every change in the tree cites the words that caused it, every claimed
change is checked by a second agent, and what went quiet becomes a ranked list of questions to ask.

| Skill | What it does |
|---|---|
| `/taska:onboard` | Writes `taska.toml` with the user: plan → epics, people (client or internal), sources; checks each source; first backfill. |
| `/taska:run` | Syncs sources, transcribes recordings without a transcript, extracts changes per evidence item, adds dependencies, verifies, refreshes the workbench. `--due` for unattended nightly runs. |
| `/taska:workbench` | Local, editable workbench: tree/board, calendar playback, acceptance criteria with quotes, dependencies, asks. Edits pin fields. `--share` exports read-only HTML. |
| `/taska:prep` | A cited Markdown pack for the next status meeting. |

The weekly-report engine, as `taska.py` commands a project's own scripts or schedules call (every value a client
would recognise comes from `[report]` and `[[schedules]]` in `taska.toml`):

| Command | What it does |
|---|---|
| `report select --week W --out F` / `report apply --prose P --sel F` / `report build --week W --prose P --out X.docx` | What goes in the weekly report, decided from the tree; the writer's text kept only for open slots; the checked docx. |
| `gate WEEK [--docx F]` | The pre-send gate: `presend.json` and `presend.html` under `reports/WEEK/`; exit 1 when a blocking gate fails. |
| `deliver WEEK --to ADDR [--schedule NAME]` | `deliver.json` and a ready-to-send `mail.md` (subject, To, Cc, body, the attachments the schedule includes). |
| `schedules [--set rows.json] [--register] [--force] [--agents-dir D]` | The project's `[[schedules]]` with each one's next run and what is registered with the OS. `--set` replaces them from a JSON list (saved to `taska.toml`, previous file kept as `taska.toml.bak`) and registers; `--register` writes one job per schedule with the OS scheduler — a launchd agent on a Mac, a hidden Task Scheduler task on Windows that runs once at wake when its time was missed — each running `taska run-schedule <name>`. One machine runs a project (`.taska/runner.json`); `--force` takes the schedules over from a machine that is gone. A `send` with `review = true` is a review copy to the owner on schedule and goes to the reader only on **Send now** (`run-schedule NAME --send`). The workbench's **Schedules** view does the same from the page: each job as a card (what it does, who gets it, when it fires in this machine's time, whether it is registered), **Run now** running the registered command, **Send now** on a reviewed send. |
| `run-schedule NAME [--week W] [--dry-run] [--send]` | One scheduled run: the project's `[[run.steps]]` for the schedule's kind (`{week} {dir} {project} {kind} {name} {python} {taska}` expanded), then for a preview or send the read-only workbench copy, the docx (when `[report]` has a template and `reports/WEEK/prose.json` exists), the gate and the delivery. A send whose required step or gate failed is **HELD** to the owner (`[report].owner`); the owner stays in Cc of a send. A send with `review = true` goes to the owner only, its subject `[Review: would go to …]`, until `--send`; `[run].scheduled` receives that flag as `{flags}`. |
| `serve [--port 8766] [--agents-dir D]` | The workbench; `--agents-dir` is where its Schedules view registers jobs (default `~/Library/LaunchAgents`). |
| `run-log begin\|step\|finish\|failed WEEK_DIR` | A run's step record, written so an interrupted run never erases the last finished one. |
| `render-evidence` | Rewrite every evidence `.md` from its `.json` in the current, line-limited format. |
| `leftovers --evidence ID` | Per third of a meeting, the sentences that sound like work (will, needs to, by Friday, agreed, a risk) and that no applied quote covers, with a token for the current state. |
| `done --evidence ID [--ack WHY --quote WORDS…] [--leftovers TOKEN]` | Marks an item processed. A coverage refusal is acknowledged only with a quote of 8+ words the script finds in each acknowledged part (the refusal says at which `ts` each part starts); a long meeting with leftovers needs the token `leftovers` printed after the last apply — proof the list was produced then, not that each sentence was decided (the trajectory shows whether an apply followed). |
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
- Unattended runs (the **Schedules** view, or `taska schedules --register`; a Mac or Windows) reach Claude through `taska ai`: a token in `~/.config/taska/claude` (from
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

See `docs/taska-guide.md` for setup and daily use.
