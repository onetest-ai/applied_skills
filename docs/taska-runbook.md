# taska runbook: hand a project over

> **Purpose:** move a running taska project from one owner to another without losing its history.
> **When to use:** the owner changes, or the project moves to another machine.
> **Who:** the `<old-owner>` (sections 2 and 7) and the `<new-owner>` (sections 3 to 6).
> **Time needed:** about 1 hour in total.
>
> Last verified: 2026-11-03 · taska 0.6.0

## 0. About this runbook

A taska project keeps its Epic → Story → Task tree current from its meetings, calendar and files.
This runbook moves the project from one person's machine to another's.

| Role | Who | Does |
|---|---|---|
| `<old-owner>` | the person who runs the project today | §2: stops their jobs, packs the project, shares it |
| `<new-owner>` | the person who runs it from now on | §3 to §6: installs, takes over, runs it |

**Three rules.**

1. **One runner at a time.** From switch-over day only the `<new-owner>`'s machine runs the project. Two machines
   running one project do the work twice and write over each other's records.
2. **Handed over, never rebuilt.** The `<new-owner>` continues the same records. Nothing is read again or redone,
   and nothing is deleted.
3. **Nothing is sent without its checks.** A report goes out only after its checks pass; a check that fails holds
   it back.

**How to read a step.** Each step is one action, then the command to type (if any), then `Expected:` (what you
see when it worked) and `If not:` (what to do when it did not). Values in angle brackets are yours to fill in.
Never copy a job name or a web address from this page: the steps tell you which command prints it.

## 1. Before you start

