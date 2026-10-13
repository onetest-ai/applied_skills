---
name: prep
description: Use when the manager prepares for a status meeting or wants a written update (`/taska:prep`) — writes a short Markdown pack from the work tree, every line cited to its evidence (schedule and scope signals, stories that moved, sessions held and coming up, what the client owes, the ranked asks by person, decisions, risks, items to confirm or close).
argument-hint: "[--days 7]"
---

# taska:prep

**How to run the script.** `$TASKA` means `python3 <skill-dir>/../run/taska.py --project <project-dir>`
(`<skill-dir>` holds this `SKILL.md`; `<project-dir>` holds `taska.toml`). One command per Bash call.

1. If `$TASKA pending` shows unprocessed evidence, offer to run `/taska:run` first — a pack built on a stale tree
   misses this week's changes.
2. `$TASKA pack --days <N>` (default 7) writes the pack and prints its path and word count.
3. Read the pack. Then tell the user, in three or four lines, what deserves their attention most (the slips and
   the asks that hold other work up), and give the path. Do not rewrite the pack: its lines are computed and cited;
   the colour of the status (green/amber/red) is the manager's call, not yours.
