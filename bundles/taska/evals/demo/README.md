# Harbor demo project

An invented project ("Harbor": a yard team, a client contact, two planted meetings) for the runbook's screenshots and
walkthrough. It holds no real client, person or domain.

```bash
bash bundles/taska/evals/demo/build.sh /path/to/harbor    # the folder must not exist or be empty
python3 bundles/taska/skills/run/taska.py --project /path/to/harbor status
```

`build.sh` needs no network and no model. It writes the planted meetings of seeds 1 and 2 (`../planted/generate.py`)
into `calls/`, runs `init`, `setup` and `sync`, applies the ops in `ops.json` (every quote is verbatim from the
generated transcripts), and records a `supports` verdict for each. The result: 2 epics, 2 stories, 6 tasks, a risk,
2 proposed-work cards and a done-card waiting for Confirm.

The three `[[schedules]]` (preview, send, update) and `[report] owner` are written straight into the demo's
`taska.toml`, all `on = false`, with addresses on `example.com`. The build never calls `schedules --set` or
`--register`, so no launchd job is created.
