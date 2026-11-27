---
name: lessons
description: Use when the consultant wants to mine past Claude Code sessions for the rules they taught, keep the client-free rule register, or render the lessons page that shows which rule each skill, agent or check enforces.
---

# Lessons

Turn the rules the consultant taught in past sessions into a client-free, approved rule register, then render the lessons page. This is a maintainer workflow: approved rules are committed to the repo.

**How to run scripts.** `$LESSONS` means `<skill-dir>`, the directory holding this `SKILL.md` (`skills/lessons`). `$BUNDLE` is a git checkout of the consult bundle (the directory above `skills/`), never the installed plugin cache. `$REGISTER` is `$BUNDLE/rules/register.json`. Run scripts as `python3 $LESSONS/<script>.py`. Session text, candidates, proposals and the terms file are private: they stay in the consultant's project, never in the bundle.

## Steps

1. Ask the consultant for the Claude Code session files (`*.jsonl`) and the private terms file (`<terms file>`, one client-identifying word or phrase per line). Run `git -C "$BUNDLE" rev-parse --is-inside-work-tree`. If it fails, tell the user that `$BUNDLE` is not a git checkout and stop until they give one. Ask the consultant for their private project folder (`<project>`). Run `git -C "<project>" rev-parse --show-toplevel 2>/dev/null` and `git -C "$BUNDLE" rev-parse --show-toplevel`. If both print the same path, `<project>` is inside the bundle's git repo: tell the user that raw session text must never land in the public repo, and stop until they give a folder outside it. Run `mkdir -p <project>/lessons`.
2. Run the miner. It keeps only the user's own words and drops subagent output, notifications and compaction summaries.
   `python3 $LESSONS/mine_sessions.py <session.jsonl> [<session.jsonl> ...] --out <project>/lessons/candidates.jsonl --only-cued`
3. Read `candidates.jsonl` in batches of 150. Cluster the corrections that recur or were applied.
4. Draft one proposal per cluster into `<project>/lessons/proposals.jsonl`, one JSON object per line. Fields: `id`, `version`, `area`, `scope`, `rule`, `why`, `sources`, `enforced_by`, `see_also`. Format: `id` matches `^[A-Z][A-Z0-9-]{2,40}$`; `version` is `1` for a new rule; `see_also` is `null` unless the rule is already enforced elsewhere; `area` is one of `language`, `evidence`, `story`, `visual`, `process`; `scope` is `epam-practice` or `personal-style`; `rule` is one sentence with no semicolons. Each proposal is client-free (no client, company or person name, nothing from the terms file), carries a `why` that describes the incident without names, lists `sources` as `{session, uuid, timestamp}` pointers into the candidates, and lists `enforced_by` as `{kind, ref, test}` naming the planned check, agent or skill. A lesson that holds for one client project only is not a rule: write it as an onboarding question into `<project>/lessons/onboarding-questions.md`.
5. Add the proposals, then show the user every refusal (the command prints them on stderr) with its problems. Fix the proposals and add again. When none are refused, run the check.
   `python3 $LESSONS/register.py add --register $REGISTER --proposals <project>/lessons/proposals.jsonl --bundle $BUNDLE --terms <terms file>`
   `python3 $LESSONS/register.py check --register $REGISTER --bundle $BUNDLE --terms <terms file>`
6. Stop and wait for the user to name the ids to approve; never choose them and never approve on your own. List each proposed rule with its id. Tell the user which of those rules name an enforcement that does not exist yet: approving them is allowed, but `build_lessons.py` fails until it exists. To find them, run the command below and read the missing paths from the rule cards in `<project>/lessons/lessons.html`.
   `python3 $LESSONS/build_lessons.py --register $REGISTER --bundle $BUNDLE --out <project>/lessons/lessons.html`
7. Only after the user has named the ids, run approve with those ids and today's date:
   `python3 $LESSONS/register.py approve --register $REGISTER --ids <id>[,<id>] --date <YYYY-MM-DD>`
8. Render the lessons page, then open it with `open <project>/lessons/lessons.html` on macOS. The render exits 1 when an approved rule has no sound enforcement: tell the user which rule, and do not call the page done.
   `python3 $LESSONS/build_lessons.py --register $REGISTER --bundle $BUNDLE --out <project>/lessons/lessons.html`
