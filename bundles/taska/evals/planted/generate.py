"""Planted meeting generator for taska tier-1 extraction tests.

Public API
----------
generate(seed, minutes=30) -> (vtt_text: str, gold: list[dict])
    Return a VTT transcript and 14 gold annotation items for the given seed.
    Gold items are in Task-3 format plus "form" and "file" (the VTT they come from).
    "evidence" is None — run.sh fills it after `taska sync`.

generate_planning(seed, minutes=30) -> (vtt_text: str, gold: list[dict])
    The seed's second meeting, a planning review: 10 items from templates.json `decision_slots` (decisions said
    without a cue word, a risk, an open question, a hypothetical and two people matters).

per_form(store, gold) -> dict
    Per-form scoring after a real extraction run.
    Returns {"incomplete": True} when the meeting evidence is not processed.
    Returns {"incomplete": False, "forms": {form: {"n", "ok", "rate"}}} otherwise.

generate_workshop(seed, minutes=30) -> (vtt_text: str, gold: list[dict])
    The seed's third meeting, a workshop prep: 3 work items (forms commitment, request, due_phrase) and 8
    not-work utterances (form "not_work", each with a "bullet": deck_handling, agenda_invite, poster,
    session_order, how_might_we, client_pain) from templates.json `workshop_slots`.

not_work_recorded(store, gold) -> dict
    {"recorded": n, "items": [{"id", "kind", "title", "bullet"}]}: what the agent created or proposed from the
    workshop's not-work utterances (on one, or in the same turn).

vtt_name(seed) -> str
    The VTT filename for this seed: "YYYY-MM-DD Harbor daily sync.vtt".

vtt_name_planning(seed) -> str
    The planning review's filename: "YYYY-MM-DD Harbor planning review.vtt" (same day).

vtt_name_workshop(seed) -> str
    The workshop prep's filename: "YYYY-MM-DD Harbor workshop prep.vtt" (same day).

CLI (run.sh entry point)
------------------------
python3 generate.py [--out DIR] SEED...
"""
import datetime as dt
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

