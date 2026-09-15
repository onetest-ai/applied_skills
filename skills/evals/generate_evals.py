"""
Corpus-agnostic adversarial eval CSV generator.
Reads *_extraction.json files from --extractions dir and a taxonomy JSON,
emits eval CSV.

Usage:
  python generate_evals.py --extractions <dir> --taxonomy <taxonomy.json> --out <csv_path>

The taxonomy JSON must contain an intent_taxonomy.eval_config dict mapping
category name → {eval_type, question, query_suffix}.  Example:
  {
    "intent_taxonomy": {
      "l1": ["ActionItem", ...],
      "eval_config": {
        "ActionItem": {"eval_type": "recall", "question": "...", "query_suffix": "..."},
        ...
      }
    }
  }
"""
import argparse
import csv
import json
import warnings
from collections import defaultdict
from pathlib import Path

DEFAULT_QUERY_SUFFIX = "specific details findings decisions evidence"
DEFAULT_TAXONOMY = Path(__file__).parent / "taxonomy.default.json"


def load_taxonomy(taxonomy_path):
    """Return (categories_list, eval_config_dict) from taxonomy JSON."""
    d = json.loads(Path(taxonomy_path).read_text(encoding="utf-8"))
    it = d.get("intent_taxonomy", {})
    categories = it.get("l1", [])
    eval_config = it.get("eval_config", {})
    return categories, eval_config


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


def load_from_db(db_path, taxonomy_path=None):
    """Generate eval rows by reading chunk_topics + chunks from a knowledge SQLite.

    No extraction JSON files required. Categories come from chunk_topics.category_label.
    taxonomy_path (optional): if given, load eval_config for question/query_suffix overrides.
    Returns list of eval row dicts with the same schema as generate_evals().
    """
    import sqlite3

    eval_config = {}
    if taxonomy_path:
        _, eval_config = load_taxonomy(taxonomy_path)

    c = sqlite3.connect(db_path)

    categories = [
        r[0] for r in c.execute(
            "SELECT DISTINCT category_label FROM chunk_topics ORDER BY category_label"
        ).fetchall()
    ]

    rows = []
    eval_counter = 1

    for cat in categories:
        cfg = eval_config.get(cat, {})
        question_tmpl = cfg.get("question", "What {} content was discussed?".format(cat))
        query_suffix = cfg.get("query_suffix", "")

        chunk_rows = c.execute(
            "SELECT c.source, c.text FROM chunk_topics ct "
            "JOIN chunks c ON c.id = ct.chunk_id "
            "WHERE ct.category_label = ? "
            "AND COALESCE(c.status, 'ACTIVE') = 'ACTIVE' "
            "ORDER BY c.source, c.ord LIMIT 15",
            (cat,),
        ).fetchall()

        if not chunk_rows:
            continue

        # Group by source
        by_slug = {}
        for source, text in chunk_rows:
            slug = source.replace(".vtt.md", "").replace(".srt.md", "").replace(".md", "")
            # strip to basename for brevity
            slug = slug.split("/")[-1].split("\\")[-1]
            by_slug.setdefault(slug, []).append(text)

        # Single-session evals: up to 3 sources
        for slug, texts in list(by_slug.items())[:3]:
            # Use source slug as expected fact — the LLM answer will reference the source
            # name when the brain retrieves the right chunk (chunk headers contain the slug).
            # Raw chunk text prefixes are ASR fragments that no rubric can match reliably.
            if not texts:
                continue
            rows.append({
                "eval_id": "E{:03d}".format(eval_counter),
                "category": cat,
                "scope": "single-session",
                "question": "{} (source: {})".format(question_tmpl, slug),
                "query_suffix": query_suffix,
                "expected_answer_must_contain": slug,
                "expected_answer_must_not_contain": "hallucinated,invented,fabricated",
                "ground_truth_source": slug,
                "notes": "db-mode | {}".format(cat),
                "min_items": 1,
            })
            eval_counter += 1

        # Cross-session eval when >= 2 sources
        if len(by_slug) >= 2:
            cross_slugs = list(by_slug.keys())[:3]
            if len(cross_slugs) >= 2:
                rows.append({
                    "eval_id": "E{:03d}".format(eval_counter),
                    "category": cat,
                    "scope": "cross-session",
                    "question": question_tmpl,
                    "query_suffix": query_suffix,
                    "expected_answer_must_contain": " | ".join(cross_slugs),
                    "expected_answer_must_not_contain": "hallucinated,invented,fabricated",
                    "ground_truth_source": ",".join(cross_slugs),
                    "notes": "db-mode | cross-session",
                    "min_items": 2,
                })
                eval_counter += 1

    # No-hallucination evals: first 3 categories
    for cat in categories[:3]:
        rows.append({
            "eval_id": "E{:03d}".format(eval_counter),
            "category": "no-hallucination",
            "scope": "cross-session",
            "question": "What are the exact numeric KPIs defined for {} items?".format(cat),
            "query_suffix": DEFAULT_QUERY_SUFFIX,
            "expected_answer_must_contain": "not established | not found | no evidence",
            "expected_answer_must_not_contain": "specific percentage,exact figure",
            "ground_truth_source": "none",
            "notes": "no-hallucination | {}".format(cat),
            "min_items": 0,
        })
        eval_counter += 1

    c.close()
    return rows


