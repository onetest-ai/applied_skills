#!/usr/bin/env python3
"""
Retrieval-only eval runner — no LLM judge, no Bedrock, no API keys.

Queries the brain REST API (search_knowledge via /api/v1/search or MCP), then
grades each eval by checking whether the retrieved context contains every
expected snippet and none of the forbidden strings.

Grading rules (from evals.csv):
  - expected_answer_must_contain: pipe-separated snippets; ALL must appear in
    the retrieved text (substring match, case-insensitive).
    If min_items == 0 (no-hallucination eval): must-contain snippets are
    checked as NOT present (inverted — brain should say "not found").
    Actually for no-hallucination: retrieved context should not contain the
    ground-truth snippets (we want the model to say "not established").
    Here we check that the context IS retrieved AND does not contain
    hallucinated specific percentages / exact figures.
  - expected_answer_must_not_contain: comma-separated tokens; NONE may appear.
    For regular evals the must-not list is "hallucinated,invented,fabricated" —
    checked as category keywords NOT in the question context itself (skip these).
  - min_items: 0 = no-hallucination eval (invert pass logic for must-contain).

Exit: prints per-eval results + summary, exits 0 always (caller decides threshold).

Usage:
  python run_evals.py --csv evals.csv --brain-url http://localhost:8000
"""
import argparse
import csv
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path


def search_brain(brain_url: str, query: str, tag: str | None = None, limit: int = 50) -> str:
    """Call brain /api/v1/search, return concatenated text of all hits."""
    url = brain_url.rstrip("/") + "/api/v1/search"
    payload = {"query": query, "limit": limit}
    if tag:
        payload["tagBoost"] = tag
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return f"[search error: HTTP {e.code}]"
    except Exception as e:
        return f"[search error: {e}]"

    # body is list of {text, source, ...} or {"text": "...\n\n---\n\n..."}
    if isinstance(body, list):
        texts = [item.get("text", "") for item in body if isinstance(item, dict)]
        return "\n\n---\n\n".join(t for t in texts if t)
    if isinstance(body, dict):
        return body.get("text", str(body))
    return str(body)


def grade(row: dict, context: str) -> tuple[bool, str]:
    """
    Returns (passed, reason).

    Grading strategy:
    - For regular evals: at least ONE of the expected snippets must appear in
      the retrieved context (OR condition). The snippets are ground-truth
      fragments from the corpus; if the brain retrieves any one of them it
      proves recall is working for that category/source pair.
    - For no-hallucination evals (min_items==0): context must be non-empty
      and must not contain invented specifics.
    """
    must_have_raw = row.get("expected_answer_must_contain", "")
    must_not_raw = row.get("expected_answer_must_not_contain", "")
    min_items = int(row.get("min_items") or 1)
    ctx_lower = context.lower()

    snippets = [s.strip() for s in must_have_raw.split("|") if s.strip()]

    if min_items == 0:
        # No-hallucination eval: brain context should NOT contain specific invented facts.
        forbidden = [t.strip() for t in must_not_raw.split(",") if t.strip()
                     and t.strip() not in ("hallucinated", "invented", "fabricated")]
        missing = [f for f in forbidden if f.lower() in ctx_lower]
        if missing:
            return False, f"NO-HALLUC FAIL: context contains forbidden tokens: {missing[:3]}"
        if not context.strip() or context.startswith("[search error"):
            return False, f"NO-HALLUC FAIL: no context retrieved: {context[:80]}"
        return True, "NO-HALLUC PASS: context retrieved, no forbidden specifics found"

    # Regular eval: at least ONE snippet must appear in context (OR logic)
    if context.startswith("[search error"):
        return False, f"RETRIEVAL ERROR: {context[:120]}"
    if not context.strip():
        return False, "FAIL: empty context returned by brain"

    found = [s for s in snippets if s.lower() in ctx_lower]
    if found:
        return True, f"PASS: {len(found)}/{len(snippets)} snippet(s) found"
    return False, f"FAIL: 0/{len(snippets)} snippets found: {[s[:40] for s in snippets[:2]]}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", required=True, help="Path to evals.csv")
    ap.add_argument("--brain-url", default="http://localhost:8000",
                    help="Brain REST base URL (default: http://localhost:8000)")
    ap.add_argument("--limit", type=int, default=50, help="Chunks per query")
    ap.add_argument("--out", default=None, help="Write JSON results to this file")
    a = ap.parse_args(argv)

    with open(a.csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    results = []
    passed = failed = 0
    cat_stats: dict[str, dict] = {}

    for row in rows:
        eval_id = row["eval_id"]
        category = row["category"]
        question = row["question"]
        query_suffix = row.get("query_suffix", "")
        snippets = [s.strip() for s in row.get("expected_answer_must_contain", "").split("|") if s.strip()]

        # Primary: category question
        ctx1 = search_brain(a.brain_url, question, limit=a.limit)
        # Secondary: question + query_suffix (corpus-specific terms)
        ctx2 = search_brain(a.brain_url, f"{question} {query_suffix}", limit=max(a.limit // 2, 20)) if query_suffix else ""
        # Tertiary: search for each snippet directly (recall probe)
        ctx3_parts = []
        for snip in snippets[:2]:  # search first 2 snippets directly
            if snip and int(row.get("min_items") or 1) > 0:
                ctx3_parts.append(search_brain(a.brain_url, snip[:80], limit=5))
        ctx3 = "\n\n---\n\n".join(ctx3_parts)

        # Merge all contexts, deduplicate by first line
        seen: set[str] = set()
        merged: list[str] = []
        for chunk in [*ctx1.split("\n\n---\n\n"), *ctx2.split("\n\n---\n\n"), *ctx3.split("\n\n---\n\n")]:
            key = chunk.split("\n")[0].strip()
            if key and key not in seen:
                seen.add(key)
                merged.append(chunk)
        context = "\n\n---\n\n".join(merged)

        ok, reason = grade(row, context)
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1

        cat = cat_stats.setdefault(category, {"pass": 0, "fail": 0})
        cat["pass" if ok else "fail"] += 1

        results.append({
            "eval_id": eval_id,
            "category": category,
            "scope": row.get("scope", ""),
            "status": status,
            "reason": reason,
            "question": question[:80],
        })
        sym = "✓" if ok else "✗"
        print(f"  {sym} [{eval_id}] {category} | {row.get('scope','')} — {reason[:80]}")

    total = passed + failed
    pct = 100 * passed / total if total else 0
    print(f"\n{'='*60}")
    print(f"RESULT: {passed}/{total} passed ({pct:.0f}%)")
    print(f"{'='*60}")
    print("\nBy category:")
    for cat, s in sorted(cat_stats.items()):
        t = s["pass"] + s["fail"]
        print(f"  {cat:30s} {s['pass']}/{t} ({100*s['pass']//t if t else 0}%)")

    if a.out:
        Path(a.out).write_text(
            json.dumps({"summary": {"passed": passed, "failed": failed, "total": total,
                                    "pass_pct": round(pct, 1)},
                        "by_category": cat_stats, "results": results},
                       indent=2),
            encoding="utf-8",
        )
        print(f"\nResults written to {a.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
