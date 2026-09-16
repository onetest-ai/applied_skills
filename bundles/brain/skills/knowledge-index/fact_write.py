#!/usr/bin/env python3
"""Resolve entities, link by recency, judge disagreements, emit + load a ledger."""
import argparse, json, math, os, sqlite3, sys
import fact_schema as FS


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def resolve_key(entity, predicate, existing_keys, aliases, embed_fn,
                high, low, model="BAAI/bge-small-en-v1.5"):
    cand = FS.canon_key(entity, predicate, aliases)
    if cand in existing_keys:
        return cand, "exact"
    if not existing_keys:
        return cand, "distinct"
    texts = [f"{cand[0]} {cand[1]}"] + [f"{e} {p}" for (e, p) in existing_keys]
    vecs = embed_fn(model, texts)
    q, rest = vecs[0], vecs[1:]
    sims = [(_cos(q, v), existing_keys[i]) for i, v in enumerate(rest)]
    best_sim, best_key = max(sims, key=lambda t: t[0])
    if best_sim >= high:
        return best_key, "auto"
    if best_sim >= low:
        return best_key, "review"
    return cand, "distinct"


def find_priors(con, entity, predicate):
    rows = con.execute(
        "SELECT assertion_id, value_json, asserted_at FROM memory_assertions "
        "WHERE entity=? AND predicate=? ORDER BY asserted_at", (entity, predicate)).fetchall()
    return [{"assertion_id": r[0], "value": json.loads(r[1]), "asserted_at": r[2]} for r in rows]


def plan_links(new_id, new_value, new_at, priors):
    auto, disagreements = [], []
    for p in priors:
        if p["value"] == new_value:
            continue
        if new_at > p["asserted_at"]:
            auto.append((new_id, "supersedes", p["assertion_id"]))
        elif new_at == p["asserted_at"]:
            disagreements.append({"new_id": new_id, "prior_id": p["assertion_id"],
                                  "new_value": new_value, "prior_value": p["value"]})
        # new_at < prior: no link; recency resolution in current_fact handles it
    return auto, disagreements


def judge_disagreements(disagreements, judge_fn):
    links, unresolved = [], []
    for d in disagreements:
        try:
            verdict = judge_fn(d)
            rel = verdict.get("relation")
        except Exception as exc:  # noqa: BLE001 — judge failures must not abort intake
            print(f"[warn] judge failed for {d['new_id']}->{d['prior_id']}: {exc}", file=sys.stderr)
            unresolved.append(d); continue
        if rel in ("supersedes", "contradicts"):
            links.append((d["new_id"], rel, d["prior_id"]))
        elif rel == "keep_both":
            pass
        else:
            unresolved.append(d)
    return links, unresolved


def _load_results(results_dir):
    items = []
    for fn in sorted(os.listdir(results_dir)):
        if fn.startswith("result_") and fn.endswith(".json"):
            try:
                items += json.load(open(os.path.join(results_dir, fn)))
            except (ValueError, OSError) as exc:
                print(f"[warn] skip {fn}: {exc}", file=sys.stderr)
    return items


