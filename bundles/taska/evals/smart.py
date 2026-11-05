"""Smart selection: a development eval round runs the smallest set of bank tasks the change can affect, so a round
takes 5–10 minutes instead of the full tier 2's 13–35. `plan` reads only the changed files (and, when given, their
changed lines) and the bank: no model, no git. `select` takes an explicit set of tasks or meetings through the same
budget. Expected minutes and the budget's drop list are bank.budget_plan's."""
import re
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parent
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import bank as B  # noqa: E402

ROUNDS = ("rescore", "capability", "second_read", "smoke", "full")   # narrowest first; a plan takes the widest it needs
CHECKS = B.CHECK_TYPES[:3]   # the check lists a task carries (outcome is not one)
SMOKE_CLASSES = ("S", "M", "L", B.ONE_BLOCK)
NO_SNAPSHOT = ("the second read alone would run on copies taken after extraction, but no run keeps an "
               "after-extraction snapshot yet: this is the smoke set")
FULL = "a release, deploy or weekly change needs the full tier 2: run.py --tier 2 --yes --full"
_PREFIX = "bundles/taska/"
_LIB = "skills/run/taska_lib/"

REPLAYED = {_LIB + "evalscore.py"}   # bank.replay grades with it
JUDGED = {"evals/quality.py"}   # quality.py judge-run --cache-only runs it
JUDGE_FILES = {"evals/quality.py", "evals/JUDGE.md", "evals/QUALITY_JUDGE.md"}
RESCORE = REPLAYED | JUDGE_FILES | {f"evals/{n}.py" for n in (
    "verdict", "compare", "crossrun", "items_csv", "relabel", "key_tools", "gold_from_judged", "check_judge",
    "check_reader", "pool", "echo", "trajectory", "evaltrace")} | {"evals/READER.md", _LIB + "report.py",
                                                                    _LIB + "teams_report.py"}
SMOKE = {_LIB + "ai.py", "evals/run.py", "evals/record.py", "evals/golden/record.py", "evals/bank.py", "evals/tiers.json"}
REGENERATE = {"evals/meetings.py", "evals/golden/generate.py", "evals/golden/templates.json", "evals/planted/generate.py",
              "evals/planted/templates.json"}
SECOND_READ = {_LIB + "reads.py"}
LONG_MEETING = {_LIB + "coverage.py", _LIB + "leftovers.py"}
CODE_CAPS = {_LIB + "sensitive.py": ("people",)}
PROMPTS = {"skills/run/EXTRACT.md", "skills/run/SKILL.md", "skills/run/VERIFY.md"}
RELEASE = {".claude-plugin/plugin.json"}
IGNORED = {"evals/evalview.py", "evals/bankview.py", "evals/live.py", "evals/smart.py",
           "skills/run/workbench.html", "skills/run/schedules.html", "skills/run/tokens.css", "skills/run/shared.js",
           "skills/run/DEPS.md", _LIB + "workbench.py", _LIB + "schedules.py", _LIB + "runner.py", _LIB + "runlog.py",
           _LIB + "gate.py", _LIB + "deliver.py"}
SYNC_ONLY = (_LIB + "sources/", _LIB + "transcripts.py", _LIB + "media.py")
ALIASES = {"committed": ("committed", "commitment"), "proposed": ("proposed", "proposal"),
           "decisions": ("decision", "decisions", "risk", "risks"),
           "client_owed": ("client_owed", "client owed", "client-owed", "client owes"), "owners": ("owner", "owners"),
           "not_work": ("not_work", "not work", "not-work"), "people": ("people matter", "people matters", "people_matter"),
           "long_meeting": ("long_meeting", "long meeting", "long meetings", "LONG_CHARS"),
           "one_block": ("one_block", "one block", "one-block"), "asr": ("asr", "speech recognition", "speech-recognition"),
           "continuity": ("continuity",)}
SECOND_READ_WORDS = ("second read", "second reads", "second-read")


def _rel(path: str) -> str | None:
    """The path inside bundles/taska, or None for a file outside it."""
    p = path.replace("\\", "/")
    p = p[2:] if p.startswith("./") else p
    return p[len(_PREFIX):] if p.startswith(_PREFIX) else None


