#!/usr/bin/env python3
"""The one eval command. It measures the skills/run beside this file and never writes to the project's store.

  run.py --project DIR --key FILE --before EVID [--tier 0|1|2] [--model low|med|high] [--yes] [--full] [--tests DIR]...
         [--ai CMD] [--skip-lower] [--echo DIR]
  run.py --project DIR --smart [--changed FILE...] [--base REF] [--bank FILE] [--budget N] [--k 1] [--parallel 4]
         [--baseline RUN] [--yes]
  run.py --project DIR (--tasks ID... | --meetings EVIDENCE_ID...) [--bank FILE] [--budget N] [--baseline RUN] [--yes]

  tier 0  the bundle's tests (and each --tests DIR) + a record of the live tree
  tier 1  + the planted suite (headless extraction of invented meetings: model calls)
  tier 2  + the key's meetings re-extracted on a snapshot copy taken before EVID, scored without covered_by, compared
          with the previous tier-2 record on the same key, with the run's trajectory (13–35 min: it needs --full)

Records go to <project>/reports/evals/ (record.py), copies to <project>/evals/runs/. --ai is the command that
reaches Claude (default: taska ai); it gets claude's arguments. --model picks one of the three pinned tiers in tiers.json
(default med); the run and its subagents get that model, and a run whose agents used any other is no tier's. Each
record gets its quality page (quality.py, no model call; --echo adds the independent notes) and its live page links it,
and the tier's verdict page (verdict.py) is rebuilt. Every Bash call of the model passes tool_guard.py when it exists.

--smart is the development round (5–10 min, smart.py): the changed files (git diff against the merge-base with main,
plus untracked files; --changed overrides) choose rescore (no model: the stored records replayed against the bank),
one capability's tasks (low tier unless --model), the smoke set, or the full tier 2 (refused: it needs --full). It
prints the plan and its expected minutes (the measured minutes per class of the project's bank runs), refuses one over
--budget naming what to drop, then runs it through bank.py run's own functions, tagged `comparable: false`. --tasks and
--meetings run an explicit set the same way. A round is judged against the project's evals/bars.json and exits 3, as
bank.py run does, when its hard bars fail or cannot be verified; --baseline (an earlier run of the same round, bank and
tier, never a full run) adds the regression bars. A project whose meetings are not the bank's is refused before any
model call."""
import argparse
import datetime as dt
import json
import shutil
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

EVALS = Path(__file__).resolve().parent
RUN_DIR = EVALS.parent / "skills" / "run"
REPO = EVALS.parents[2]
for p in (RUN_DIR, EVALS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import compare  # noqa: E402
import evalview  # noqa: E402
import live  # noqa: E402
import quality  # noqa: E402
import record  # noqa: E402
import trajectory  # noqa: E402
import verdict  # noqa: E402
from taska_lib import config as C, evalscore as ES, ledger as L  # noqa: E402

SESSIONS = Path.home() / ".claude" / "projects"
TIERS = record.TIERS
GUARD = RUN_DIR / "tool_guard.py"
BASH_OUTPUT = 150000   # characters: taska context is about 52 KB; above Claude Code's default it is saved to a file the agent greps
SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SSE_PORT")


def model_env(model: str) -> dict[str, str]:
    """The model's environment: this process's, without the variables of a Claude session that may have started it,
    with the tier's model for its subagents and room for long command output."""
    env = {k: v for k, v in os.environ.items() if k not in SESSION_VARS}
    return env | {"CLAUDE_CODE_SUBAGENT_MODEL": TIERS[model], "BASH_MAX_OUTPUT_LENGTH": str(BASH_OUTPUT)}
TASKA = [sys.executable, str(RUN_DIR / "taska.py")]
PROMPT = ("Run the taska nightly pass unattended (--due mode: never ask questions, finish with the summary) for the "
          "project at {copy}. Read {skill}/SKILL.md and follow it exactly. $TASKA is `{copy}/taska` (use it verbatim; it "
          "already carries --project). <skill-dir> is {skill}. Subagents get the same $TASKA. Never run `sync` (this is "
          "an evaluation copy with no sources). Never edit events.jsonl, evidence files or taska.toml by hand. Do not "
          "create artifacts or use any web or cloud tool.")


def model_cmd(ai: list[str], copy: Path, model: str) -> list[str]:
    """The headless model command for one copy: the prompt, the tier's model, the tool guard's hook on every Bash call
    when tool_guard.py exists, the permission mode and the tools the model may use."""
    cmd = [*ai, "-p", PROMPT.format(copy=copy, skill=RUN_DIR), "--model", TIERS[model]]
    if GUARD.exists():
        hook = {"type": "command", "command": f'"{sys.executable}" "{GUARD}" "{copy}/taska"'}
        cmd += ["--settings", json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [hook]}]}})]
    return cmd + ["--permission-mode", "acceptEdits",
                  "--allowedTools", f"Bash({copy}/taska:*)", "Read", "Write", "Edit", "Glob", "Grep", "Agent"]


