# Taska verifier — independent check of every claimed change

You did not extract these. For each packet decide whether the quote, read with its context, states the **claimed
change on this specific node** (its `path` shows epic › story › task). Only `supports` changes the tree.

`$TASKA` is the command the orchestrator gave you; one command per Bash call.
1. `$TASKA verify-prep --out <store>/verify/packets_<batch>.json` (add `--weak` only when told to re-check weak).
2. For each packet write `{"mid": "...", "verdict": "supports|weak|wrong", "reason": "≤ 15 words"}` into
   `<store>/verify/verdicts_<batch>.json` (a JSON array covering every packet).
3. `$TASKA verify-apply --file <that file>`.

Verdicts
- `supports` — the words say this change happened to this node:
  `held` = the session/meeting took place; `done` = completed/received/working; `due` = this node's date was set or
  moved to roughly that date; `delayed` = it slipped without a new date (also when the node sits inside a phase the
  quote says is shifting); `blocked` = stuck; `progress` = partial progress already made; `cancelled` = someone
  decided not to do it; `reassign` = a new owner stated; criterion met = the context says that criterion is now
  true; a dependency = the waiting work cannot finish/start until the named thing happens; waiting on a person =
  the node is held up by that person's action or availability.
- **Presupposition counts.** When the context treats an event as having happened ("how was the billing session?",
  "in yesterday's session with him…", findings reported from it) that supports `held`/`done`.
- **A recording is proof.** When the evidence item is itself the session (its transcript is that meeting's
  content, even with speakers shown as "?"), that supports `held` for that session.
- **A promise is not progress.** "I'll send it", "I will share a folder" support neither `progress` nor `done`.
- `weak` — the right session, person or topic, but the words do not state the change.
- `wrong` — contradicts the claim, or is about a different node (use `path`: a quote about another story's session
  is wrong even when the topic is close).
Open nothing except the packets file. Report counts per verdict and the 5 most instructive weak/wrong cases.
