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

## Requirements
- Python 3.11+ (standard library only; nothing to install).
- Optional, reported by `taska.py doctor`: `ffmpeg` + `whisper-cli` and a ggml model for recordings that came
  without a transcript (a model cached by brain's video lane is found automatically); `node` + the user's
  `msgraph` skill for Teams transcripts and calendar (found where Claude Code or CodeMie installs it,
  e.g. `~/.codemie/claude-plugin/skills/msgraph/scripts/msgraph.js`; `[sources.<key>].cli` or `$MSGRAPH_CLI`
  override).
- Evals only: the `claude` CLI on PATH for the planted end-to-end run (`evals/planted/run.sh`).
- Unattended runs (`schedule`) reach Claude through `taska ai`: a token in `~/.config/taska/claude` (from
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