def generate_evals(extractions_dir, taxonomy_path):
    """Generate list of eval dicts from extraction JSONs and taxonomy."""
    categories, eval_config = load_taxonomy(taxonomy_path)
    all_exts = load_extractions(extractions_dir)
    if not all_exts:
        return []

    # Group by category; warn on extractions whose category is absent from taxonomy
    by_cat = defaultdict(list)
    unknown_cats: set = set()
    for slug, ext in all_exts:
        cat = ext.get("category", "")
        if cat in categories:
            by_cat[cat].append((slug, ext))
        elif cat:
            unknown_cats.add(cat)
    for cat in sorted(unknown_cats):
        warnings.warn(
            f"Category '{cat}' in extractions not found in taxonomy l1 — skipped. "
            f"Add it to your taxonomy JSON to generate evals for it.",
            stacklevel=2,
        )

    rows = []
    eval_counter = 1

    for cat, items in by_cat.items():
        cfg = eval_config.get(cat, {})
        if not cfg:
            warnings.warn(
                f"Category '{cat}' has no eval_config entry in taxonomy — "
                f"using generic fallbacks. Add eval_config.{cat} to your taxonomy JSON.",
                stacklevel=2,
            )
        eval_type = cfg.get("eval_type", "recall")
        base_q = cfg.get("question", f"What {cat} items were found?")
        query_suffix = cfg.get("query_suffix", DEFAULT_QUERY_SUFFIX)

        # Single-session evals: one per source slug (up to 3 per category)
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
                "query_suffix": query_suffix,
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
                "query_suffix": query_suffix,
                "expected_answer_must_contain": " | ".join(all_facts[:3]),
                "expected_answer_must_not_contain": "hallucinated,invented,fabricated",
                "ground_truth_source": ",".join(slugs[:3]),
                "notes": f"{eval_type} | cross-session",
                "min_items": 2,
            })
            eval_counter += 1

    # No-hallucination evals: one per category actually present in corpus (not taxonomy order)
    for cat in list(by_cat.keys())[:3]:
        rows.append({
            "eval_id": f"E{eval_counter:03d}",
            "category": "no-hallucination",
            "scope": "cross-session",
            "question": f"What are the exact numeric KPIs defined for {cat} items?",
            "query_suffix": eval_config.get(cat, {}).get("query_suffix", DEFAULT_QUERY_SUFFIX),
            "expected_answer_must_contain": "not established | not found | no evidence",
            "expected_answer_must_not_contain": "specific percentage,exact figure",
            "ground_truth_source": "none",
            "notes": f"no-hallucination | {cat}",
            "min_items": 0,
        })
        eval_counter += 1

    return rows


FIELDNAMES = [
    "eval_id", "category", "scope", "question", "query_suffix",
    "expected_answer_must_contain", "expected_answer_must_not_contain",
    "ground_truth_source", "notes", "min_items",
]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate adversarial eval CSV from extraction JSONs or knowledge DB"
    )
    parser.add_argument("--extractions", help="Dir containing *_extraction.json files")
    parser.add_argument("--db",          help="Path to knowledge.sqlite (chunk_topics + chunks)")
    parser.add_argument("--taxonomy",    default=str(DEFAULT_TAXONOMY),
                        help="Taxonomy JSON with intent_taxonomy.eval_config (default: {})".format(
                            DEFAULT_TAXONOMY.name))
    parser.add_argument("--out",         required=True, help="Output CSV path")
    args = parser.parse_args(argv)

    if args.db and args.extractions:
        parser.error("--db and --extractions are mutually exclusive — use one or the other")
    if not args.db and not args.extractions:
        parser.error("one of --db or --extractions is required")

    if args.db:
        # Optional taxonomy for question/query_suffix overrides; None = use generic fallbacks
        taxo = args.taxonomy if Path(args.taxonomy).exists() else None
        rows = load_from_db(args.db, taxo)
    else:
        rows = generate_evals(args.extractions, args.taxonomy)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print("Written {} evals -> {}".format(len(rows), args.out))
    return rows


if __name__ == "__main__":
    main()
