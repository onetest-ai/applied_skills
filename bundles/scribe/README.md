# Scribe — living documents over a Brain

Scribe turns a Brain (RAG + taxonomy graph + deterministic marts) and a synced raw-evidence
folder into **scheduled, versioned, cited living documents**: docx/pdf deliverables that redraft
only the sections whose evidence actually changed, keep every claim traceable to its source down
to a claim id, refuse to guess where the evidence doesn't support a sentence, and hand a human
edits back the next time the document is regenerated. It is the authoring half of the
`applied-ai` marketplace — `brain` builds the knowledge store, `kb` answers ad hoc questions
against it, and Scribe is the third leg: documents that keep themselves current instead of being
written once and going stale.

## Install

Scribe needs the **brain plugin** installed alongside it (it imports `parse_corpus`, `chunking`
and `semantic_core` from the Brain bundle at run time) plus three system tools: `pandoc`
(docx/pdf rendering), `soffice`/LibreOffice (legacy `.doc`/`.ppt` raw evidence), and `node`/`npx`
(mermaid-cli, for diagram sections). `scribe.py doctor` checks all three plus the Brain the
scripts read and never installs anything itself.

```bash
claude plugin marketplace add onetest-ai/applied_skills
claude plugin install brain@applied-ai
claude plugin install scribe@applied-ai

# from a checkout: an isolated venv for the scribe scripts' Python deps
./install.sh --bundle scribe --deps
```

`--deps` builds `<project>/.claude/venv` (or `~/.claude/venv` with `--user`) with everything in
`bundles/scribe/requirements.txt` — a self-contained superset of `bundles/brain/requirements.txt`
(the two must not diverge; `tests/test_packaging.py::test_requirements_cover_brain_requirements`
checks it). Zero-install alternative: `uv run --with-requirements bundles/scribe/requirements.txt
python <script>`.

## Quick start

In a Claude Code session, from a project directory that already has `scribe.toml` (see below)
and a reachable Brain:

```
/scribe:onboard              # walk through adding one new document (a "task")
/scribe:run --due            # draft/render/publish every task that's due
/scribe:review               # approve or reject anything waiting in propose mode
/scribe:tasks                # list tasks, check status, enable/disable, promote to a template
```

`promote <task> --as <template-id>` refuses to overwrite an existing template id unless you pass
`--force` (every instance still pinned to the old version keeps working; the template file
itself resets to version 1). Only prose fields (`goal`, `inputs`, and each section's
`title`/`intent`/`queries`/`must`) get `{{param}}` substituted for the instance's param values —
every id (`id`, section `id`s), `kind`, `lanes` and `acceptance` is copied byte-for-byte.

`/scribe:onboard` is interactive — it asks one question at a time (purpose, starting point,
scope, evidence, output, acceptance, operations), shows a per-section Brain/raw evidence
coverage table before drafting anything, and always trial-runs the new task in propose mode
before it can be enabled. `/scribe:run --due` is what a nightly/headless schedule invokes; it
prepares every due task, drafts only the sections whose evidence went stale, verifies every
claim (`check-file` for raw quotes, `check-task` for upstream claims, the `kb:verifier` subagent
for Brain-cited claims), then merges, renders, publishes and records lineage.

## `scribe.toml` reference

One `scribe.toml` per Scribe project, next to `tasks/` and `out/`:

```toml
[project]
brain_db      = "../brain-project/knowledge.sqlite"   # required — the store the scripts read directly
brain_catalog = "../brain-project/schema/metrics.json"                 # required
brain_skills  = "/path/to/applied_skills/bundles/brain/skills"         # required — brain bundle's skills dir (parse_corpus.py, chunking.py)
brain_mcp_dir = "/path/to/applied_skills/mcp/brain"                    # required — mcp/brain checkout (semantic_core.py)
out_root      = "out"          # default "out" — published docx/pdf + lineage + run reports
tasks_dir     = "tasks"        # default "tasks" — one *.task.md per document
templates_dir = "/path/to/applied_skills/bundles/scribe/skills/run/templates"  # required — the LIBRARY templates dir
raw_root      = "raw-replay"   # default "raw-replay" — the synced folder of raw evidence
work_dir      = "work"         # default "work" — scratch space, cleared per task per run
brain_handoff_dir = "../brain-project/ops/handoff"   # optional — hand-off mode reports (A1); must be the
                                                      # SAME dated-report dir the Brain's `handoff --out` writes to

[run]
top_k              = 8    # default 8  — evidence items fetched per section query
max_tasks_per_run  = 20   # default 20 — `plan --due` caps how many tasks one run drafts
budget_minutes     = 90   # default 90 — a task past this minute budget is deferred, not dropped
default_task_minutes = 10 # default 10 — estimate used until a task has recorded run minutes

[groups.discovery]
enabled = true
tasks   = ["domain-profile--*", "subsystem-profile--*"]   # fnmatch globs against task id
```