def _alias(line: str, words: tuple[str, ...]) -> str | None:
    """The first of `words` the line names as a whole word, if any."""
    return next((w for w in words if re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", line, re.I)), None)


def _caps_named(line: str, caps: list[str]) -> dict[str, str]:
    """{capability: the alias that names it} for one line."""
    found = {c: _alias(line, ALIASES.get(c, (c, c.replace("_", " ")))) for c in caps}
    return {c: a for c, a in found.items() if a}


def _verdict(rnd: str, caps: list[str] | None = None, long_meeting: bool = False, matches: list[dict] | None = None,
             note: str | None = None) -> dict:
    return {"round": rnd, "caps": caps or [], "long_meeting": long_meeting, "matches": matches or [], "note": note}


def _prompt(path: str, lines: list[str] | None, caps: list[str]) -> dict:
    """A prompt file's round from its changed lines: every line about the second read, every line naming a capability
    (each line's matched aliases recorded), else (or with no lines) extraction."""
    long_meeting = any(_alias(ln, ALIASES["long_meeting"]) for ln in lines or [])
    if lines is None:
        return _verdict("smoke", note=f"no changed lines for {path}: read as extraction (the smoke set); use the git "
                                      "path (no --changed) or --tasks for a narrower round")
    body = [ln for ln in lines if ln.strip()]
    if not body:
        return _verdict("smoke", long_meeting=long_meeting)
    if all(_alias(ln, SECOND_READ_WORDS) for ln in body):
        return _verdict("second_read")
    matches = [{"line": ln, "caps": _caps_named(ln, caps)} for ln in body]
    if all(m["caps"] for m in matches):
        return _verdict("capability", sorted({c for m in matches for c in m["caps"]}), long_meeting, matches)
    return _verdict("smoke", long_meeting=long_meeting, matches=[m for m in matches if m["caps"]])


def classify(path: str, lines: list[str] | None, caps: list[str]) -> dict:
    """One changed file's verdict: its `round` (or `ignored` / `regenerate`), the capabilities it names, whether it is
    long-meeting code, the aliases matched per line and a note for the plan's why."""
    rel = _rel(path)
    if rel is None or rel.startswith(("tests/", "docs/")) or rel in IGNORED or rel.endswith("README.md"):
        return _verdict("ignored")
    if rel.startswith(SYNC_ONLY):
        return _verdict("ignored", note=f"{path}: trials run on stored-evidence snapshots and never sync, so no round "
                                        "exercises it")
    if rel in RELEASE:
        return _verdict("full")
    if rel in REGENERATE:
        return _verdict("regenerate", note=f"{path} makes meetings, not runs: no round on the current bank; regenerate "
                                           "the bank (a new version), then run the changed meetings' tasks as an "
                                           "explicit set (--meetings)")
    if rel in SMOKE:
        note = (f"{path} changes the pinned models: every older run at that tier stops pairing, so the baseline "
                "is invalidated" if rel == "evals/tiers.json" else None)
        return _verdict("smoke", note=note)
    if rel in RESCORE or (rel.startswith("evals/") and rel.endswith(".jsonl")):
        return _verdict("rescore")
    if rel in SECOND_READ:
        return _verdict("second_read")
    if rel in CODE_CAPS:
        return _verdict("capability", [c for c in CODE_CAPS[rel] if c in caps])
    if rel in PROMPTS:
        return _prompt(path, lines, caps)
    if rel.startswith("evals/"):
        return _verdict("rescore")
    if rel.startswith("skills/run/"):
        return _verdict("smoke", long_meeting=rel in LONG_MEETING)
    return _verdict("ignored")


def exercised(path: str) -> bool:
    """A rescore change some free command runs: bank.replay (evalscore, a bank version) or the cached judge (quality)."""
    rel = _rel(path) or ""
    return rel in REPLAYED or rel in JUDGED or (rel.startswith("evals/") and Path(rel).name.startswith("bank.v")
                                                and rel.endswith(".jsonl"))


def _checks(t: dict) -> list[dict]:
    return [chk for typ in CHECKS for chk in t.get(typ, [])]


def _caps_of(t: dict, status: str | None = None) -> set[str]:
    return {c for chk in _checks(t) if status is None or chk.get("status") == status for c in chk.get("caps") or []}


def _golden(t: dict) -> bool:
    return any(chk.get("status") == "golden" for chk in _checks(t))


def _medium(t: dict) -> int:
    return sum(chk.get("difficulty") == "medium" for chk in _checks(t))


def _class_of(t: dict) -> str:
    """The smoke class: bank._minutes_key's size, or `one block` for any one-block task."""
    key = B._minutes_key(t)
    return B.ONE_BLOCK if key.endswith(f"|{B.ONE_BLOCK}") else key


def smoke_set(bank: list[dict], long_meeting: bool) -> tuple[list[str], list[str]]:
    """One task per class S, M, L and one block (XL too for long-meeting changes): golden first, then the most
    medium-difficulty checks, then one that starts no chain's later step, then bank order. A class with no golden task
    or no task at all is said, never silently filled."""
    chosen, why = [], []
    for klass in SMOKE_CLASSES + (("XL",) if long_meeting else ()):
        cands = [(i, t) for i, t in enumerate(bank) if _class_of(t) == klass]
        if not cands:
            why.append(f"the bank has no task of class {klass}")
            continue
        _, best = min(cands, key=lambda c: (not _golden(c[1]), -_medium(c[1]), bool(c[1].get("after")), c[0]))
        if not _golden(best):
            why.append(f"class {klass} has no golden task: {best['task']} stands in")
        chosen.append(best["task"])
    return chosen, why


def capability_tasks(bank: list[dict], caps: list[str]) -> tuple[list[str], list[str]]:
    """The tasks with a golden check of each capability (any check of it, said, when none is golden), in bank order."""
    ids, why = set(), []
    for cap in caps:
        golden = [t["task"] for t in bank if cap in _caps_of(t, "golden")]
        if not golden:
            golden = [t["task"] for t in bank if cap in _caps_of(t)]
            why.append(f"{cap} has no golden task: {', '.join(golden) or 'no task at all'}")
        ids |= set(golden)
    return [t["task"] for t in bank if t["task"] in ids], why


def _minutes(out: dict, bank: list[dict], minutes_by_class: dict[str, float], budget: float | None, k: int,
             parallel: int, overhead: tuple[float | None, int]) -> dict:
    """The plan's expected minutes and budget check, from bank.budget_plan."""
    by = {t["task"]: t for t in bank}
    chosen = [by[tid] for tid in out["tasks"]]
    out |= {"expected_minutes": 0.0 if not chosen and out["round"] != "full" else None, "budget": None,
            "minutes_note": None}
    if not chosen or out["refused"]:
        return out
    try:
        bp = B.budget_plan(chosen, k, parallel, minutes_by_class, budget if budget is not None else float("inf"), overhead)
    except ValueError as e:
        out["minutes_note"] = str(e)
        if budget is not None:
            out["refused"] = f"--budget {budget:g} cannot be checked: {e}"
        return out
    out["expected_minutes"], out["budget"] = bp["expected_minutes"], bp | {"budget": budget}
    if budget is not None and bp["over"]:
        out["refused"] = (f"expected {bp['expected_minutes']:g} minutes is over the budget of {budget:g} at k = {k}, "
                          f"parallel = {parallel}: drop {', '.join(bp['drop'])} (or lower --k, or raise --parallel)")
    return out


def plan(changed: list[str], bank: Path, minutes_by_class: dict[str, float], budget: float | None,
         lines: dict[str, list[str]] | None = None, k: int = 1, parallel: int = 4,
         overhead: tuple[float | None, int] = (None, 0)) -> dict:
    """The round for a change: its widest file decides (rescore < capability < second read < smoke < full) and the
    tasks are the union of what each file needs. `lines` maps a file to its changed lines; without them a prompt file
    is extraction. Every round but a full one is a subset of the bank, so its results are `comparable: false` against
    a full baseline."""
    _, tasks = B.load_bank(bank)
    caps = sorted({c for t in tasks for c in _caps_of(t)})
    files, named, long_meeting, rounds, matches, notes = {}, set(), False, {"rescore"}, {}, []
    for path in changed:
        v = classify(path, (lines or {}).get(path), caps)
        files[path] = v["round"]
        named |= set(v["caps"])
        long_meeting = long_meeting or v["long_meeting"]
        if v["round"] in ROUNDS:
            rounds.add(v["round"])
        if v["matches"]:
            matches[path] = v["matches"]
            notes += [f"{path}: '{m['line']}' names " + ", ".join(f"{c} ('{a}')" for c, a in m["caps"].items())
                      for m in v["matches"]]
        if v["note"]:
            notes.append(v["note"])
    rnd = max(rounds, key=ROUNDS.index)
    why: list[str] = []
    if all(r == "ignored" for r in files.values()):
        why.append("nothing a round measures changed: rescoring the stored records is free")
    why += notes
    judge = any((_rel(f) or "") in JUDGE_FILES for f in changed)
    unexercised = [f for f, r in files.items() if r == "rescore" and not exercised(f)]
    if unexercised:
        why.append(f"no free command exercises {', '.join(unexercised)}: rescore replays the bank (evalscore) and reuses "
                   "cached judge answers (quality) only")
    ids: list[str] = []
    refused = None
    if rnd == "full":
        refused = FULL
    else:
        if rnd in ("smoke", "second_read"):
            ids, w = smoke_set(tasks, long_meeting)
            why += w
            if rnd == "second_read":
                why.append(NO_SNAPSHOT)
        if named:
            extra, w = capability_tasks(tasks, sorted(named))
            why += w
            ids += [t for t in extra if t not in ids]
        ids = B.chain_closure(ids, tasks)
        if rnd == "capability" and not ids:
            refused = f"no task in the bank checks {', '.join(sorted(named)) or 'the named capability'}"
    out = {"round": rnd, "tasks": ids, "capabilities": sorted(named), "long_meeting": long_meeting, "files": files,
           "matches": matches, "why": why, "judge": judge, "unexercised": unexercised, "comparable": rnd == "full",
           "k": k, "parallel": parallel, "refused": refused}
    return _minutes(out, tasks, minutes_by_class, budget, k, parallel, overhead)


def select(bank: Path, minutes_by_class: dict[str, float], budget: float | None, tasks: list[str] | None = None,
           meetings: list[str] | None = None, k: int = 1, parallel: int = 4,
           overhead: tuple[float | None, int] = (None, 0)) -> dict:
    """An explicit set (task ids, or every task of the named meetings) through the same budget; an unknown id or a
    meeting with no task is refused by name."""
    _, all_tasks = B.load_bank(bank)
    known = {t["task"] for t in all_tasks}
    of_meeting = {m: [t["task"] for t in all_tasks if t["evidence"] == m] for m in meetings or []}
    unknown = sorted(set(tasks or []) - known) + sorted(m for m, ids in of_meeting.items() if not ids)
    wanted = set(tasks or []) | {tid for ids in of_meeting.values() for tid in ids}
    ids = B.chain_closure([t["task"] for t in all_tasks if t["task"] in wanted], all_tasks) if not unknown else []
    out = {"round": "explicit", "tasks": ids, "capabilities": [], "long_meeting": False, "files": {}, "matches": {},
           "why": ["a set chosen by hand"], "judge": False, "unexercised": [], "comparable": False, "k": k,
           "parallel": parallel, "refused": f"not in {bank}: {', '.join(unknown)}" if unknown else None}
    return _minutes(out, all_tasks, minutes_by_class, budget, k, parallel, overhead)


def diff_lines(diff: str) -> dict[str, list[str]]:
    """Per file (its new path, or its old one when deleted), the added and removed lines of a unified diff."""
    out: dict[str, list[str]] = {}
    current, old, in_hunk = None, None, False
    for ln in diff.splitlines():
        if ln.startswith("diff --git "):
            current, old, in_hunk = None, None, False
        elif not in_hunk and ln.startswith("--- "):
            old = ln[6:] if ln.startswith("--- a/") else None
        elif not in_hunk and ln.startswith("+++ "):
            current = ln[6:] if ln.startswith("+++ b/") else old
            if current is not None:
                out.setdefault(current, [])
        elif ln.startswith("@@"):
            in_hunk = True
        elif in_hunk and current is not None and ln[:1] in ("+", "-"):
            out[current].append(ln[1:])
    return out