# Ensure taska_lib is importable regardless of the calling directory
_HERE = Path(__file__).resolve().parent
_RUN_DIR = _HERE.parents[1] / "skills" / "run"
for _p in (_RUN_DIR, _HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from taska_lib import store as S, ledger as L
from meetings import build_vtt, filler, meeting_date, sync_project, ts
from taska_lib.evalscore import _card_match, _gold_params, _hit_strength, _resolve_node, _decided, match_item
from taska_lib.text import norm, resolve_due

_TEMPLATES = json.loads((_HERE / "templates.json").read_text())
FILLER: list[dict] = _TEMPLATES["filler"]   # the filler pool, {speaker, utterance}: reused by the golden generator

# ── date helpers ──────────────────────────────────────────────────────────────

def vtt_name(seed: int) -> str:
    """VTT filename for this seed."""
    return f"{meeting_date(seed)} Harbor daily sync.vtt"


def vtt_name_planning(seed: int) -> str:
    """The planning review's VTT filename for this seed."""
    return f"{meeting_date(seed)} Harbor planning review.vtt"


def vtt_name_workshop(seed: int) -> str:
    """The workshop prep's VTT filename for this seed."""
    return f"{meeting_date(seed)} Harbor workshop prep.vtt"


# ── timing ────────────────────────────────────────────────────────────────────

# ── cue-sequence layout ───────────────────────────────────────────────────────
#
# 48 cues total: 14 item cues + 34 filler cues.
#
# Structure:
#   [fill*2, item0, fill*2, item1, fill*2, item2, fill*2, item3,  ← third 1 (4 items)
#    fill*4,                                                        ← cross-third gap
#    fill*2, item4, fill*2, item5, fill*2, item6, fill*2, item7, fill*2, item8,  ← third 2 (5 items)
#    fill*4,                                                        ← cross-third gap
#    fill*2, item9, fill*2, item10, fill*2, item11, fill*2, item12, fill*2, item13, fill*2]  ← third 3 (5 items)
#
# With 48 cues uniformly spaced over D=minutes*60 seconds, the item timestamps
# land at:
#   items 0-3: < D/3  (first temporal third)
#   items 4-8: [D/3, 2D/3)  (second temporal third)
#   items 9-13: >= 2D/3  (third temporal third)

_ITEM_CUE_INDICES = [2, 5, 8, 11, 16, 19, 22, 25, 28, 33, 36, 39, 42, 45]
_N_CUES = 48

assert len(_ITEM_CUE_INDICES) == 14
assert max(_ITEM_CUE_INDICES) < _N_CUES

# The planning review: the same 48 cues, its 10 items spread 3/3/4 by timestamp. It stays short, so the decision gate
# reads it as one part; the per-third gate is exercised on real three-part meetings, not by the planted suite.
_PLANNING_CUE_INDICES = [3, 7, 12, 18, 23, 28, 34, 38, 42, 45]

assert len(_PLANNING_CUE_INDICES) == len(_TEMPLATES["decision_slots"])
assert max(_PLANNING_CUE_INDICES) < _N_CUES

# The workshop prep: 3 work items among 8 not-work utterances.
_WORKSHOP_CUE_INDICES = [3, 6, 10, 14, 18, 22, 27, 31, 35, 40, 44]

assert len(_WORKSHOP_CUE_INDICES) == len(_TEMPLATES["workshop_slots"])
assert max(_WORKSHOP_CUE_INDICES) < _N_CUES

DECISION_FORMS = {"implicit_decision": "decision", "client_decision": "decision", "cadence_decision": "decision",
                  "implicit_risk": "risk"}
NOT_WORK_FORMS = ("open_question", "hypothetical", "people_matter", "not_work")


# ── gold field derivation ─────────────────────────────────────────────────────

def _to_gold_fields(slot: dict, meeting_date: str) -> dict:
    """Derive gold annotation fields from a template slot."""
    form = slot["form"]
    if form in DECISION_FORMS or form in NOT_WORK_FORMS:
        # said once, owned by nobody: a decision or risk is recorded, the rest is not work at all
        return {"kind": DECISION_FORMS.get(form), "commitment": "committed" if form in DECISION_FORMS else "not_work",
                "owner": None, "due": None, "status": None, "client_owed": False, "covered_by": None,
                "must_report": False}

    _kind = {
        "commitment": "task", "request": "task", "due_phrase": "task",
        "client_owed": "task", "proposal_team": "task", "proposal_none": "task",
        "declined": "task", "conditional_decision": "decision", "risk": "risk",
    }
    kind = _kind[form]

    _commit = {
        "commitment": "committed", "request": "committed", "due_phrase": "committed",
        "client_owed": "committed", "conditional_decision": "committed", "risk": "committed",
        "proposal_team": "proposed", "proposal_none": "proposed",
        "declined": "declined",
    }
    commitment = _commit[form]

    if form == "client_owed":
        owner = "Opal Brennick"
    elif form == "proposal_team":
        owner = "Yard team"
    elif form in ("proposal_none", "declined", "conditional_decision", "risk"):
        owner = None
    else:
        owner = slot.get("owner")

    due = None
    if form == "due_phrase":
        dp = slot.get("due_phrase", "")
        if dp:
            due = resolve_due(dp, meeting_date)
    elif form == "conditional_decision":
        due = slot.get("trigger_date")

    return {
        "kind": kind,
        "commitment": commitment,
        "owner": owner,
        "due": due,
        "status": None,
        "client_owed": form == "client_owed",
        "covered_by": None,
        "must_report": False,
    }


# ── main generator ────────────────────────────────────────────────────────────

def generate(seed: int, minutes: int = 30) -> tuple[str, list[dict]]:
    """Return (vtt_text, gold_items) for the given seed.

    vtt_text  : complete WebVTT for a meeting lasting ``minutes`` minutes.
    gold_items: 14 annotation dicts (Task-3 format + "form" + "file").  "evidence" is None.
    """
    return _meeting(random.Random(seed), seed, minutes, _TEMPLATES["slots"], _ITEM_CUE_INDICES, vtt_name(seed))


def generate_planning(seed: int, minutes: int = 30) -> tuple[str, list[dict]]:
    """Return (vtt_text, gold_items) for the seed's planning review: 10 annotation dicts, a people matter marked
    ``"people_matter": true``. Its filler is drawn apart from the daily sync's, so the two never open alike."""
    return _meeting(random.Random(f"planning-{seed}"), seed, minutes, _TEMPLATES["decision_slots"],
                    _PLANNING_CUE_INDICES, vtt_name_planning(seed))


def generate_workshop(seed: int, minutes: int = 30) -> tuple[str, list[dict]]:
    """Return (vtt_text, gold_items) for the seed's workshop prep: 11 annotation dicts, 3 work items and 8
    ``"form": "not_work"`` utterances carrying a ``"bullet"``. Its filler is drawn apart from the other meetings'."""
    return _meeting(random.Random(f"workshop-{seed}"), seed, minutes, _TEMPLATES["workshop_slots"],
                    _WORKSHOP_CUE_INDICES, vtt_name_workshop(seed))


def _meeting(rng: random.Random, seed: int, minutes: int, slots: list[dict], item_cues: list[int],
             file: str) -> tuple[str, list[dict]]:
    when = meeting_date(seed)
    filler_pool = _TEMPLATES["filler"]

    D = minutes * 60
    step = D / _N_CUES
    cue_dur = max(3, int(step * 0.7))

    # Draw filler for all non-item positions
    n_filler = _N_CUES - len(slots)
    filler_draw = filler(rng, filler_pool, n_filler)

    item_set = set(item_cues)
    item_idx = 0
    filler_idx = 0

    cues = []
    gold = []

    for cue_num in range(_N_CUES):
        start = int(cue_num * step)
        end = start + cue_dur

        if cue_num in item_set:
            slot = slots[item_idx]
            speaker, text = slot["speaker"], slot["utterance"]
            cues.append((start, end, speaker, text))

            fields = _to_gold_fields(slot, when)
            gold.append({
                "evidence": None,
                "quote": text,
                "ts": ts(start),
                "speaker": speaker,
                "title": slot["title"],
                **fields,
                "form": slot["form"],
                "source": "meetings",
                "file": file,
                **({"people_matter": True} if slot["form"] == "people_matter" else {}),
                **({"bullet": slot["bullet"]} if "bullet" in slot else {}),
            })
            item_idx += 1
        else:
            f = filler_draw[filler_idx % len(filler_draw)]
            filler_idx += 1
            cues.append((start, end, f["speaker"], f["utterance"]))

    return build_vtt(cues), gold


# ── per-form scorer ───────────────────────────────────────────────────────────

def _hits(g: dict, N: dict, evs: list[dict], ev: dict) -> tuple[set[str], set[str], bool]:
    """(every node the agent made or noted quoting g, the kinds of the nodes created from it, whether a pending card
    quotes it). match_item keeps only the best node, where a decision made beside a task must count. Quote hits
    only: the planning review sets its items a few hundred characters apart, and a node quoting a neighbour is not
    this one."""
    shingles, offset, item = _gold_params(g, ev)
    decided = _decided(evs)
    nodes, created, carded = set(), set(), False
    for e in evs:
        if e.get("evidence") != g["evidence"] or e.get("type") not in ("create", "agent", "note"):
            continue
        hit = _hit_strength(e, shingles, offset, item)
        if hit is None or hit[0] < 0.5:
            continue
        if e.get("type") == "agent" and e.get("op") == "propose":
            carded = carded or (e.get("by") == "agent" and e.get("mid") not in decided)
            continue
        if e.get("by") != "agent" and not (e.get("type") == "create" and e.get("from_proposal")):
            continue
        if e.get("type") == "create":
            created.add(e.get("kind"))
        nid = e.get("id") if e.get("type") == "create" else e.get("node")
        resolved = _resolve_node(nid, N) if nid else None
        if resolved:
            nodes.add(resolved)
    return nodes, created, carded


def per_form(store: Path, gold: list) -> dict:
    """Per-form scoring after a real extraction run.

    Returns ``{"incomplete": True}`` when the meeting evidence is not processed
    (no "processed" event in events.jsonl for any gold evidence id).

    Returns ``{"incomplete": False, "forms": {form: {"n", "ok", "rate"}}}``
    otherwise.

    ok rules (per binding decisions):
    - committed  → matched to a node
    - proposed   → matched to a node OR a pending propose card (like scorer B8)
    - declined   → NOT matched to a task or story (a decision recording the refusal is fine)
    - planning review forms count the nodes whose quote holds the item (not a nearby one):
      implicit_decision, client_decision, cadence_decision → a decision node among them; implicit_risk → a risk node
    - open_question, hypothetical → no decision node created from it (a task, nothing, or a note quoting it on a
      decision made from something else is fine)
    - people_matter → no node of any kind and no pending card quotes it
    """
    store = Path(store)
    ev_dict = S.evidence(store)
    evs = S.events(store)
    N, _notes, _order, ev = L.fold(store, evs=evs, ev=ev_dict)
    proc = S.processed(evs)

    gold_evids = {g["evidence"] for g in gold if g.get("evidence")}
    if not gold_evids or not (gold_evids <= proc):
        return {"incomplete": True}

    form_counts: dict = {}
    for g in gold:
        form = g.get("form")
        if not form or form == "not_work":
            continue    # scored by not_work_recorded: the count of what the agent made of it
        c = form_counts.setdefault(form, {"n": 0, "ok": 0})
        c["n"] += 1

        if form in DECISION_FORMS or form in NOT_WORK_FORMS:
            nodes, created, carded = _hits(g, N, evs, ev)
            if form in DECISION_FORMS:
                ok = DECISION_FORMS[form] in {N[n].get("kind") for n in nodes}
            elif form == "people_matter":
                ok = not nodes and not carded
            else:
                # a revise never changes a kind, so only a create makes it a decision; a note on a decision is context
                ok = "decision" not in created
            c["ok"] += ok
            continue

        nid = match_item(g, N, evs, ev)
        carded = False if nid is not None else _card_match(g, evs, ev)
        commitment = g.get("commitment")

        if commitment == "declined":
            # B16 is declined work created anyway: a task or story for it fails; a decision recording the "no" does not
            ok = nid is None or N.get(nid, {}).get("kind") in ("decision", "risk")
        elif commitment == "proposed":
            ok = nid is not None or carded
        else:
            ok = nid is not None

        if ok:
            c["ok"] += 1

    return {
        "incomplete": False,
        "forms": {
            form: {
                "n": c["n"],
                "ok": c["ok"],
                "rate": round(c["ok"] / c["n"], 3) if c["n"] else None,
            }
            for form, c in form_counts.items()
        },
    }


def _turn_texts(item: dict | None, quote: str) -> list[str]:
    """Normalised text of the turn(s) of an evidence item that hold `quote`."""
    nq = norm(quote)
    return [norm(t.get("text") or "") for t in (item or {}).get("turns") or [] if nq and nq in norm(t.get("text") or "")]


def not_work_recorded(store: Path, gold: list) -> dict:
    """What the agent made of the not-work utterances of a meeting: ``{"recorded": n, "items": [{"id", "kind",
    "title", "bullet"}]}``. Counts an agent create or propose of that meeting's evidence whose quote lies on a
    ``form: "not_work"`` gold utterance or in the same turn as one, unless it quotes a work item (as per_form reads a hit). The bullet is the gold utterance's."""
    store = Path(store)
    ev = S.evidence(store)
    evs = S.events(store)
    items, seen = [], set()
    work = [g for g in gold if g.get("form") != "not_work" and g.get("evidence")]
    for g in gold:
        if g.get("form") != "not_work" or not g.get("evidence"):
            continue
        shingles, offset, item = _gold_params(g, ev)
        turns = _turn_texts(item, g["quote"])
        for e in evs:
            created = e.get("type") == "create" and e.get("by") == "agent"
            proposed = e.get("type") == "agent" and e.get("op") == "propose" and e.get("by") == "agent"
            if e.get("evidence") != g["evidence"] or not (created or proposed) or e.get("mid") in seen:
                continue
            if any((h := _hit_strength(e, *_gold_params(w, ev))) and h[0] >= 0.5
                   for w in work if w["evidence"] == e.get("evidence")):
                continue    # it quotes a work item, which may share a turn with the not-work line
            eq = norm(e.get("quote") or "")
            hit = _hit_strength(e, shingles, offset, item)
            if not eq or not ((hit and hit[0] >= 0.5) or any(eq in t for t in turns)):
                continue
            seen.add(e.get("mid"))
            what = e.get("fields") or {} if created else e.get("proposal") or {}
            items.append({"id": e.get("id") if created else e.get("node"),
                          "kind": e.get("kind") if created else what.get("kind"),
                          "title": what.get("title"), "bullet": g.get("bullet")})
    return {"recorded": len(items), "items": items}


# ── CLI entry point (called by run.sh) ───────────────────────────────────────

def call_model(args: list) -> int:
    """Run the model with claude's arguments: once through $TASKA_AI when set (a launcher that picks its own
    sign-in), else claude on the subscription, then on claude's default backend. The exit code; -1 when it could
    not run."""
    ai = os.environ.get("TASKA_AI")
    subscription = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_MODEL"} | {"CLAUDE_CODE_USE_BEDROCK": "false"}
    tries = [([ai, *args], None)] if ai else [(["claude", *args], subscription), (["claude", *args], None)]
    rc = -1
    for cmd, env in tries:
        try:
            rc = subprocess.run(cmd, env=env, timeout=600).returncode
        except (subprocess.TimeoutExpired, OSError) as exc:
            sys.stderr.write(f"  model call failed: {exc!r}\n")
            rc = -1
        if rc == 0:
            return 0
    return rc


def keep(project: Path, dest: Path, seed: int) -> Path:
    """Copy a finished run (config, gold, ledger, evidence, the meeting) to dest/seed-N, the meetings path made
    relative so the copy holds no path of this machine and replays anywhere."""
    out = Path(dest) / f"seed-{seed}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in ("calls", ".taska"):
        shutil.copytree(project / name, out / name, ignore=shutil.ignore_patterns("*.tmp", "out", "proposals"))
    shutil.copy(project / "gold.json", out / "gold.json")
    toml = (project / "taska.toml").read_text().replace(f'"{project / "calls"}"', '"calls"')
    (out / "taska.toml").write_text(toml)
    return out


def prepare(P: Path, seed: int, taska_cmd: list[str]) -> list[dict] | None:
    """Write the seed's three meetings to P/calls, set up the project and sync it, then patch every gold item with the
    evidence of the file it came from (a meeting's title is its file name without .vtt) and write P/gold.json.
    None when a meeting has no pending evidence after sync."""
    gold = []
    vtts = {}
    for vtt, items in (generate(seed), generate_planning(seed), generate_workshop(seed)):
        vtts[items[0]["file"]] = vtt
        gold += items
    people = [{"name": "Nora Quill", "side": "internal"}, {"name": "Ravi Tamsin", "side": "internal"},
              {"name": "Opal Brennick", "side": "client"}]
    teams = [{"name": "Yard team", "members": ["Nora Quill", "Ravi Tamsin"],
              "aliases": ["yard guys", "the yard people"]}]
    evidence = sync_project(vtts, people, P, taska_cmd, teams)
    for g in gold:
        evid = evidence.get(g["file"])
        if not evid:
            sys.stderr.write(f"  seed {seed}: no pending evidence for {g['file']} after sync\n")
            return None
        g["evidence"] = evid
    (P / "gold.json").write_text(json.dumps(gold))
    return gold


def _run_seeds(seeds: list, out_dir: Path, skill_dir: Path, repo_dir: Path, keep_dir: Path = None):
    """Run the full planted pipeline for each seed and write the output JSON."""
    import tempfile

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    out_file = out_dir / f"planted-{ts}.json"

    taska_cmd = [sys.executable, str(skill_dir / "taska.py")]
    planted_dir = Path(__file__).resolve().parent

    # Check for claude
    import shutil
    claude_ok = bool(os.environ.get("TASKA_AI") or shutil.which("claude"))
    if not claude_ok:
        result = {
            "error": "claude not on PATH; headless extraction skipped",
            "seeds": {str(s): {"incomplete": True} for s in seeds},
            "total": {},
        }
        out_file.write_text(json.dumps(result, indent=1))
        print(json.dumps(result, indent=1))
        sys.stderr.write(f"DONE_WITH_CONCERNS: claude not on PATH → {out_file}\n")
        return result

    seeds_data = {}

    for seed in seeds:
        seed = int(seed)
        sys.stderr.write(f"=== seed {seed} ===\n")
        P = Path(tempfile.mkdtemp())
        try:
            # 1-6. Both meetings, the project, sync, gold patched with each meeting's evidence id
            gold_patched = prepare(P, seed, taska_cmd)
            if gold_patched is None:
                seeds_data[str(seed)] = {"incomplete": True}
                continue
            sys.stderr.write(f"  seed {seed}: evidence {sorted({g['evidence'] for g in gold_patched})}\n")

            # 7. Headless extraction — mirrors full-run.sh ai():
            #    try subscription first (CLAUDE_CODE_USE_BEDROCK=false, unset ANTHROPIC_MODEL),
            #    then fall back to Bedrock (plain claude) if the subscription call fails.
            prompt = (
                f"Run the taska nightly pass unattended (--due mode: never ask questions, "
                f"finish with the summary) for the project at {P}. "
                f"Read {skill_dir}/SKILL.md and follow it exactly. "
                f"$TASKA is `python3 {skill_dir}/taska.py --project {P}`. "
                f"<skill-dir> is {skill_dir}. "
                "Never edit events.jsonl, evidence files or taska.toml by hand. "
                "Do not create artifacts or use any web or cloud tool."
            )
            call_model(["-p", prompt, "--permission-mode", "acceptEdits", "--allowedTools",
                        f"Bash(python3 {skill_dir}/taska.py:*)", "Read", "Write", "Edit", "Glob", "Grep", "Agent"])

            # 8. Score per-form
            seed_result = per_form(P / ".taska", gold_patched)
            if not seed_result.get("incomplete"):
                seed_result["not_work_recorded"] = not_work_recorded(P / ".taska", gold_patched)
            seeds_data[str(seed)] = seed_result
            if keep_dir and not seed_result.get("incomplete"):
                keep(P, keep_dir, seed)
            sys.stderr.write(f"  seed {seed}: {seed_result}\n")

        finally:
            shutil.rmtree(P, ignore_errors=True)

    # Aggregate totals
    total: dict = {}
    for res in seeds_data.values():
        if res.get("incomplete"):
            continue
        for form, c in res.get("forms", {}).items():
            tc = total.setdefault(form, {"n": 0, "ok": 0})
            tc["n"] += c["n"]
            tc["ok"] += c["ok"]
    for c in total.values():
        c["rate"] = round(c["ok"] / c["n"], 3) if c["n"] else None

    result = {"seeds": seeds_data, "total": total,
              "not_work_recorded": sum(r["not_work_recorded"]["recorded"] for r in seeds_data.values()
                                       if r.get("not_work_recorded"))}
    out_file.write_text(json.dumps(result, indent=1))
    sys.stderr.write(f"Wrote {out_file}\n")
    return result


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Planted suite runner")
    ap.add_argument("seeds", nargs="+", type=int, metavar="SEED")
    ap.add_argument("--out", default=str(_HERE / "out"), metavar="DIR")
    ap.add_argument("--keep", metavar="DIR", help="save each complete run to DIR/seed-N (replayed by test_planted_recorded.py)")
    args = ap.parse_args()

    repo_dir = _HERE.parents[3]
    skill_dir = repo_dir / "bundles" / "taska" / "skills" / "run"
    result = _run_seeds(args.seeds, Path(args.out), skill_dir, repo_dir, Path(args.keep) if args.keep else None)
    print(json.dumps(result, indent=1))
