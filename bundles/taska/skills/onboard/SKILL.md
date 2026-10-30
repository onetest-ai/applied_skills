---
name: onboard
description: Use when a manager wants to start tracking a project's work with taska, or to add a source, a person or a plan to an existing taska project — interviews one question at a time, writes taska.toml (plan, people with client/internal side, meeting folders, Teams transcripts and calendar through the msgraph skill, file folders), checks every source, sets up the epics and backfills the first evidence.
argument-hint: "[project-dir]"
---

# taska:onboard

You, the agent, set up taska for one project with the user, one question at a time, and finish with a working
first run. Everything project-specific goes into `taska.toml` in the project directory; nothing goes into the
plugin.

**How to run the script.** `$TASKA` means `python3 <skill-dir>/../run/taska.py --project <project-dir>`, where
`<skill-dir>` is the directory holding this `SKILL.md` (`taska.py` lives in the sibling `run` directory) and
`<project-dir>` is the directory the user picks (default: the current one). One command per Bash call; every
command prints JSON.

## Steps
1. **Project.** Ask for the project's name and the directory to keep taska's files in. Run
   `$TASKA init --name "<name>" --timezone <IANA zone>`. It writes a commented `taska.toml`; edit that file for
   every later step (keep its comments).
2. **Plan → epics.** Ask how the work is structured:
   - a delivery plan with activities and dates (a timeline slide, a spreadsheet, a list): get it as a file and
     convert it to `plan.json` (`{"activities": [{"name", "start", "end", "phase"}]}`), `epics.csv`
     (`name,start,end`) or `epics.md` (`- Name (2026-09-07..2026-09-27)`), then set `[plan].file`;
   - functional epics for a product team: `[plan].epics = [...]`;
   - nothing yet: leave `[plan]` empty; epics are added in the workbench.
3. **Sources**, one at a time; after each, run `$TASKA doctor` and fix what it reports before the next.
   - **Meetings folder** (synced SharePoint/OneDrive "Call Transcripts", a Teams recordings folder): path,
     include globs, `client_patterns` (title words that mark a client meeting), `since`. Transcripts (.vtt .srt
     .docx .txt) are read directly; recordings (video or audio) without one are transcribed with whisper.cpp —
     `doctor` shows whether `whisper-cli` and a ggml model were found; if not, say how to install them
     (`brew install whisper-cpp ffmpeg` on macOS and a ggml model file under `~/.cache/taska/whisper/`).
   - **Teams transcripts** (`msgraph_transcripts`) and **calendar** (`msgraph_calendar`) go through the user's
     `msgraph` skill, which owns sign-in. If `doctor` says it is not signed in, tell the user to run the login
     command it prints, prefixed with `!`, and wait. Use `subject` (transcripts) and `subjects` (calendar) to keep
     to this project's meetings. When titles are not unique to the project (a generic word such as "Performance",
     or two series sharing one title), choose meetings by who attends instead: `attendee_domains = ["client.example"]`
     and/or `organizer = ["<name>"]`, and set `client_domains` so a meeting with a client attendee is labelled a
     client meeting. Ask the user which client domain and organizer mark this project's meetings. A calendar source reported `unsupported` needs an msgraph that lists a past date
     range; say so plainly.
   - **Files folder** (`files_folder`): a synced SharePoint/OneDrive library of deliverables. New and changed
     documents become evidence with a text excerpt (.docx .pptx .xlsx .md .txt .csv; .pdf when `pdftotext` is
     installed). Exclude the transcripts folder and anything that is not a deliverable, and set `since` so a large
     library does not flood the first run.
   - **Code host** (`code_host`): pull/merge requests and issues/work items through the CLI the user is already
     signed in to — `provider = "github"` (`gh`), `"gitlab"` (`glab`) or `"azure"` (`az` with the azure-devops
     extension; also `organization` and `project`). A merged PR is evidence that the task it implements is done.
     If the user's code host is reachable only through an MCP server, fetch items with it and hand them to
     `$TASKA ingest --file <items.json> --source <name>` (a JSON array of `{kind, at, title, text, url}`).
   - Recordings in a meetings folder: when the project also has an msgraph source, taska first fetches the Teams
     transcript of the meeting (real speaker names) and transcribes locally only when Teams has none.
4. **People.** Run `$TASKA sync`, then `$TASKA people`: every speaker not yet in `[[people]]`, with turns and how
   many of their meetings were client meetings (`likely_side`). Propose a `[[people]]` entry per person — canonical name,
   `side` (internal | client | external), and `aliases` for every label variant seen (Teams adds digits:
   "Ada Example1") — and let the user correct sides. Owners, "client owes" and the Asks all depend on it.
5. `$TASKA setup` (creates the epics once), then `$TASKA pending`. Ask how far back to backfill (default: the last
   two weeks); for a long history start small: process the oldest ~5 items with `/taska:run`, show the user the
   tree in `/taska:workbench`, and adjust people or plan before the rest.
6. Offer unattended runs: `$TASKA schedule --time 18:30` (cron) or `--launchd` prints the entry; show it and let
   the user install it themselves.
