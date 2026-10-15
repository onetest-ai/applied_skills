# Taska guide

Taska keeps a delivery manager's work tree — epics, stories with acceptance criteria, tasks — up to date from the
project's meetings, so nothing that was said and then dropped goes unnoticed.

## Running `taska.py`
The `taska.py …` commands below are the plugin's script. Claude runs them for you from the slash commands; to run one
yourself use `python3 <plugin-dir>/skills/run/taska.py --project <project-dir> <command>` (every command prints
JSON). `<plugin-dir>` is where Claude Code installed the plugin (`taska@applied-ai`).

## Install
```bash
/plugin marketplace add onetest-ai/applied_skills
/plugin install taska@applied-ai
```
Python 3.11+ is all it needs. For recordings without a transcript: `brew install ffmpeg whisper-cpp` and a ggml
model (any `ggml-*.bin` under `~/.cache/taska/whisper/` or brain's `~/.cache/brain/whisper/`). For Teams transcripts
and calendar: the `msgraph` skill, signed in — taska looks for its CLI at `~/.claude/skills/msgraph/scripts/msgraph.js`;
set `cli = "..."` on the source or `$MSGRAPH_CLI` if yours lives elsewhere.

## Set up a project
Run `/taska:onboard` in the directory you want taska's files in. It asks, one question at a time:
1. **The project** — name and timezone → `taska.toml`.
2. **The plan** — a timeline with activities and dates (`plan.json`, `epics.csv`, `epics.md`), a list of functional
   epics, or nothing (add epics in the workbench).
3. **Sources**
   - `meetings_folder`: a synced SharePoint/OneDrive transcripts folder or a recordings folder. Transcripts
     (`.vtt .srt .docx .txt`) are read directly; recordings (`.mp4 .mov .m4a .mp3 .wav …`) without one are
     transcribed. `client_patterns` marks client meetings by title.
   - `msgraph_transcripts`: Teams transcripts pulled directly, by meeting subject.
   - `msgraph_calendar`: calendar events by subject (`subjects`, `exclude_subjects` for the recurring call whose
     transcript already says it all) — scheduled, moved, cancelled, ended. Needs an msgraph whose `calendar`
     command accepts `--start/--end` for a past range; the first `sync` reports `unsupported` if not (`doctor`
     only checks that you are signed in).
   - `files_folder`: a synced SharePoint/OneDrive library. Each new or changed document becomes evidence with a
     text excerpt (.docx .pptx .xlsx .md .txt .csv, .pdf with `pdftotext`). For bulk drop folders (a client's
     inputs) set `digest = true`: one item per folder per day listing what arrived.
   - `code_host`: pull/merge requests and issues/work items via `gh` (GitHub), `glab` (GitLab) or `az` (Azure
     DevOps) — whichever you are signed in to. Evidence only when an item appears or changes state. Code hosts
     reachable only through an MCP server: the agent fetches and calls `taska.py ingest`.
4. **People** — everyone seen in the transcripts, with their side (internal / client) and every alias Teams uses
   ("Ada Example1"). Owners, "the client owes" and the asks depend on it.

## Recordings without a transcript
Taska first asks Teams (through msgraph) for the meeting's own transcript — matched by title and start time — which
has real speaker names and no recognition errors. Only when there is none does it transcribe locally: `sync` lists them; `taska.py transcribe --limit N` transcribes N at a time with whisper.cpp (minutes per hour of
audio; `/taska:run` starts it in the background). Speakers are not separated, so such transcripts read as timed
paragraphs. A local transcript that is one line repeated (a known whisper failure) is rejected, never ingested, and
re-transcribed; a Teams or sidecar transcript like that is left pending and reported, not replaced. If a transcript arrives days later, it is processed in its own place in time.

`taska.py redo --evidence <id>` re-queues an item; `--drop` also forgets it so its source re-reads it (folder
sources: the next sync, or `transcribe` for a recording). An item from Teams, the calendar or a code host is not
re-read by `--drop`; its source remembers it as seen.

## Daily use
- `/taska:run` after new meetings (or schedule it: `taska.py schedule` prints a cron/launchd entry for
  `/taska:run --due`, which handles at most `[run].max_items` items, default 10). Long recordings are transcribed
  in the background and picked up by the next run. An unattended `claude -p` cannot answer permission prompts:
  allow the Bash and subagent calls it needs in the project's Claude Code settings, and watch the first
  scheduled log before trusting it.
- `/taska:workbench` to look and edit: step through meetings on the calendar strip and watch stories change; fix a
  title, a date, an owner; tick a criterion; link a dependency; accept or reject the agent's merge suggestions.
  Your edits are pinned — later meetings add evidence but never overwrite them — and each can be undone.
- `/taska:prep` before the status meeting: schedule and scope signals, stories that moved, sessions, what the
  client owes, the top asks by person, decisions, risks, and what to confirm or close.

## What gets asked
Computed from the tree: overdue work, sessions that should have happened, dates that keep moving, things waiting
on a person, client-owed items gone quiet, and work that waits on something stale (asked once, at the root). The
prep list holds the top 15, at most two per story; quiet internal items older than a week go to *confirm or
close* instead — they usually finished without anyone saying so.

## What it keeps, and who reads it
`.taska/` holds verbatim meeting transcripts, calendar details and document excerpts from your project — client
conversations included. Keep it out of version control (`.taska/` in `.gitignore`) and out of shared folders.
The extraction and verification agents read that text, so whatever the model provider sees for your Claude Code
sessions sees these excerpts too. Treat pull-request text, shared documents and calendar invites from outside your
team as untrusted: an item can only change the tree by quoting itself, but anyone who can write one can write that
quote — the verifier checks that the words support the claim, not that the author is entitled to make it.

## Files
```
taska.toml                 project config (yours to edit)
.taska/events.jsonl        the ledger (append-only; never edit)
.taska/evidence/           normalised meetings, events, files (json + md)
.taska/media/              transcripts made from recordings
.taska/out/workbench.html  read-only export; pack-<date>.md
```
