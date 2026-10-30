"""Planted meeting generator for taska tier-1 extraction tests.

Public API
----------
generate(seed, minutes=30) -> (vtt_text: str, gold: list[dict])
    Return a VTT transcript and 14 gold annotation items for the given seed.
    Gold items are in Task-3 format plus a "form" key.
    "evidence" is None — run.sh fills it after `taska sync`.

per_form(store, gold) -> dict
    Per-form scoring after a real extraction run.
    Returns {"incomplete": True} when the meeting evidence is not processed.
    Returns {"incomplete": False, "forms": {form: {"n", "ok", "rate"}}} otherwise.

vtt_name(seed) -> str
    The VTT filename for this seed: "YYYY-MM-DD Harbor daily sync.vtt".

CLI (run.sh entry point)
------------------------
python3 generate.py [--out DIR] SEED...
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Ensure taska_lib is importable regardless of the calling directory
_HERE = Path(__file__).resolve().parent
_RUN_DIR = _HERE.parents[1] / "skills" / "run"
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

from taska_lib import store as S, ledger as L
from taska_lib.evalscore import _card_match, match_item
from taska_lib.text import resolve_due

_TEMPLATES = json.loads((_HERE / "templates.json").read_text())

# ── date helpers ──────────────────────────────────────────────────────────────

_BASE_DATE = dt.date(2026, 3, 2)  # Monday


def _meeting_date(seed: int) -> str:
    """ISO date derived from seed: advance `seed` weekdays from 2026-03-02."""
    d = _BASE_DATE
    n = int(seed)
    while n > 0:
        d += dt.timedelta(days=1)
        while d.isoweekday() > 5:          # skip Sat/Sun
            d += dt.timedelta(days=1)
        n -= 1
    return d.isoformat()


def vtt_name(seed: int) -> str:
    """VTT filename for this seed."""
    return f"{_meeting_date(seed)} Harbor daily sync.vtt"


# ── timing ────────────────────────────────────────────────────────────────────

def _ts(seconds: int) -> str:
    """Integer seconds -> 'HH:MM:SS'."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _vtt_cue(start: int, end: int, speaker: str, text: str) -> str:
    return f"{_ts(start)}.000 --> {_ts(end)}.000\n<v {speaker}>{text}</v>"


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


# ── gold field derivation ─────────────────────────────────────────────────────

def _to_gold_fields(slot: dict, meeting_date: str) -> dict:
    """Derive gold annotation fields from a template slot."""
    form = slot["form"]

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

def generate(seed: int, minutes: int = 30):
    """Return (vtt_text, gold_items) for the given seed.

    vtt_text  : complete WebVTT for a meeting lasting ``minutes`` minutes.
    gold_items: 14 annotation dicts (Task-3 format + "form").  "evidence" is None.
    """
    import random
    rng = random.Random(seed)

    meeting_date = _meeting_date(seed)
    slots = _TEMPLATES["slots"]
    filler_pool = _TEMPLATES["filler"]

    D = minutes * 60
    step = D / _N_CUES
    cue_dur = max(3, int(step * 0.7))

    # Draw filler for all non-item positions
    n_filler = _N_CUES - len(slots)
    filler_draw = [rng.choice(filler_pool) for _ in range(n_filler)]

    item_set = set(_ITEM_CUE_INDICES)
    item_idx = 0
    filler_idx = 0

    cues_text = []
    gold = []

    for cue_num in range(_N_CUES):
        start = int(cue_num * step)
        end = start + cue_dur

        if cue_num in item_set:
            slot = slots[item_idx]
            speaker, text = slot["speaker"], slot["utterance"]
            cues_text.append(_vtt_cue(start, end, speaker, text))

            fields = _to_gold_fields(slot, meeting_date)
            gold.append({
                "evidence": None,
                "quote": text,
                "ts": _ts(start),
                "speaker": speaker,
                "title": slot["title"],
                **fields,
                "form": slot["form"],
                "source": "meetings",
            })
            item_idx += 1
        else:
            f = filler_draw[filler_idx % len(filler_draw)]
            filler_idx += 1
            cues_text.append(_vtt_cue(start, end, f["speaker"], f["utterance"]))

    vtt = "WEBVTT\n\n" + "\n\n".join(cues_text) + "\n"
    return vtt, gold


# ── per-form scorer ───────────────────────────────────────────────────────────

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
        if not form:
            continue
        c = form_counts.setdefault(form, {"n": 0, "ok": 0})
        c["n"] += 1

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
            # 1. Generate VTT + write gold.json
            vtt, gold = generate(seed)
            calls = P / "calls"
            calls.mkdir()
            (calls / vtt_name(seed)).write_text(vtt)
            (P / "gold.json").write_text(json.dumps(gold))

            # 2. Init project
            subprocess.run(
                [*taska_cmd, "--project", str(P), "init", "--name", "Harbor Logistics"],
                capture_output=True, check=True,
            )

            # 3. Append people, team, source to taska.toml
            toml = P / "taska.toml"
            toml.write_text(
                toml.read_text() + f"""
[[people]]
name = "Nora Quill"
side = "internal"
[[people]]
name = "Ravi Tamsin"
side = "internal"
[[people]]
name = "Opal Brennick"
side = "client"
[[teams]]
name = "Yard team"
members = ["Nora Quill", "Ravi Tamsin"]
aliases = ["yard guys", "the yard people"]
[sources.meetings]
adapter = "meetings_folder"
path = "{calls}"
"""
            )

            # 4. Sync
            subprocess.run(
                [*taska_cmd, "--project", str(P), "sync"],
                capture_output=True, check=True,
            )

            # 5. Get evidence ID
            r = subprocess.run(
                [*taska_cmd, "--project", str(P), "pending"],
                capture_output=True, text=True, check=True,
            )
            items = json.loads(r.stdout).get("items") or []
            if not items:
                sys.stderr.write(f"  seed {seed}: no pending evidence after sync\n")
                seeds_data[str(seed)] = {"incomplete": True}
                continue
            evid = items[0]["id"]
            sys.stderr.write(f"  seed {seed}: evidence {evid}\n")

            # 6. Patch gold with evidence ID
            gold_patched = json.loads((P / "gold.json").read_text())
            for g in gold_patched:
                g["evidence"] = evid
            (P / "gold.json").write_text(json.dumps(gold_patched))

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

    result = {"seeds": seeds_data, "total": total}
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
