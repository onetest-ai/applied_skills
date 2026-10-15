---
name: workbench
description: Use when the manager wants to see or edit the project's work tree (`/taska:workbench`) or share a read-only copy — opens the local workbench (Tree/Board views, a scrolling calendar to replay the tree meeting by meeting, acceptance criteria with evidence quotes, dependencies, Asks by person) where their edits are recorded and pinned so later evidence never overwrites them.
argument-hint: "[--share]"
---

# taska:workbench

**How to run the script.** `$TASKA` means `python3 <skill-dir>/../run/taska.py --project <project-dir>`
(`<skill-dir>` holds this `SKILL.md`; `<project-dir>` holds `taska.toml`). One command per Bash call.

- **Edit (default).** Start `$TASKA serve --port 8766` in the background (it runs until stopped) and give the user
  the URL it prints. Tell them what they can do there: change title, status, owner, client owner, due date or
  parent; tick, add or reword acceptance criteria; add or remove dependencies; record who a task is waiting on;
  add tasks; merge or delete; accept or reject the agent's suggestions; undo any of their own edits from an
  item's history. Every edit is an event marked as theirs and pins that field.
- **Share (`--share`).** `$TASKA export` writes a self-contained read-only HTML file; give the user its path. It
  contains the project's evidence quotes, so it is for people who may see them.
