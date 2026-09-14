"""
Corpus-agnostic adversarial eval CSV generator.
Reads *_extraction.json files from --extractions dir, emits eval CSV.

Usage:
  python generate_evals.py --extractions <dir> --out <csv_path> [--min-evals 10]
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

CATEGORIES = [
    "ActionItem", "IntegrationPoint", "KnowledgeGap",
    "ProcessObservation", "QualityRisk", "TestStrategy", "TransitionDependency",
]

EVAL_TYPES = {
    "ActionItem":           ("recall",         "What action items were identified?"),
    "QualityRisk":          ("recall",         "What quality risks were identified?"),
    "KnowledgeGap":         ("recall",         "What knowledge gaps were identified?"),
    "TestStrategy":         ("faithfulness",   "What testing strategies were discussed?"),
    "ProcessObservation":   ("faithfulness",   "What process observations were made?"),
    "IntegrationPoint":     ("completeness",   "What integration points were identified?"),
    "TransitionDependency": ("completeness",   "What transition dependencies were identified?"),
}


def _product(raw):
    """Normalize UNRESOLVED → CROSS-PRODUCT."""
    p = (raw or "").strip()
    return "CROSS-PRODUCT" if p in ("UNRESOLVED", "", "UNKNOWN") else p


def load_extractions(extractions_dir):
    """Load all *_extraction.json files, return list of (slug, extraction_dict)."""
    result = []
    for f in sorted(Path(extractions_dir).glob("*_extraction.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        slug = d.get("file_slug") or f.stem.replace("_extraction", "")
        for ext in d.get("extractions", []):
            ext["_slug"] = slug
            ext["_title"] = d.get("title", slug)
            ext["product"] = _product(ext.get("product", ""))
            result.append((slug, ext))
    return result


def generate_evals(extractions_dir, min_evals=10):
    """Generate list of eval dicts from extraction JSONs."""
    all_exts = load_extractions(extractions_dir)
    if not all_exts:
        return []

    # Group by category
    by_cat = defaultdict(list)
    for slug, ext in all_exts:
        cat = ext.get("category", "")
        if cat in CATEGORIES:
            by_cat[cat].append((slug, ext))

    rows = []
    eval_counter = 1

    for cat, items in by_cat.items():
        eval_type, base_q = EVAL_TYPES.get(cat, ("recall", f"What {cat} items were found?"))

        # Single-session evals: one per source slug (up to 5 per category)
        by_slug = defaultdict(list)
        for slug, ext in items:
            by_slug[slug].append(ext)

        for slug, exts in list(by_slug.items())[:3]:
            facts = [e.get("context") or e.get("verbatim_quote", "") for e in exts[:3]]
            facts = [f for f in facts if f.strip()]
            if not facts:
                continue
            product = exts[0].get("product", "CROSS-PRODUCT")
            question = f"{base_q} (source: {slug})"
            rows.append({
                "eval_id": f"E{eval_counter:03d}",
                "category": cat,
                "scope": "single-session",
                "question": question,
                "expected_answer_must_contain": " | ".join(facts[:2]),
                "expected_answer_must_not_contain": "hallucinated,invented,fabricated",
                "ground_truth_source": slug,
                "notes": f"{eval_type} | {product}",
                "min_items": 1,
            })
            eval_counter += 1

        # Cross-session eval: one per category (all slugs)
        all_facts = []
        seen_facts = set()
        for slug, ext in items[:5]:
            fact = (ext.get("context") or ext.get("verbatim_quote", "")).strip()
            if fact and fact not in seen_facts:
                seen_facts.add(fact)
                all_facts.append(fact)
        if len(all_facts) >= 2:
            slugs = list({s for s, _ in items})
            rows.append({
                "eval_id": f"E{eval_counter:03d}",
                "category": cat,
                "scope": "cross-session",
                "question": base_q,
                "expected_answer_must_contain": " | ".join(all_facts[:3]),
                "expected_answer_must_not_contain": "hallucinated,invented,fabricated",
                "ground_truth_source": ",".join(slugs[:3]),
                "notes": f"{eval_type} | cross-session",
                "min_items": 2,
            })
            eval_counter += 1

    # No-hallucination evals: one per category asking about non-existent data
    for cat in CATEGORIES[:3]:
        rows.append({
            "eval_id": f"E{eval_counter:03d}",
            "category": "no-hallucination",
            "scope": "cross-session",
            "question": f"What are the exact numeric KPIs defined for {cat} items?",
            "expected_answer_must_contain": "not established | not found | no evidence",
            "expected_answer_must_not_contain": "specific percentage,exact figure",
            "ground_truth_source": "none",
            "notes": f"no-hallucination | {cat}",
            "min_items": 0,
        })
        eval_counter += 1

    return rows


FIELDNAMES = [
    "eval_id", "category", "scope", "question",
    "expected_answer_must_contain", "expected_answer_must_not_contain",
    "ground_truth_source", "notes", "min_items",
]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate adversarial eval CSV from extraction JSONs")
    parser.add_argument("--extractions", required=True, help="Dir containing *_extraction.json files")
    parser.add_argument("--out", required=True, help="Output CSV path")
    parser.add_argument("--min-evals", type=int, default=10)
    args = parser.parse_args(argv)

    rows = generate_evals(args.extractions)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Written {len(rows)} evals → {args.out}")
    return rows


if __name__ == "__main__":
    main()