**What the handover moves.** One zip file holds `taska.toml` (the project's settings) and the `.taska` folder
(the tree, every meeting it has read, and where each source stopped reading). A note beside the zip lists what
it holds.

**What it does not move.** Each owner sets these up on their own machine:

- sign-ins: Claude Code (or your provider), the `msgraph` skill for Teams and calendar, any tokens or mail passwords;
- scheduled jobs (the jobs your Mac runs at set times);
- local source folders that existed only on the `<old-owner>`'s machine. Their meetings are already in the
  project; only new files there would be missed;
- the other files in `<project-dir>`: scripts and report templates the project's own commands use, `reports/` and
  `logs/`. If a schedule runs its own command, the `<old-owner>` copies what it needs separately (§2 step 8) and
  the `<new-owner>` puts it back into `<project-dir>` (§3 step 6).

**The `taska` command.** Commands below start with `taska`. Make that word work in your terminal once per
terminal window (or add the line to `~/.zshrc`):

```bash
alias taska='python3 <plugin>/skills/run/taska.py --project <project-dir>'
```

`<plugin>` is the folder Claude Code installed taska into: the `installPath` of the taska entry printed by
`claude plugin list --json`. `<project-dir>` is the folder that holds `taska.toml`.

**Prerequisites.** These steps are for macOS.

| Who | Need | Check | Ready when |
|---|---|---|---|
| both | Python 3.11 or newer | `python3 --version` | `Python 3.11` or higher |
| both | taska installed and enabled | `claude plugin list` | taska is listed with `Status: ✔ enabled` |
| `<old-owner>` | a cloud folder (OneDrive, SharePoint …) you can share: `<shared-folder>` | open it in Finder | you can create a folder in it |
| `<new-owner>` | access to `<shared-folder>`, synced to your Mac | open it in Finder | you see the files the `<old-owner>` put there |
| `<new-owner>` | Claude Code signed in | start `claude` | it answers |
| `<new-owner>` | the `msgraph` skill signed in, only if the note's Sources line lists a source whose adapter (in brackets, e.g. `(msgraph_transcripts)`) starts with `msgraph` or `teams` | `node ~/.claude/skills/msgraph/scripts/msgraph.js status` | `Logged in as: <new-owner>@example.com` |

## 2. Old owner: freeze and pack

Do this on switch-over day, after your last edit in the workbench: anything changed after step 6 is not in the
package.

1. List the jobs that run this project.

   ```bash
   taska schedules
   ```

   Expected: JSON. Under `"os"` there is one row per job, each with a `"label"`, a `"loaded"` and a `"path"`.
   Note every `"label"` whose `"loaded"` is `true` or whose `"path"` is not `null`. If there is none, go to
   step 6.

   If not: `command not found: taska` means the `taska` command is not set up; see §1, then repeat this step.

2. Stop each job you noted as `"loaded": true`, one command per label, with the label exactly as printed.

   ```bash
   launchctl bootout gui/$(id -u)/<label>
   ```

   Expected: no output.

   If not: an error that says there is no such process means that job was already stopped; go on.

3. Check that the jobs are stopped. Do this before step 4: once a job's file is moved, `taska schedules` shows
   `"loaded": false` for it whether it still runs or not.

   ```bash
   taska schedules
   ```

   Expected: every row you noted shows `"loaded": false`, and its `"path"` is still shown.

   If not: if a row still shows `"loaded": true`, go to §8.

4. Move each noted job's file out of the way, so a restart of your Mac does not start the job again. Use the
   `"path"` printed for that label in step 3.

   ```bash
   mkdir -p ~/taska-stopped-jobs && mv "<path>" ~/taska-stopped-jobs/
   ```

   Expected: no output, and the file is now in `~/taska-stopped-jobs`.

   If not: `No such file or directory` means the path was copied wrongly; copy it again from step 3.

5. Check that nothing is left for this project.

   ```bash
   taska schedules
   ```

   Expected: every row under `"os"` shows `"loaded": false` and `"path": null`.

   If not: for a row that still shows a path, go to step 4.

6. Pack the project into the shared folder.

   ```bash
   taska handover --out <shared-folder>/handover
   ```

   Expected: JSON with `"status": "ok"`, the `"zip"` and `"note"` it wrote, and `"counts"` (evidence, meetings,
   events, nodes, pending_cards). Your project is not changed.

   If not: if Terminal says `Operation not permitted`, macOS does not let it write to the cloud folder. Run
   `taska handover --out ~/taska-handover` instead and copy both files into `<shared-folder>/handover`
   with Finder.

7. Open the note (the file ending `-handover.md`) and compare it with the workbench.

   Expected: the number of tree items and of cards waiting for a Confirm match what the workbench shows, and
   the Sources line lists every source you use.

   If not: if the note has a section "Paths to check", change each listed line in `<project-dir>/taska.toml` as
   it says (a path written as `~/...`), then go to step 6. If the counts differ, reload the workbench; if they
   still differ, a run was still writing: go to step 1.

8. Check whether the project runs its own commands, which the zip does not carry.

   ```bash
   taska schedules
   ```

   Expected: `"command"` is `null`, and every schedule's `"runs"` ends with `run-schedule "<name>"`, and
   `taska.toml` has no `[[run.steps]]`. Then there is nothing more to copy.

   If not: copy every file or folder those commands name (a scripts folder, a report template) with Finder into
   `<shared-folder>/handover/extras`, and tell the `<new-owner>`.

9. Share `<shared-folder>` with `<new-owner>@example.com` with edit rights, and tell them the zip's file name.

   Expected: the `<new-owner>` sees the zip in their synced copy of the folder.

   If not: check the sharing settings of the cloud folder, and that the sync app runs on both machines.

**Check before you hand over:**

- [ ] `taska schedules` shows `"loaded": false` and `"path": null` on every `"os"` row.
- [ ] The zip and the note are in `<shared-folder>/handover`.
- [ ] The note's counts match the workbench.
- [ ] Files the project's own commands use are in `<shared-folder>/handover/extras`, or there are none.
- [ ] The `<new-owner>` can open the shared folder.
- [ ] From now on you run nothing on this project: no `/taska:run`, no edits in the workbench.

## 3. New owner: install and take over

1. Install taska as described in [the taska guide](taska-guide.md#install), then check it.

   ```bash
   claude plugin list
   ```

   Expected: taska is listed with its version and `Status: ✔ enabled`.

   If not: run the install again and restart Claude Code. If `python3 --version` shows a version below 3.11, see §8.

2. Set up the `taska` command (§1), with `<project-dir>` set to the folder you will unpack into.

   ```bash
   taska -h
   ```

   Expected: a usage text that starts with `usage: taska.py`.

   If not: `No such file or directory` means `<plugin>` is wrong; copy the `installPath` again.

3. Copy the zip from your synced `<shared-folder>/handover` to your Downloads folder with Finder.

   Expected: the zip is in Downloads. Terminal cannot always read a synced cloud folder (`Operation not
   permitted`); Finder can.

   If not: if the folder or the zip is missing, the sharing or the sync has not finished; ask the `<old-owner>`.

4. Check that `<project-dir>` holds no taska project yet.

   ```bash
   ls -a <project-dir>
   ```

   Expected: `No such file or directory`, or a folder without `.taska` and `taska.toml`.

   If not: stop. Never unpack over an existing project and never delete it. Move the folder aside first, then
   repeat this step:

   ```bash
   mv <project-dir> <project-dir>-before-<date>
   ```

5. Unpack the zip into `<project-dir>`.

   ```bash
   mkdir -p <project-dir> && unzip ~/Downloads/<zip> -d <project-dir> && ls -a <project-dir>
   ```

   Expected: the list shows `.taska` and `taska.toml`.

   If not: if `unzip` cannot find the file, check the zip's name with `ls ~/Downloads/*.zip` and repeat this
   step.

6. If the `<old-owner>` said there are extra files, copy everything in `<shared-folder>/handover/extras` into
   `<project-dir>` with Finder, keeping the folder names.

   Expected: each file the `<old-owner>` named is in `<project-dir>`. If there is no `extras` folder, there is
   nothing to copy; go on.

   If not: if `extras` is missing although they said there are extra files, the sync has not finished; ask the
   `<old-owner>`.

7. Check the project before anything runs.

   ```bash
   taska doctor
   ```

   Expected: JSON. Read it with the table below. If any source shows `"would_reread": true`, stop: do not run
   `/taska:run`; go to §8, row "doctor says would_reread: true".

   If not: an error naming `tomllib` means your Python is too old; see §8.

   | Field | Normal | When it differs |
   |---|---|---|
   | `project` | the project's name | wrong project: check `<project-dir>` in the `taska` command (step 2) |
   | `handover_check` → each source → `would_reread` | `false` | `true`: stop, do not run `/taska:run`; §8, row "doctor says would_reread: true" |
   | `portability` | `[]` | each line names a path tied to the old owner's home folder; change it in `taska.toml` as the line says |
   | `sources` → each → `available` | `true` | a folder source: if the folder exists on your Mac under another path, change that source's `path` in `taska.toml` (write it as `~/...`). If it existed only on the old machine, this is normal: its items are already in the project |
   | `sources` → each → `signed_in_as` (Teams and calendar sources) | your address | missing: sign in to the `msgraph` skill, then repeat this step |
   | `sources` → each → `reason` or `note` | absent | says what is missing (a tool, a sign-in); fix it or accept it |
   | `install` | `{}` | the install command for each missing tool (needed only for recordings without a transcript) |
   | `status` | `ok` | `degraded` lists the sources in `unavailable`: normal only for the old machine's local folders |

8. In Claude Code, in `<project-dir>`, run the first update.

   ```text
   /taska:run
   ```

   Expected: it ends with a summary: what changed, what waits for a Confirm, and meetings that have no
   transcript. Every meeting the summary lists is dated after the handover date (the date in the zip's name).

   If not: if it lists a meeting dated on or before the handover date, stop it (Esc) and go to §8.

9. Open the workbench.

   ```text
   /taska:workbench
   ```

   Expected: it prints a local web address. Open it: the tree shows the project's epics and stories, as many
   items as the note's tree items (plus any the first run added).

   If not: if the page says it "runs older code than is installed", an old workbench server still holds its
   address. Quit the terminal or Claude session that started it, or find and stop it by the port in the printed
   address, then run `/taska:workbench` again. If the message stays, go to §8.

   ```bash
   lsof -nP -iTCP:<port> -sTCP:LISTEN
   kill <pid>
   ```

10. Open **To review** and decide each card: read **Why** and the quote, then click **Confirm**, or **Reject** and
   pick a reason.

   Expected: each decided card leaves the list. A task is closed only when a person confirms it.

   If not: if the page says it is a read-only copy, you opened an exported file, not the live workbench: go to
   step 9.

11. Tell the `<old-owner>` that you have taken over, and wait until they confirm that their jobs are stopped
    (§2, step 5). Then set up your own schedules in §4. The package still names the old machine as the project's
    runner, so the first registration on your machine is refused (for up to 7 days) unless you add `--force`:

    ```bash
    taska schedules --register --force
    ```

    Use `--force` only after that confirmation, and only this first time.

    Expected: the `<old-owner>` confirms that `taska schedules` shows no loaded job on their machine.

    If not: register nothing until they confirm (one runner at a time).

**Check after you take over:**

- [ ] `taska doctor`: no `"would_reread": true`, and `portability` is `[]`.
- [ ] Every source is `available`, or is a folder that existed only on the old machine.
- [ ] The first `/taska:run` read only new meetings.
- [ ] The workbench shows the tree, and To review holds only what you have not decided yet.
- [ ] The `<old-owner>` has confirmed their jobs are stopped.

## 4. Schedules

An *update* schedule never sends mail, even when To is filled in.

A schedule is a job your Mac runs for this project at set days and times. Each schedule is one of three kinds:

| What you pick on the page | What it does | Who gets mail |
|---|---|---|
| **Update the tree only (no mail)** | reads new meetings, calendar and files, and updates the tree | nobody, whatever To and Cc say |
| **A Friday draft** | builds a draft of the week's report | you (the owner), plus the schedule's Cc |
| **The weekly report** | To is only you: builds the report, checks it, and makes your *review copy*. To is anyone else: sends that review copy | see below |

A draft or weekly report run from Friday to Sunday reports on the current week; one run from Monday to Thursday
reports on the week before. An update always works on the current week.

**Review copy and real send.** The weekly report goes out in two schedules:

1. One addressed to you only (To = your address, the one in `[report].owner` of `taska.toml`; a send with an
   empty To is this one too). It builds the report, runs its checks and mails you the
   *review copy*, with the subject starting `Review copy:`. Read it before the real send fires.
2. One addressed to the reader. It builds nothing: it sends the review copy you were mailed that week, unchanged,
   with you always in Cc. If there is no review copy yet, if a file of it changed, or if the tree changed after
   it was made, the send is **HELD**: it goes to you only, with a subject starting `HELD:` and the reason.

A send whose checks fail is HELD the same way. `[report].owner` must be set: without it every weekly report is
HELD and no mail is written at all. To send after a HELD, run your review schedule again, read the new
copy, then run the reader's schedule.

**Where the mail is.** taska writes each mail into `<project-dir>/reports/<week>/`: `mail.md` (subject, To, Cc,
body, attachments) and `deliver.json`. Your project's mail step sends it. Without a mail step nothing leaves your
machine: open `mail.md` and send it yourself.

**A schedule with its own command** (a `command` line in the schedule, or `[run].scheduled` in `taska.toml`) does
only what that command does. If that command has no mail step, no mail is sent, whatever the kind says. The
preview below shows it: `"decided_by"` names the command instead of `null`.

**Set up a schedule safely.**

- Ask Claude in `<project-dir>` (`/taska:onboard`), or open the schedules page: the workbench address followed by
  `/schedules` (`http://127.0.0.1:<port>/schedules`).
- Keep a new schedule **Off: only when you press Run now** (`on = false`) until you trust it. It then runs only
  when you press **Run now**.
- **Save** writes the schedules into `taska.toml` and registers their jobs with your Mac. Nothing runs at Save.
- **Run now** first shows what the run will do: the week, the command, the steps, To, Cc and the attachments.
  Nothing runs until you confirm.
- The same preview in the terminal:

  ```bash
  taska schedules --preview "<name>"
  ```

  `"mail": null` means the run sends no mail. Otherwise `"mail"` lists `"to"`, `"cc"`, what is attached, and for
  a weekly report the `"held"` address.

**When does it run next?** `taska schedules` prints, per schedule, `"fires_local"` (the days and times the job
fires in this Mac's time), `"next"` (the next run; `null` while it is off) and `"what"` (who gets the mail). Under
`"os"`, `"state"` is `active` when the job is registered and loaded, and `"last_exit"` is `0` when the last run
ended well; it is `null` until the job has run once.

## 5. Routine

| When | Do |
|---|---|
| Every working day | Open the workbench (`/taska:workbench`), then **To review**: Confirm or Reject each card. |
| After a meeting without a transcript | Run `/taska:run`; its summary lists recordings it could not read yet. They are retried on the next run. |
| Before a status meeting | Run `/taska:prep` for a short pack, each line cited. |
| Each week, after your review copy arrives | Read it. If you change the tree afterwards, run the review schedule again (Run now) before the real send, or the send is HELD. |
| Each week | `taska schedules`: every schedule you use shows `"state": "active"` and `"last_exit": 0` (`null` until its first run). |
| When an unattended run fails to start | `taska ai --probe` says which sign-in works and how to fix the one that does not. |
| After any change to sources or your Mac | `taska doctor`: `"status": "ok"`, or `degraded` only for folders you know are gone. |

## 6. Verify the handover

**Day 1 (the `<new-owner>`, on switch-over day):**

- [ ] The `<old-owner>` has confirmed that `taska schedules` on their machine shows no loaded job.
- [ ] `taska doctor`: no `"would_reread": true`, `portability` is `[]`, every source you need is `available`.
- [ ] The first `/taska:run` read only meetings dated after the handover date.
- [ ] The workbench's tree items match the note's count (plus what the first run added).
- [ ] `taska schedules`: your schedules are registered, the reader's send is still `"on": false`.
- [ ] `taska schedules --preview "<name>"` for each schedule shows the To and Cc you expect.

**Week 1:**

- [ ] Each update schedule ran on its days: `"last_exit": 0` (no longer `null`), and new meetings appear in the tree.
- [ ] Your review copy arrived, with the subject starting `Review copy:`.
- [ ] You have decided every card in To review at least once.
- [ ] No meeting appears twice in the tree or in the Recap.
- [ ] Only then: switch the reader's send to **On** and Save.

## 7. Roll back

> **Purpose:** give the project back to the `<old-owner>` with everything the `<new-owner>` did on it.
> **When to use:** the `<new-owner>` cannot run the project, and the `<old-owner>` takes it back.
> **Who:** the `<new-owner>` (steps 1 and 2), then the `<old-owner>` (steps 3 to 7).
> **Time needed:** about 30 minutes.

One runner at a time still holds: the `<new-owner>` stops first.

1. `<new-owner>`: stop your jobs exactly as in §2, steps 1 to 5, on your machine, and stop using the project now:
   no `/taska:run`, no edits in the workbench.

   Expected: `taska schedules` shows `"loaded": false` and `"path": null` on every `"os"` row.

   If not: go to §8.

2. `<new-owner>`: pack the project. Always do this, even if you changed nothing by hand: your scheduled runs
   changed it too.

   ```bash
   taska handover --out <shared-folder>/rollback
   ```

   Expected: JSON with `"status": "ok"` and the `"zip"` it wrote. Tell the `<old-owner>` that your jobs are
   stopped and give them the zip's file name.

   If not: `Operation not permitted`: as in §2, step 6, with `rollback` in place of `handover`.

3. `<old-owner>`: with Finder, copy the newest zip in `<shared-folder>/rollback` (the date is in its name) to your
   Downloads folder.

   Expected: the zip is in Downloads and its name matches the one the `<new-owner>` gave you.

   If not: if the folder or the zip is missing, the sync has not finished; ask the `<new-owner>`.

4. `<old-owner>`: move your old project records aside (never delete them). Only `.taska` and `taska.toml` move:
   `reports/`, `logs/` and any scripts stay where they are.

   ```bash
   mkdir <project-dir>-before-<date> && mv <project-dir>/.taska <project-dir>/taska.toml <project-dir>-before-<date>/
   ```

   Expected: no output; `ls -a <project-dir>` no longer shows `.taska` or `taska.toml`.

   If not: if `mkdir` says the folder exists, pick another `<date>` suffix.

5. `<old-owner>`: unpack the zip into `<project-dir>`.

   ```bash
   unzip ~/Downloads/<zip> -d <project-dir> && ls -a <project-dir>
   ```

   Expected: the list shows `.taska` and `taska.toml` again, next to your `reports/` and `logs/`.

   If not: check the zip's name with `ls ~/Downloads/*.zip` and repeat this step.

6. `<old-owner>`: check the project before anything runs.

   ```bash
   taska doctor
   ```

   Expected: no source shows `"would_reread": true`, `portability` is `[]`, and every source you use shows
   `"available": true`.

   If not: `would_reread`: §8, row "doctor says would_reread: true". `portability` or `available`: fix
   `taska.toml` as in the doctor table of §3, step 7.

7. `<old-owner>`: take the schedules back, only after the `<new-owner>` confirmed in step 2 that their jobs are
   stopped. The package names their machine as the runner, so `--force` is needed.

   ```bash
   taska schedules --register --force
   ```

   Expected: JSON with `"registered"`: each schedule shows `"action": "loaded"` (or `"unloaded"` when it is off).
   Under `"os"` each schedule that is on shows `"state": "active"`. The job files you moved to
   `~/taska-stopped-jobs` in §2 are not needed; leave them there.

   If not: an error saying another machine "registered these schedules" means `--force` was left out. If both
   machines ran meanwhile, see §8, row "two machines ran one project".

**Check after the roll back:**

- [ ] On the `<new-owner>`'s machine, `taska schedules` shows `"loaded": false` and `"path": null` on every `"os"` row.
- [ ] Your earlier `.taska` and `taska.toml` are kept in `<project-dir>-before-<date>`.
- [ ] `taska doctor`: no `"would_reread": true`, `portability` is `[]`.
- [ ] `taska schedules` on your machine: every schedule that is on shows `"state": "active"`.
- [ ] The workbench shows the decisions the `<new-owner>` made.
- [ ] The `<new-owner>` runs nothing on the project from now on.

## 8. Troubleshooting

| You see | Cause | Do |
|---|---|---|
| `python3 --version` below 3.11, or an error naming `tomllib` | taska needs Python 3.11 or newer; macOS ships an older one | Install Python 3.11 or newer (the official installer or Homebrew), open a new terminal, check `python3 --version`. |
| `"loaded": true` after `launchctl bootout` | The label typed differs from the job's (a typo, or copied from elsewhere), so the real job was not stopped | Copy the label again from `taska schedules` and repeat §2 step 2. Still `true`: move the job's file (§2 step 4), restart the Mac, check again. |
| A stop command names a job that does not exist (`No such process`, yet the job still runs) | Labels change when a project or schedule is renamed; the label came from a document or an old setup | Never copy a label from a page or a chat. Look it up with `taska schedules` and use it exactly as printed. |
| **doctor says would_reread: true** for a source | The package has that source's items but no record of where it stopped reading (`.taska/cursors.json`), often because the source was renamed in `taska.toml` | Do not run `/taska:run`. Ask the `<old-owner>` to run `/taska:run` once and pack again (§2 step 6). If the old machine is gone, run anyway: items are recognised, so the work repeats but nothing is doubled. |
| The first `/taska:run` lists a meeting dated on or before the handover date | The same as the row above, or the `<old-owner>` ran after packing | Stop it (Esc). Run `taska doctor`. Ask the `<old-owner>` for a fresh package after they stop all runs. |
| `Operation not permitted` on a synced cloud folder | macOS does not let Terminal read or write that folder | Copy the files with Finder (§2 step 6, §3 step 3). |
| Files in `<shared-folder>` are missing or hours old | The sync app is not running or is paused | Open the sync app and wait until it says it is up to date. A run reports the folder as unavailable until then. |
| The workbench says it "runs older code than is installed", and it stays | An old workbench server still holds the address | Stop it by its port (§3 step 9), then run `/taska:workbench` again. |
| `signed_in_as` is missing, or the msgraph skill says `NOT_LOGGED_IN` or `TOKEN_EXPIRED` | The Microsoft 365 sign-in expired or was never made on this machine | `node ~/.claude/skills/msgraph/scripts/msgraph.js login`, then `taska doctor` again. |
| A scheduled run fails at once; `taska ai --probe` is not `ok` | The sign-in that unattended runs use expired | Do what `taska ai --probe` names (see §9 for the token). |
| **No email arrived** | Something in this order: | 1. Did the job fire? `taska schedules`: `"state"` and `"last_exit"`; the log in `<project-dir>/logs/`, the file named after the schedule. 2. Is it an update schedule? It never mails. 3. Is To empty? A weekly report with an empty To is your review copy: it goes to you only. 4. Does the schedule have its own command (`"decided_by"` in `taska schedules --preview "<name>"`)? It mails only if that command has a mail step. 5. Does the project have a mail step at all? taska only writes `reports/<week>/mail.md` and `deliver.json`; without a mail step nothing sends them: send `mail.md` yourself, or add a mail step to the schedule's command. 6. Read `<project-dir>/reports/<week>/run-status.json` and `mail.md`: a HELD went to you instead. |
| The log shows the mail step ended with exit code 137 | The step was killed (out of memory or time) | Read the log, then press **Run now** on the schedule. |
| A send arrives with a subject starting `HELD:` | No review copy this week, a file of it changed, the tree changed after it, or a check failed | The subject says which. Run the review schedule again, read the copy, then run the send. |
| `"state"` is `not_loaded`, `not_registered` or `outdated` | The job file is missing or not loaded, or a time-zone change moved its local time | Press **Save** on the schedules page, or `taska schedules --register`. |
| `"state"` is `off`, `stale`, `foreign` or `unreadable` | `off`: the schedule is switched off and runs only with Run now. `stale`: a job left by a deleted schedule. `foreign`: a job made outside `taska.toml` that runs this project. `unreadable`: its job file is damaged, so what it runs is unknown | `off`: switch it on and Save when you trust it. `stale` or `foreign`: Retire it on the schedules page, or `taska schedules --retire <label>` (its file moves to `.taska/retired-jobs/`). `unreadable`: note its `"path"` and ask for help. Never delete a job file. |
| `--register` says another machine "registered these schedules" | The package names the old machine as the runner (for 7 days) | Only after the `<old-owner>` confirmed their jobs are stopped: `taska schedules --register --force`. |
| `crontab` cannot be changed on macOS | macOS blocks crontab edits | Use the jobs taska registers (`taska schedules --register`); never crontab. |
| A workbench or other service stops when a Claude session ends, or behaves differently from a normal terminal | It was started from inside that session and inherited its settings | Start it from a normal terminal (`env -i HOME=$HOME zsh -lic '<command>'`) or as a scheduled job. |
| The mail server refuses a password | It requires a modern sign-in (OAuth2), not a password | Use the mail route your admin provides; a mail password is never the fix. |
| Two machines ran one project | Jobs were not stopped on one machine | Stop one machine's jobs (§2 steps 1 to 5). Keep the project whose records are newer; ask before merging anything. Never delete either copy. |

## 9. What never goes in a handover

Each owner signs in themselves, on their own machine. None of these is in `taska.toml`, the package, the shared
folder or a chat:

- the Claude Code (or provider) sign-in;
- the `msgraph` skill's sign-in (Teams, calendar);
- the token for unattended runs: made with `claude setup-token`, stored in `~/.config/taska/claude`;
- mail credentials, if your mail step needs any.

A stored token or credential file lives under `~/.config/taska/` and only you may read it: run this after adding
any file there.

```bash
chmod 600 ~/.config/taska/*
ls -l ~/.config/taska/
```

Expected: each file shows `-rw-------`. If you find a token or a password in `taska.toml`, the shared folder or a
message, take it out and make a new one: treat the old one as seen by others.