def run(con, results, aliases, embed_fn, judge_fn, high, low, now_iso, apply=False,
        strict_merges=False, model="BAAI/bge-small-en-v1.5"):
    import temporal_memory as T
    T.ensure_schema(con)
    existing = list({(r[0], r[1]) for r in con.execute(
        "SELECT DISTINCT entity, predicate FROM memory_assertions")})
    existing_ids = {r[0] for r in con.execute("SELECT assertion_id FROM memory_assertions")}
    bands = {"exact": 0, "auto": 0, "review": 0, "distinct": 0}
    merge_items = []
    review_count = 0

    # Pass 1: parse + canonicalize every item into a candidate assertion.
    assertions = []
    for it in _load_results(results):
        try:
            cid = int(it["chunk_id"]); value = it["value"]
            ent_in, pred_in = it["entity"], it["predicate"]
        except (KeyError, TypeError, ValueError) as exc:
            print(f"[warn] skip malformed item: {exc}", file=sys.stderr); continue
        row = con.execute("SELECT source, event_date, speaker FROM chunks WHERE id=?", (cid,)).fetchone()
        if not row:
            print(f"[warn] chunk {cid} not found", file=sys.stderr); continue
        source, event_date, speaker = row
        (ce, cp), band = resolve_key(ent_in, pred_in, existing, aliases, embed_fn, high, low, model)
        if band == "review":
            # Spec §7.1 / decision #4: the review band is DISTINCT by default and
            # is NEVER auto-merged — --strict-merges only makes it more prominent
            # in the audit report, it does not change the merge decision.
            own_key = FS.canon_key(ent_in, pred_in, aliases)
            merge_items.append({"from": [own_key[0], own_key[1]],
                                 "to": [ce, cp], "band": "review"})
            ce, cp = own_key
            review_count += 1
        elif band == "auto":
            own_key = FS.canon_key(ent_in, pred_in, aliases)
            merge_items.append({"from": [own_key[0], own_key[1]],
                                 "to": [ce, cp], "band": "auto"})
        bands[band] += 1
        if (ce, cp) not in existing:
            existing.append((ce, cp))
        asserted_at = T._timestamp(event_date) if event_date else now_iso
        aid = FS.assertion_id(ce, cp, value, source, f"chunk:{cid}")
        if aid in existing_ids:
            # Already ingested (idempotent re-onboard): drop before linking/ledger
            # so a re-run with reworded evidence never triggers the content-mismatch
            # raise in temporal_memory._insert_immutable.
            continue
        sentiment = it.get("sentiment", "neutral")
        if sentiment not in FS.SENTIMENTS:
            sentiment = "neutral"
        assertions.append({
            "assertion_id": aid, "entity": ce, "predicate": cp, "value": value,
            "asserted_at": asserted_at, "ingested_at": now_iso, "source": source,
            "segment_id": f"chunk:{cid}", "evidence": it.get("evidence", ""),
            "sentiment": sentiment, "stance": it.get("stance", "")})

    if strict_merges and review_count:
        print(f"[strict-merges] {review_count} review-band pairs left distinct "
              "(needs human alias decision)", file=sys.stderr)

    # Pass 2: link in chronological order so a later fact supersedes an earlier one
    # even within the same batch. Priors = DB rows + in-batch assertions seen so far.
    assertions.sort(key=lambda a: a["asserted_at"])
    priors_cache = {}
    all_auto, disagreements = [], []
    supersede_items = []
    prior_by_id = {}
    for a in assertions:
        key = (a["entity"], a["predicate"])
        if key not in priors_cache:
            priors_cache[key] = find_priors(con, *key)  # DB priors, once per key
        for p in priors_cache[key]:
            prior_by_id[p["assertion_id"]] = {"value": p["value"], "entity": key[0], "predicate": key[1]}
        auto, dis = plan_links(a["assertion_id"], a["value"], a["asserted_at"], priors_cache[key])
        for new_id, _rel, prior_id in auto:
            prior_info = prior_by_id.get(prior_id, {})
            supersede_items.append({
                "new_id": new_id, "prior_id": prior_id,
                "entity": key[0], "predicate": key[1],
                "new_value": a["value"], "prior_value": prior_info.get("value")})
        all_auto += auto; disagreements += dis
        priors_cache[key].append({"assertion_id": a["assertion_id"],
                                  "value": a["value"], "asserted_at": a["asserted_at"]})
    judged, unresolved = judge_disagreements(disagreements, judge_fn)
    judge_items = [{"new_id": rel[0], "prior_id": rel[2], "relation": rel[1]} for rel in judged]
    links = all_auto + judged
    report = {"assertions": len(assertions), "merges": bands,
              "auto_supersedes": len(all_auto),
              "judge": {"resolved": len(judged), "unresolved": len(unresolved)},
              "merge_items": merge_items,
              "supersede_items": supersede_items,
              "judge_items": judge_items}
    if apply:
        ledger = FS.build_ledger(assertions, links)
        T.load_ledger(con, ledger); con.commit()
    return report


def _bedrock_judge(model):
    """Return a callable(dis) -> {"relation": ...} backed by Bedrock. Lazy boto3 import."""

    def _call(dis):
        import boto3  # noqa: PLC0415
        client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        prompt = (
            "Two assertions about the same entity/predicate disagree.\n"
            f"New value: {dis['new_value']!r} (assertion {dis['new_id']})\n"
            f"Prior value: {dis['prior_value']!r} (assertion {dis['prior_id']})\n"
            "Does the new value supersede the prior, contradict it, or should both be kept "
            "(e.g. different scope/opinion)? "
            'Answer ONLY as JSON: {"relation": "supersedes"|"contradicts"|"keep_both"}'
        )
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": prompt}],
        })
        resp = client.invoke_model(modelId=model_id, body=body)
        payload = json.loads(resp["body"].read())
        text = payload["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        return json.loads(text)

    return _call


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--results", required=True)
    ap.add_argument("--aliases"); ap.add_argument("--report")
    ap.add_argument("--apply", action="store_true"); ap.add_argument("--strict-merges", action="store_true")
    ap.add_argument("--merge-high", type=float, default=0.90); ap.add_argument("--merge-low", type=float, default=0.75)
    ap.add_argument("--model", default="BAAI/bge-small-en-v1.5")
    a = ap.parse_args(argv)
    from datetime import datetime, timezone
    import knowledge_index as KI
    aliases = json.load(open(a.aliases)) if a.aliases else {}
    judge = _bedrock_judge(a.model)
    con = sqlite3.connect(a.db)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    report = run(con, a.results, aliases, KI.embed, judge, a.merge_high, a.merge_low,
                 now_iso, apply=a.apply, strict_merges=a.strict_merges, model=a.model)
    if a.report:
        json.dump(report, open(a.report, "w"), indent=1)
    print(json.dumps(report), file=sys.stderr)


if __name__ == "__main__":
    main()
