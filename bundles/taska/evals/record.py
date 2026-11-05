#!/usr/bin/env python3
"""One eval record: a store scored against a frozen key, with tree lint, meeting audit and the provenance a later
comparison needs (git commit and diff, prompts, code version, key, models).

  record.py --project DIR --key FILE [--tier 0|1|2] [--label TEXT] [--snapshot-of TEXT] [--neutral]
            [--session FILE] [--out-root DIR]

Writes <out-root>/<YYYYmmdd-HHMMSS>-<tier>/{meta,score,lint,audit}.json and items.csv, and appends one line to
<out-root>/progress.jsonl (default out-root: <project>/reports/evals). Read-only on the store."""
import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parent
RUN_DIR = EVALS.parent / "skills" / "run"
for p in (RUN_DIR, EVALS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import items_csv  # noqa: E402
import trajectory  # noqa: E402
from taska_lib import config as C, evalscore as ES, ingest_audit as IA, ledger as L, lint as LI, store as S  # noqa: E402
from taska_lib.workbench import code_version  # noqa: E402

HEAD_GAPS = ("B7", "B7c", "B8", "B10", "B12", "B13", "B14", "B15")
TIERS: dict[str, str] = json.loads((EVALS / "tiers.json").read_text(encoding="utf-8"))   # low/med/high, as in the Brain evals


def neutral(key: list[dict]) -> list[dict]:
    """The key without covered_by: covered_by names nodes of the tree the judges saw, so a re-extracted copy (other ids)
    must be matched on quotes and positions only, and so must anything it is compared with."""
    return [dict(g, covered_by=None) for g in key]


def _git(run_dir: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(run_dir), *args], capture_output=True, text=True, check=True).stdout


def prompts_sha(run_dir: Path) -> str:
    """One fingerprint over every prompt the run follows (SKILL.md, EXTRACT.md, …): code_version covers only code."""
    h = hashlib.sha256()
    for f in sorted(Path(run_dir).rglob("*.md")):
        h.update(str(f.relative_to(run_dir)).encode() + b"\0" + f.read_bytes())
    return h.hexdigest()


def provenance(run_dir: Path) -> dict[str, str | bool]:
    bundle = str(Path(run_dir).resolve().parents[1])
    diff = _git(run_dir, "diff", "HEAD", "--", bundle)
    return {"git_commit": _git(run_dir, "rev-parse", "HEAD").strip(),
            "git_dirty": bool(_git(run_dir, "status", "--porcelain", "--", bundle).strip()),
            "git_diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
            "code_version": code_version(run_dir), "prompts_sha256": prompts_sha(run_dir)}


MANIFEST_SUFFIXES = (".py", ".md", ".html", ".css", ".js", ".json", ".toml")
SKIP_DIRS = ("__pycache__", "runs")


