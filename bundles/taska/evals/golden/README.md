# Golden meetings

Invented meetings with exactly known answers, written as task-bank tasks (`evals/bank.py`). Invented data only: the
Harbor Logistics cast in `templates.json`.

- `templates.json` — the cast (internal and client side), the seven rule questions with the not-work reason each
  falls back to, the filler, and the archetypes: an XL one-block readout, an XL conversation readout, an L working
  session with 4+ owners, a status stand-up, an S client call, a speech-recognition call and a continuity pair. The
  three planted meetings (`planted/generate.py`) are reused as they are.
- `generate.py` — `generate(seed)` gives the VTTs and one slot per expected answer; `bank_tasks(seeds, project,
  rulings)` syncs them into a scratch project, locates every slot in the synced evidence and computes each task's
  class; `review_page(tasks, problems)` shows the class matrix, any empty class and every check.

```bash
python3 evals/golden/generate.py review --seeds 1 2 3 --rulings rulings.json --out DIR
python3 evals/golden/generate.py write --seeds 1 2 3 --rulings rulings.json --reviewed reviewed.json --version v1
python3 evals/golden/generate.py project --bank evals/golden/bank.v1.jsonl --out DIR
```

`review` writes `DIR/golden-review.html` and prints the counts (per capability, per class, per status, empty classes,
pending rulings); it writes no bank. `write` writes `golden/bank.v1.jsonl` (`--out` picks another folder) and refuses
a version that exists. Both exit 1 when a slot cannot be located and 2 on a bad rulings file or write error.

`rulings.json` maps a rule category to `work` or `not_work` only. A category absent from the file stays pending: its
checks carry `pending_ruling: true` and are graded apart.

`reviewed.json` is the owner's approval: a JSON list of the check ids (the `id` column of the review page) they read
and agree with. `write` sets `reviewed` on each of them, leaves a check waiting for a ruling as it is (and lists it
under `reviewed_but_pending`) and refuses an id the bank does not have. Only a reviewed check can become `golden`.

`write` also writes `bank.v1.meta.json` beside the bank: the seeds, the rulings, the reviewed ids, the date and the
note. With it the bank regenerates byte for byte; `tests/test_golden_bank_flow.py` regenerates every committed bank
from it and fails when a task's transcript or a span has moved. `record.py --promote` carries it to the new version.

`project` syncs the bank's meetings (from the seeds its tasks name) into the fresh folder `DIR` and checks that every
task's transcript matches. That `DIR` is the project for `bank.py run` and `run.py --smart` on the golden bank; both
refuse, before any model call, a project whose meetings are not the bank's.

## Recordings, the free gate and runs

```bash
python3 evals/golden/record.py --bank evals/golden/bank.v1.jsonl --tier low --k 3 --yes   # paid: records k trials per task
python3 evals/golden/record.py --bank evals/golden/bank.v1.jsonl --check                  # free: replay every recording
python3 evals/golden/record.py --bank evals/golden/bank.v1.jsonl --promote v2             # free: statuses from recordings
python3 evals/bank.py run --project DIR --bank evals/golden/bank.v1.jsonl --tier low --k 3 --yes   # paid: the run gate
python3 evals/bank.py compare-models --a RUN_A --b RUN_B                                  # free: one bank, two tiers
```

A recording (`recorded/<version>/<task>-<n>/`) keeps the copy's ledger, `taska.toml`, `trace.json` and `grade.json`,
with every other meeting's text blanked and no session id or local path; refreshing one never removes a recording
nothing replaced. `tests/test_golden_recorded.py` replays every recording on each test run and fails when a grader
change flips a recorded verdict; it skips when there are none. A check becomes `golden` once it is reviewed and two of
its three recordings agree; a check waiting for a ruling never does.

`DIR` for `bank.py run` is a project holding the bank's meetings (`generate.py project` makes one); `record.py` syncs
one from the seeds when `--project` is omitted.

## Continuity in v1

v1 covers two meetings run in order on one copy (`after`): meeting 2 restates meeting 1's commitment in other words
and must create no new node for it (`no_duplicate`), and it reports another of meeting 1's commitments done, a
`no_duplicate` check that also names the status it expects (`expect_status: done`). Parked: the re-synced copy of
meeting 1 (`duplicate_of`) is dropped: no meeting delivers one and no task checks it; the reprocess case (meeting 1's decisions read cleared and read again) is not built; and the done report's status
check is pending (`pending_ruling: true`, graded apart and never promoted), because the bank's grader does not yet
read `expect_status`.

These results are a gate on taska's code and prompts over invented meetings, not a claim about any client's meetings.