def write_wrapper(dest: Path) -> Path:
    """dest/taska: the copy's own taska command, already carrying --project dest."""
    wrapper = Path(dest) / "taska"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{RUN_DIR / "taska.py"}" --project "{dest}" "$@"\n')
    wrapper.chmod(0o755)
    return wrapper


def _taska(project: Path, *args: str) -> dict:
    r = subprocess.run([*TASKA, "--project", str(project), *args], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def reachable(probe: list[str]) -> bool:
    """The `ai --probe` answer: True when a route to Claude answers."""
    try:
        return subprocess.run(probe).returncode == 0
    except OSError as e:
        print(f"Claude could not be reached: {e}")
        return False


GIT_DIFF = ("--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/", "--no-renames")


def changed_files(base: str | None, repo: Path = REPO) -> tuple[list[str], dict[str, list[str]]]:
    """The files changed against `base` (default: the merge-base with main) plus untracked files, repo-relative, and
    each one's changed lines (an untracked file's every line). ValueError (pass --base) when git cannot answer."""
    import smart

    def git(*args: str) -> str:
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
        if r.returncode != 0:
            raise ValueError(f"git {' '.join(args[:2])} failed: {r.stderr.strip() or r.returncode}; pass --base REF "
                             "(or --changed FILE...)")
        return r.stdout
    ref = base or git("merge-base", "HEAD", "main").strip()
    files = [f for f in git("diff", *GIT_DIFF, "--name-only", ref).splitlines() if f]
    lines = smart.diff_lines(git("diff", *GIT_DIFF, "-U0", ref))
    for f in git("ls-files", "--others", "--exclude-standard").splitlines():
        if f and f not in files:
            files.append(f)
            try:
                lines[f] = (repo / f).read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                lines[f] = []
    return files, lines


def judge_cache(record_dir: Path, out_dir: Path) -> dict:
    """quality.py judge-run --cache-only on a copy of one record's quality folder under out_dir (the stored record is
    never written): answers judged in other runs on its key, from a judge tier that has a cache and is not the reader's."""
    meta = json.loads((record_dir / "meta.json").read_text(encoding="utf-8"))
    reader = quality._reader_tiers(meta)
    tier = next((t for t in ("high", "med", "low") if t not in reader
                 and (f := quality._cache_file(record_dir, meta, t)) is not None and f.is_file()), None)
    if tier is None:
        return {"skipped": f"no judge cache on this key for a tier other than the reader's ({', '.join(sorted(reader)) or 'unknown'})"}
    out = out_dir / record_dir.name / "quality"
    shutil.copytree(record_dir / "quality", out, dirs_exist_ok=True)
    return {"tier": tier, "out": str(out), "batches": quality.judge_run(record_dir, out, tier, None, cache_only=True)}


def _newest_bank(project: Path) -> Path | None:
    found = sorted(Path(project, "evals", "bank").glob("bank.v*.jsonl"),
                   key=lambda p: int(m[1]) if (m := re.match(r"bank\.v(\d+)\.jsonl$", p.name)) else -1)
    return found[-1] if found else None


def smart_round(a: argparse.Namespace) -> int:
    """run.py --smart / --tasks / --meetings: the plan printed first, then rescore with no model or trials through
    bank.run_bank, every result written with the round's `comparable` tag."""
    import bank as B
    import bankview
    import smart
    project = Path(a.project)
    bank_path = Path(a.bank) if a.bank else _newest_bank(project)
    if not bank_path or not bank_path.is_file():
        print(f"no bank file: pass --bank (none under {project / 'evals' / 'bank'})")
        return 2
    head, bank = B.load_bank(bank_path)
    by_task = {t["task"]: t for t in bank}
    try:
        bars, bars_path = B.load_bars(project)
    except ValueError as e:
        print(str(e))
        return 2
    sources = [Path(x) for x in a.minutes_from] if a.minutes_from else [project / "reports" / "evals" / "bank"]
    try:
        changed, lines = ((a.changed, {}) if a.changed else changed_files(a.base)) if a.smart else ([], {})
    except ValueError as e:
        print(str(e))
        return 2
    rnd = smart.plan(changed, bank_path, {}, None, lines)["round"] if a.smart else "explicit"   # the round sets the tier
    tier = a.model or ("low" if rnd == "capability" else "med")
    minutes = B.measured_minutes(sources, by_task, tier, TIERS[tier])
    overhead = B.measured_overhead(sources, by_task, tier, TIERS[tier])
    if a.smart:
        p = smart.plan(changed, bank_path, minutes, a.budget, lines, a.k, a.parallel, overhead)
    else:
        p = smart.select(bank_path, minutes, a.budget, a.tasks, a.meetings, a.k, a.parallel, overhead)
    p["tier"] = tier
    print(json.dumps(p, ensure_ascii=False))
    expected = p["expected_minutes"]
    print(f"{p['round']} round: {len(p['tasks'])} tasks, expected "
          + (f"{expected:g} minutes" if expected is not None else f"minutes not measured ({p['minutes_note']})"))
    if p["refused"]:
        print(p["refused"])
        return 2
    if p["round"] == "rescore":
        out_root = project / "reports" / "evals"
        records = [d for d in sorted(out_root.glob("2*")) if (d / "meta.json").is_file()]
        if not records:
            print(f"no stored records under {out_root} to rescore")
            return 0
        result = B.replay(bank_path, records)
        out = B.write_replay(result, out_root / "bank" / f"replay-{result['bank']['version']}-{dt.datetime.now():%Y%m%d-%H%M%S}",
                             bank_path)
        judged = None
        if p["judge"]:   # the newest record's judge answers, from the cache only
            try:
                judged = judge_cache(records[-1], out)
            except Exception as e:
                judged = {"error": f"{type(e).__name__}: {e}"}
        print(json.dumps({"run": str(out), "records": result["records"], "trials": len(result["trials"]),
                          "judge_cache": judged, "unexercised": p["unexercised"]}))
        return 0
    if not p["tasks"]:
        print(f"the {p['round']} round has no task to run in {bank_path}: nothing is measured")
        return 2
    if not a.yes:
        print(f"the {p['round']} round costs model calls: re-run with --yes")
        return 2
    try:
        if a.baseline:   # refused before any model call: the same round's subset, bank and tier only
            base = B.load_run(Path(a.baseline))[0]
            B._comparable({"round": p["round"], "comparable": p["comparable"]}, base)
            B._same({"bank_sha256": head["sha256"], "tier": tier}, base, ("bank_sha256", "tier"))
        B.check_transcripts(project, [by_task[t] for t in p["tasks"]])
    except (ValueError, OSError) as e:
        print(str(e))
        return 2
    ai = [a.ai] if a.ai else None
    probe = [a.ai, "--probe"] if a.ai else [*TASKA, "--project", str(project), "ai", "--probe"]
    failed = not reachable(probe)
    tags: dict[str, str | bool] = {"round": p["round"], "comparable": p["comparable"]}
    out = B.run_bank(project, bank_path, tier, a.k, p["tasks"], a.parallel, ai,
                     invalid="Claude could not be reached: see the reasons above" if failed else "",
                     minutes_by_class=minutes, tags=tags)
    (out / "plan.json").write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    baseline = B.load_baseline(out, Path(a.baseline)) if a.baseline else None
    doc = B.page_doc(out, by_task, bars=bars, baseline=baseline, bars_source=str(bars_path) if bars is not None else None)
    bankview.write(out, doc)
    verdict = doc["aggregate"]["bars"]
    print(json.dumps({"run": str(out), "round": p["round"], "comparable": p["comparable"], "invalid": failed,
                      "bars": verdict["run"]}))
    if failed:
        return 1
    if verdict["run"] in ("fail", "unverified"):
        hard = [f"{r['key']} {r['result']}" for r in verdict["rows"] if r["hard"] and r["result"] in ("fail", "no data")]
        print(f"the hard bars of {bars_path} are {verdict['run']}: {', '.join(hard)}")
        return 3
    return 0


def tests(test_dirs: list[Path]) -> bool:
    runs = [(REPO, ["--with", "pytest", "--with", "pyyaml"], str(EVALS.parent / "tests"))]
    runs += [(Path(d).parent, ["--with", "pytest"], str(d)) for d in test_dirs]
    return all(subprocess.run(["uv", "run", *w, "python", "-m", "pytest", t, "-q", "--color=no"], cwd=cwd).returncode == 0
               for cwd, w, t in runs)


def prepare_copy(project: Path, before: str, key_meetings: set[str], out: Path) -> Path:
    """A snapshot copy that cannot sync and reaches every key meeting in one pass: the other pending items are closed
    with ledger.mark_processed (`done` is the extractor's gate, not the harness's)."""
    snap = _taska(project, "snapshot", "--before", before, "--out", str(out))
    toml = out / "taska.toml"
    t = re.sub(r"(?ms)^\[(sources\.[^\]]+|run)\].*?(?=^\[|\Z)", "", toml.read_text(encoding="utf-8"))
    toml.write_text(t + "\n[run]\nmax_items = 50\nbackfill_max = 0\n", encoding="utf-8")   # an eval copy reads only its key meetings
    write_wrapper(out)
    _taska(out, "render-evidence")
    store = C.load(out).store
    for evid in snap["pending"]:
        if evid not in key_meetings:
            L.mark_processed(store, evid, "eval copy: not a key meeting")
    return out


def find_session(copy: Path, since: float, root: Path | None = None) -> Path | None:
    return trajectory.find_session(copy, since, root or SESSIONS)


def best_baseline(out_root: Path, key_sha: str, tier: str, exclude: Path | None = None) -> tuple[Path | None, int]:
    """(The best run so far, how many runs it was chosen from): complete tier-2 records on the same key, scored without
    links, on the same model tier, one per run (its newest score); ranked by committed work found, then decisions and
    risks, then precision, then the newest. Best of one run per change is partly luck: the page says "best of N"."""
    runs: dict[tuple[str | None, str | None], tuple[Path, dict]] = {}
    for d in sorted(Path(out_root).glob("2*-2")):
        if d == exclude or not (d / "meta.json").exists() or not (d / "score.json").exists():
            continue
        m, sc = (json.loads((d / f).read_text(encoding="utf-8")) for f in ("meta.json", "score.json"))
        if m.get("key_sha") != key_sha or m.get("covered_by") is not False or m.get("model_tier") != tier or sc.get("incomplete"):
            continue
        runs[(m.get("project"), m.get("store_events_md5"))] = (d, sc)   # oldest first: a run keeps its newest score
    if not runs:
        return None, 0
    rate = lambda g: g["ok"] / g["n"] if g and g.get("n") else 0.0
    best = max(runs.values(), key=lambda r: (r[1]["B7"]["ok"], (r[1].get("B13") or {}).get("ok", 0), rate(r[1].get("B15")), r[0].name))
    return best[0], len(runs)


def tier2(project: Path, key: Path, before: str, ai: list[str], runs_root: Path, out_root: Path, model: str = "med",
          echo: Path | None = None) -> dict:
    _, items = ES.load_gold(key)
    start = record.manifests()
    copy = prepare_copy(project, before, {g["evidence"] for g in items},
                        runs_root / f"{dt.datetime.now():%Y%m%d-%H%M%S}-tier2-{model}")
    started, page = time.time(), out_root / "live"
    base, base_of = best_baseline(out_root, ES.load_gold(key)[0].get("sha256") or "", model)
    baseline = dict(live.load_baseline(base), of=base_of) if base else None
    with open(f"{copy}.run.txt", "w", encoding="utf-8") as log:
        proc = subprocess.Popen(model_cmd(ai, copy, model), cwd=copy, stdout=log, stderr=subprocess.STDOUT,
                                env=model_env(model))
        while True:   # the live page, every live.EVERY seconds while the model works
            live.write(page, live.snapshot(copy, items, started, time.time(), SESSIONS, model_tier=model, baseline=baseline))
            try:
                rc = proc.wait(timeout=live.EVERY)
                break
            except subprocess.TimeoutExpired:
                continue
    try:
        return after_run(copy, key, before, model, out_root, started, rc, base, baseline, echo, project, start)
    except Exception:   # the model's work is in the copy: say why recording failed and how to redo it, then fail
        Path(f"{copy}.after_run_error.txt").write_text(
            traceback.format_exc() + f"\nRedo the record without a model call:\n  python3 {Path(__file__).resolve()} --project "
            f"{project} --key {key} --before {before} --model {model} --record-copy {copy}\n", encoding="utf-8")
        raise


def after_run(copy: Path, key: Path, before: str, model: str, out_root: Path, started: float, rc: int | None,
              base: Path | None, baseline: dict | None, echo: Path | None = None, project: Path | None = None,
              start_manifests: dict | None = None) -> dict:
    """Everything after the model: the record, the comparison with the best run so far, the trajectory, the quality
    page (no model call), the last live write with the quality caveats, the page's copy in the record and the tier's
    verdict page. rc None: the harness is gone and the session's own end decides."""
    _, items = ES.load_gold(key)
    page = out_root / "live"
    session = find_session(copy, started)
    rec = record.record(copy, key, "2", f"re-extraction on a snapshot, {model} tier", f"before-{before}", True, session,
                        out_root, model, start_manifests)
    cmp_ = None
    if base:
        (ma, sa), (mb, sb) = compare._load(base), compare._load(rec)
        keyd = {g["cid"]: g for g in items}
        cmp_ = {"a": str(base), "b": str(rec), **compare.compare(ma, sa, mb, sb, keyd),
                "gaps": {g: {"a": sa.get(g), "b": sb.get(g)} for g in compare.GAPS if g in sa}}
        (rec / "compare.html").write_text(compare.page(cmp_, keyd), encoding="utf-8")
    if session:
        run = trajectory.load_run(session)
        traces = trajectory.evidence_traces(run, C.load(copy).store / "evidence")
        (rec / "trajectory.json").write_text(trajectory.to_json(run, traces), encoding="utf-8")
        (rec / "trajectory.html").write_text(trajectory.render_html(run, traces), encoding="utf-8")
        (rec / "trace.json").write_text(json.dumps(trajectory.to_trace(run)), encoding="utf-8")   # the call timeline
    snap = live.snapshot(copy, items, started, time.time(), SESSIONS, exit_code=rc, record=rec.name, model_tier=model,
                         baseline=baseline, out_root=out_root)
    qdoc = quality_page(rec, echo)
    for out, href in ((page, f"../{rec.name}/quality/index.html"), (rec / "live", "../quality/index.html")):   # live/ is the newest run's
        metrics, tile_note = live.precision_tile(snap["metrics"], qdoc, href)
        live.write(out, snap | {"metrics": metrics, "caveats": tile_note + (live.caveats(qdoc, href) if qdoc else [])})
    vdoc = verdict_page(out_root, model, project, rec)
    incomplete = json.loads((rec / "score.json").read_text(encoding="utf-8")).get("incomplete")
    on_tier = json.loads((rec / "meta.json").read_text(encoding="utf-8")).get("model_tier") == model
    errors = [f for f in ("quality_error.txt", "verdict_error.txt") if (rec / f).is_file()]
    changed = json.loads((rec / "meta.json").read_text(encoding="utf-8")).get("manifest_changed")
    return {"ok": snap["status"] == "finished" and not incomplete and on_tier and not changed, "record": rec, "copy": copy,
            "session": session, "compare": cmp_, "evaluation_errors": errors,
            "verdict": vdoc["verdict"] if vdoc else None}


def quality_page(rec: Path, echo: Path | None) -> dict | None:
    """The record's quality page from its copy, its key and the independent notes (none without `echo`), never a judge
    call; a failure is written beside the record and costs nothing else."""
    try:
        with tempfile.TemporaryDirectory() as no_notes:
            doc = quality.document(rec, Path(echo) if echo else Path(no_notes), None, rec / "quality", {})
        evalview.write_quality(rec / "quality", doc)
        return doc
    except Exception:
        (rec / "quality_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        return None


def verdict_page(evals_dir: Path, tier: str, project: Path | None, rec: Path) -> dict | None:
    """The tier's verdict page (<evals_dir>/verdict/<tier>) with the project's bars and adopted baseline; a failure is
    written beside the record and never fails the run."""
    import bank as B
    try:
        evals = Path(project) / "evals" if project else None
        bars = (B.load_bars(project)[0] or {}) if project else {}
        adopted = (json.loads((evals / "baseline.json").read_text(encoding="utf-8"))
                   if evals and (evals / "baseline.json").is_file() else {})
        doc = verdict.build(evals_dir, tier, bars, adopted.get(tier), rec.name)
        verdict.write(Path(evals_dir) / "verdict" / tier, doc)
        return doc
    except Exception:
        (rec / "verdict_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        return None


def record_copy(copy: Path, key: Path, before: str, model: str, out_root: Path, echo: Path | None = None,
                project: Path | None = None) -> dict:
    """Record a finished copy whose harness stopped before recording: the same after-run steps, its session dating it."""
    base, base_of = best_baseline(out_root, ES.load_gold(key)[0].get("sha256") or "", model)
    return after_run(Path(copy), key, before, model, Path(out_root), 0.0, None, base,
                     dict(live.load_baseline(base), of=base_of) if base else None, echo, project)


def exit_code(r: dict) -> int:
    """1 the run itself failed; 4 it ran but its quality or verdict could not be built; else the verdict's code
    (0 ship, 3 inconclusive, 5 hold); a run with no verdict step (below tier 2) is 0."""
    if not r["ok"]:
        return 1
    if r.get("evaluation_errors"):
        return 4
    return verdict.EXIT[r["verdict"]] if r.get("verdict") else 0


def run(project: Path, key: Path, before: str, tier: int = 0, ai: list[str] | None = None,
        test_dirs: list[Path] | None = None, skip_lower: bool = False, runs_root: Path | None = None,
        out_root: Path | None = None, model: str = "med", echo: Path | None = None) -> dict:
    project = Path(project)
    out_root = Path(out_root) if out_root else project / "reports" / "evals"
    ai = ai or [*TASKA, "--project", str(project), "ai", "--"]
    ok, result = True, {}
    if not skip_lower:
        ok = tests(test_dirs or []) and ok
        record.record(project, key, "0", "live tree", f"live-{dt.date.today()}", out_root=out_root)
        if tier >= 1:
            env = dict(os.environ, TASKA_AI=ai[0]) if ai[0] != sys.executable else dict(os.environ)
            ok = subprocess.run([sys.executable, str(EVALS / "planted" / "generate.py"), "--out", str(out_root / "planted"),
                                 "1", "2", "3", "4", "5"], env=env).returncode == 0 and ok
    if tier >= 2:
        result = tier2(project, key, before, ai, Path(runs_root) if runs_root else project / "evals" / "runs", out_root, model, echo)
        ok = ok and result["ok"]
    return {**result, "ok": ok}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--key", help="a key version file (items.vN.jsonl); required except for a smart or explicit round")
    ap.add_argument("--before", help="the first key meeting: the copy is the store before it")
    ap.add_argument("--tier", type=int, default=0, choices=(0, 1, 2))
    ap.add_argument("--model", choices=list(TIERS), help="the pinned model tier (tiers.json; default med, low for a capability round)")
    ap.add_argument("--yes", action="store_true", help="tiers 1 and 2 cost model calls")
    ap.add_argument("--full", action="store_true", help="tier 2 re-extracts every key meeting: 13–35 min")
    ap.add_argument("--tests", action="append", default=[], help="a project's own test folder (repeatable)")
    ap.add_argument("--ai", help="the command that reaches Claude (default: taska ai)")
    ap.add_argument("--skip-lower", action="store_true", help="tier 2 only (the lower tiers ran already)")
    ap.add_argument("--record-copy", help="record a finished copy whose harness stopped before recording (no model call)")
    ap.add_argument("--echo", help="an independent note-taker's calls folder for the quality page (default: none)")
    ap.add_argument("--smart", action="store_true", help="the development round the changed files need (5–10 min)")
    ap.add_argument("--changed", nargs="+", help="the changed files (default: git diff against --base, plus untracked)")
    ap.add_argument("--base", help="the git ref to diff against (default: the merge-base with main)")
    ap.add_argument("--tasks", nargs="+", help="an explicit set of bank task ids, through the same budget")
    ap.add_argument("--meetings", nargs="+", help="every bank task of these evidence ids, through the same budget")
    ap.add_argument("--bank", help="the bank file (default: the newest <project>/evals/bank/bank.vN.jsonl)")
    ap.add_argument("--budget", type=float, help="refuse, before any model call, a round expected to take more minutes")
    ap.add_argument("--k", type=int, default=1, help="trials per task in a smart or explicit round")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--minutes-from", nargs="+", help="recorded bank runs whose minutes per class set the expectation")
    ap.add_argument("--baseline", help="an earlier run of the same round (bank and tier) to check regression against")
    a = ap.parse_args(argv)
    if a.base and a.changed:
        print("--base and --changed both name the change: pass one")
        return 2
    if a.smart and (a.tasks or a.meetings):
        print("--smart chooses the tasks; --tasks and --meetings name them: pass one")
        return 2
    if a.smart or a.tasks or a.meetings:
        return smart_round(a)
    if not a.key or not a.before:
        ap.error("--key and --before are required (or run --smart, --tasks or --meetings)")
    a.model = a.model or "med"
    if a.record_copy:
        r = record_copy(Path(a.record_copy), Path(a.key), a.before, a.model, Path(a.project) / "reports" / "evals",
                        Path(a.echo).expanduser() if a.echo else None, Path(a.project))
        print(json.dumps({k: str(v) if isinstance(v, Path) else v for k, v in r.items() if k != "compare"}))
        return exit_code(r)
    if a.tier and not a.yes:
        print(f"tier {a.tier} costs model calls: re-run with --yes")
        return 2
    if a.tier == 2 and not a.full:
        print("a full tier 2 takes 13–35 min: add --full, or run --smart")
        return 2
    ai = [a.ai] if a.ai else None
    probe = [a.ai, "--probe"] if a.ai else [*TASKA, "--project", a.project, "ai", "--probe"]
    if a.tier and subprocess.run(probe).returncode != 0:
        print("Claude could not be reached: see the reasons above")
        return 1
    r = run(Path(a.project), Path(a.key), a.before, a.tier, ai, [Path(t) for t in a.tests], a.skip_lower, model=a.model,
            echo=Path(a.echo).expanduser() if a.echo else None)
    print(json.dumps({k: str(v) if isinstance(v, Path) else v for k, v in r.items() if k != "compare"}))
    return exit_code(r)


if __name__ == "__main__":
    sys.exit(main())