`brain_db`, `brain_catalog`, `brain_skills`, `brain_mcp_dir` and `templates_dir` are **required** —
`scribe.toml` fails to load without them. `templates_dir` is the **library** templates dir (ship
with the plugin, or wherever your checkout keeps `bundles/scribe/skills/run/templates`); a
project's own `<project>/templates/` is picked up automatically whenever that directory exists,
*in addition to* `templates_dir` — you never point `templates_dir` at the project dir itself, or
the library templates (`domain-profile`, `subsystem-profile`, `raid`, …) disappear.
`out_root`/`tasks_dir`/`raw_root`/`work_dir` default as shown. All paths resolve relative to the
project dir unless absolute. `SCRIBE_BRAIN_DB`/`SCRIBE_NOW` environment variables override
`brain_db`/today's date, mainly for tests. A group's `tasks` list is `fnmatch` globs matched
against task ids — including fan-out children, whose ids are `<parent>--<taxonomy-node-id>`
(never `[node]`: brackets break `[TASK:...]` citation tags and glob matching alike). **A new
fan-out child never runs on its own** — it expands `enabled: false` and is listed under
`plan`/`list`'s `new_fanout_children` until `scribe.py --project . enable <child id>` records the
approval on its parent task's `approved_children:`.

## Artifacts tree

```
out/
  <task.out>/
    <title>.docx, <title>.pdf     # stable — always the latest published version
    _versions/vNNN.docx, vNNN.pdf # every past rendered version
    _src/
      vNNN.md                     # published Markdown for that version (the drafting base)
      vNNN.lineage.json           # what this version cited/consumed (written by `lineage`)
      vNNN.sources.json
      state.json                  # per-task build state — see CLAUDE.md's state.json invariant
      .pending/                   # journal for an in-flight/crashed publish (auto mode only)
    _pending/                     # propose-mode staging: vNNN.{md,docx,pdf,diff.md} awaiting `/scribe:review`
  _runs/<date>.json                # today's run report — one row per task processed
  _lineage/index.json              # `scribe.py index` — "what depends on file X" answers
work/
  <task>/
    pack/                          # `prepare`'s per-section drafting packs
    sections/                      # the agent's drafted section bodies + evidence.json
    raw/                           # this run's raw-replay parse (manifest.json, raw.sqlite)
    render/                        # this run's docx/pdf render.json
```

## Scheduling

```bash
scribe.py --project . schedule --time 02:00                          # a crontab line
scribe.py --project . schedule --time 02:00 --launchd                 # a launchd plist (macOS)
scribe.py --project . schedule --brain-project <brain project dir>    # chain a Brain hand-off first
```

`schedule` only **prints** text — it never touches `crontab`/`launchctl` itself; installing what
it prints is the operator's decision. The chained command it prints is
`claude -p "<slash command>" --permission-mode bypassPermissions`: an unattended run cannot stop
to ask for tool permission at 2am, so headless invocations run with permissions bypassed — this
is safe specifically because `/scribe:run` and `/brain:brain-maintenance handoff` are both
designed to run unattended already (never asking the user a question; recording a failure and
moving on instead). With `--brain-project`, the Brain's own hand-off classification
(`claude -p "/brain:brain-maintenance handoff"`) runs first so Scribe drafts against a store that
was just given the chance to catch up; Scribe's own stale-Brain preflight
(`doctor`'s `brain_handoff` block) is what lets it still make progress against the last good
Brain if the hand-off half aborted. Both commands run with a minimal PATH (no shell profile is
sourced under cron/launchd), so `schedule` captures the PATH of the shell it was run from and
bakes it into what it prints, and points stdout/stderr at a log file under `<project>/logs/` so a
silent unattended failure still leaves evidence.

## Troubleshooting

- **Locked docx** (Word has the file open). `publish` reports `"locked: <path>"` and leaves the
  publish journal in place for a retry on the next run — nothing under `out/` is touched.
- **Offline synced folder.** `doctor`'s `raw_root.available: false` means the raw-replay folder
  isn't reachable right now. `prepare` still runs — every `[FILE:]` claim is carried forward
  unchanged with a note (never treated as "checked and found nothing") — but no new raw evidence
  is considered until the folder is back online.
- **Hidden raw files are ignored.** `select_raw_files` and `raw_root_status` skip any
  dot-file/dot-directory, macOS AppleDouble `._x` sidecar, Office `~$` lock file, and OS-hidden
  file under `raw_root` — the same rule the Brain applies to source roots — so a `.DS_Store`
  left in a synced folder is never selected as evidence, and a folder that holds only such files
  after previously holding real ones still reads as `"empty"`, not `"ok"`.
- **Stale Brain.** `doctor`'s `brain_handoff` block reports whether the last hand-off run
  applied, deferred or aborted; a run against a stale Brain still proceeds and the published
  Changes block says so, rather than blocking the whole document on an unrelated Brain-side
  failure.
- **`needs_quote`.** A carried `[FILE:]` claim with no recorded evidence to verify it against
  (never published, or published under a different section). `check-file.json.needs_quote` lists
  it; `accept`'s `zero_unverified` check fails the task until a quote is supplied or the claim is
  re-cited/marked `Not modeled:`.
- **`unsupported_structure` / `section_heading_changed`.** A human edited the published docx in a
  way the scripts refuse to guess about — added a heading that isn't a section heading, typed
  text above the first section, or renamed/deleted a section heading. `prepare` fails with the
  offending headings/detail listed; the published version stays untouched until the docx is
  fixed.
- **`guard-brain` printed a `manual` line instead of editing `brain.toml`.** It only auto-edits an
  `exclude` list written as its own `[sources.roots.<key>]` table; a root declared as a
  single-line inline table (`<key> = { ... }`) can't be safely rewritten by a script, so
  `guard-brain` returns a hint line to paste in yourself for that root rather than guess at TOML
  formatting. Do it, then re-run `guard-brain` to confirm.

See `docs/scribe-guide.md` (repo root) for the document-owner's guide — what survives editing the
docx in Word, reading the Changes block, and the propose/review flow.