def _manifest(root: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    for f in sorted(root.rglob("*")):
        rel = f.relative_to(root)
        if f.is_file() and f.suffix in MANIFEST_SUFFIXES and not any(p in SKIP_DIRS for p in rel.parts):
            h.update(str(rel).encode() + b"\0" + f.read_bytes() + b"\0")
            n += 1
    return h.hexdigest(), n


def manifests(bundle: Path = EVALS.parent) -> dict[str, str | int]:
    """What a run executes (skills/) and what grades it (evals/), every file on disk, tracked or not."""
    ex, n1 = _manifest(Path(bundle) / "skills")
    gr, n2 = _manifest(Path(bundle) / "evals")
    return {"execution_manifest_sha256": ex, "grading_manifest_sha256": gr, "manifest_files": n1 + n2}


def changed_since(start: dict, end: dict) -> list[str]:
    return [k for k in ("execution", "grading") if start.get(f"{k}_manifest_sha256") != end.get(f"{k}_manifest_sha256")]


RUN_FIELDS = ("git_commit", "git_dirty", "git_diff_sha256", "code_version", "prompts_sha256", "models",
              "execution_manifest_sha256", "grading_manifest_sha256", "manifest_changed")


def run_provenance(out_root: Path, project: str) -> dict[str, object]:
    """The code, prompts and models a run of this copy used: from the first record of the copy (written right after its
    run); a later rescore carries the provenance of whoever rescored it, never the run's own."""
    for d in sorted(Path(out_root).glob("2*-2")):
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8")) if (d / "meta.json").exists() else {}
        if meta.get("project") == str(project):
            return {k: meta[k] for k in RUN_FIELDS if k in meta}
    return {}


def models(session: Path) -> list[str]:
    return sorted({a.model for a in trajectory.load_run(session).agents if a.model})


def write_record(root: Path, progress: Path, tier: str, label: str, meta: dict, score: dict,
                 extra: dict[str, object] | None = None) -> Path:
    ts = dt.datetime.now()
    rec = Path(root) / f"{ts:%Y%m%d-%H%M%S}-{tier}"
    rec.mkdir(parents=True, exist_ok=False)
    meta = dict(meta, tier=tier, label=label, ts=ts.isoformat(timespec="seconds"))
    (rec / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    (rec / "score.json").write_text(json.dumps(score, indent=1, ensure_ascii=False), encoding="utf-8")
    for name, body in (extra or {}).items():
        (rec / f"{name}.json").write_text(json.dumps(body, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    line = {"ts": meta["ts"], "kind": "eval", "tier": tier, "label": label, "record": rec.name,
            "key_sha": meta.get("key_sha"), "code_version": meta.get("code_version"),
            "git_commit": meta.get("git_commit"), "incomplete": score.get("incomplete")}
    if score.get("incomplete"):   # rates over a partial run would read as a result: the record keeps them, the log does not
        line["missing"] = score.get("missing", [])
    for g in HEAD_GAPS if not score.get("incomplete") else ():
        if g in score:
            line[g] = {"ok": score[g]["ok"], "n": score[g]["n"], "lb95": score[g]["lb95"],
                       "distinct": score[g].get("distinct_nodes")}
    with open(progress, "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
    if "rows" in score:   # after the progress line: a failed CSV never orphans a record, and is said in the folder
        try:
            items_csv.write(score["rows"], dict(meta, record=rec.name), rec / "items.csv")
        except OSError as e:
            (rec / "items.csv.unavailable.txt").write_text(f"No items.csv: writing it failed ({type(e).__name__}: {e}).\n")
    return rec


def model_fields(used: list[str], model_tier: str | None) -> dict[str, object]:
    """The tier a run counts for: only when every agent it ran used that tier's pinned model."""
    pinned = TIERS.get(model_tier or "")
    return {"tiers": TIERS, "model_pinned": pinned,
            "model_tier": model_tier if pinned and used and set(used) == {pinned} else None,
            "model_mismatch": sorted(set(used) - {pinned}) if pinned else []}


def record(project: Path, key: Path, tier: str = "0", label: str = "", snapshot_of: str | None = None,
           neutral_key: bool = False, session: Path | None = None, out_root: Path | None = None,
           model_tier: str | None = None, start_manifests: dict | None = None) -> Path:
    cfg = C.load(project)
    head, items = ES.load_gold(key)
    score = ES.score(cfg.store, neutral(items) if neutral_key else items)
    evs = S.events(cfg.store)
    ev_file = Path(cfg.store) / "events.jsonl"
    now = manifests()
    meta = {**provenance(RUN_DIR), **now,
            **({"manifest_changed": changed_since(start_manifests, now)} if start_manifests else {}),
            "covered_by": not neutral_key, "key_version": head.get("version"),
            "key_sha": head.get("sha256"), "key_path": str(key), "project": str(project), "snapshot_of": snapshot_of,
            "models": (used := models(session) if session else []), "session": str(session) if session else None,
            **model_fields(used, model_tier),
            "store_events_md5": hashlib.md5(ev_file.read_bytes() if ev_file.exists() else b"").hexdigest(),
            "processed": sorted(S.processed(evs) | {e.get("evidence") for e in evs if e.get("by") == "agent" and e.get("evidence")})}
    root = Path(out_root) if out_root else Path(project) / "reports" / "evals"
    root.mkdir(parents=True, exist_ok=True)
    return write_record(root, root / "progress.jsonl", tier, label, meta, score,
                        extra={"lint": LI.lint(L.fold(cfg.store)[0]),
                               "audit": {"A7_room_audio": IA.room_audio(S.evidence(cfg.store))}})


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--key", required=True, help="a key version file (items.vN.jsonl, header with sha256)")
    ap.add_argument("--tier", default="0")
    ap.add_argument("--label", default="")
    ap.add_argument("--snapshot-of")
    ap.add_argument("--neutral", action="store_true", help="ignore the key's covered_by (required for re-extracted copies)")
    ap.add_argument("--session", help="the run's Claude Code session file: the models it used go into meta.json")
    ap.add_argument("--out-root", help="default: <project>/reports/evals")
    ap.add_argument("--model-tier", choices=sorted(TIERS), help="the tier the run was pinned to (tiers.json)")
    a = ap.parse_args(argv)
    rec = record(Path(a.project), Path(a.key), a.tier, a.label, a.snapshot_of, a.neutral,
                 Path(a.session) if a.session else None, Path(a.out_root) if a.out_root else None, a.model_tier)
    score = json.loads((rec / "score.json").read_text(encoding="utf-8"))
    print(json.dumps({"status": "ok", "record": str(rec), "incomplete": score.get("incomplete"),
                      **{g: f"{score[g]['ok']}/{score[g]['n']}" for g in HEAD_GAPS if g in score}}))
    return rec


if __name__ == "__main__":
    main()
