"""Compute accuracy/consistency from promptfoo results; diff two snapshots.

Rows are read in the shape promptfoo writes for a config built by evals/generate_promptfoo.py:
the eval id lives in ``testCase.metadata.eval_id`` (the generator puts it in the test's
metadata, not its vars). A snapshot with no identifiable rows is an error, never "0% accuracy".
"""
import argparse
import json
import sys
from collections import defaultdict


def _eval_id(r):
    for meta in ((r.get("testCase") or {}).get("metadata"), r.get("metadata")):
        if isinstance(meta, dict) and meta.get("eval_id"):
            return str(meta["eval_id"])
    return None


def _iter_rows(snap):
    for r in snap.get("results", {}).get("results", []):
        prov = (r.get("provider") or {}).get("id", "?")
        yield _eval_id(r), prov, bool(r.get("success"))


def summarize(snap):
    succ = defaultdict(lambda: defaultdict(list))
    unidentified = 0
    for eid, prov, ok in _iter_rows(snap):
        if eid is None:
            unidentified += 1
            continue
        succ[eid][prov].append(ok)
    if not succ:
        raise ValueError(f"no result row carries an eval_id (testCase.metadata.eval_id); "
                         f"{unidentified} unidentified rows — is this a promptfoo results file "
                         "from a config built by evals/generate_promptfoo.py?")
    by_q = {}
    for eid, provs in succ.items():
        run_to_run, canonical = {}, {}
        for prov, oks in provs.items():
            modal = round(sum(oks) / len(oks))
            run_to_run[prov] = sum(1 for o in oks if int(o) == modal) / len(oks)
            canonical[prov] = modal
        vals = list(canonical.values())
        cross = vals.count(max(set(vals), key=vals.count)) / len(vals)
        pass_rate = sum(sum(o) for o in provs.values()) / sum(len(o) for o in provs.values())
        by_q[eid] = {"pass_rate": pass_rate, "run_to_run": run_to_run, "cross_model": cross}
    overall = {
        "accuracy": sum(q["pass_rate"] for q in by_q.values()) / len(by_q),
        "questions": len(by_q),
        "unidentified_rows": unidentified,
        "version": snap.get("knowledge_version", "unknown"),
    }
    return {"by_question": by_q, "overall": overall}


def diff(baseline, current):
    b, c = summarize(baseline), summarize(current)
    out = {"by_question": {}, "baseline_version": b["overall"]["version"],
           "current_version": c["overall"]["version"],
           "warn_same_version": b["overall"]["version"] == c["overall"]["version"]}
    for eid, cq in c["by_question"].items():
        bq = b["by_question"].get(eid)
        if not bq:
            out["by_question"][eid] = {"temporal": "new"}
            continue
        db = cq["pass_rate"] - bq["pass_rate"]
        if db > 0.01:
            temporal = "improved"
        elif db < -0.01:
            temporal = "regressed"
        else:
            temporal = "stable"
        if (bq["pass_rate"] >= 0.5) != (cq["pass_rate"] >= 0.5):
            temporal = "flipped"
        out["by_question"][eid] = {"accuracy_delta": round(db, 3), "temporal": temporal}
    for eid in b["by_question"]:
        if eid not in c["by_question"]:
            out["by_question"][eid] = {"temporal": "missing"}
    return out


def render_md(current_summary, d=None):
    o = current_summary["overall"]
    L = [f"# Eval report — {o['version']}", "",
         f"- accuracy: {o['accuracy']:.2%} over {o['questions']} questions"]
    if o["unidentified_rows"]:
        L += [f"- ⚠️ {o['unidentified_rows']} result rows carried no eval_id and were not scored"]
    if d:
        if d["warn_same_version"]:
            L += ["", "> ⚠️ baseline and current share a knowledge_version — not a real drift measurement."]
        regr = [g for g, r in d["by_question"].items() if r.get("temporal") in ("regressed", "flipped")]
        gone = [g for g, r in d["by_question"].items() if r.get("temporal") == "missing"]
        L += ["", "## Regressions", *([f"- {g}" for g in regr] or ["- none"]),
              "", "## In the baseline, missing from this run", *([f"- {g}" for g in gone] or ["- none"])]
    return "\n".join(L)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--current", required=True)
    p.add_argument("--baseline")
    p.add_argument("--out")
    a = p.parse_args(argv)
    cur = json.load(open(a.current))
    try:
        cur_sum = summarize(cur)
        d = diff(json.load(open(a.baseline)), cur) if a.baseline else None
    except ValueError as e:
        print(f"consistency_diff: {e}", file=sys.stderr)
        return 2
    md = render_md(cur_sum, d)
    if a.out:
        open(a.out, "w").write(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
