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

# Fixed no-hallucination probes — questions targeting data confirmed absent from any corpus.
# Module-level so both load_from_db() and generate_evals() stay in sync.
_NO_HALLUC = [
    ("What are the contractor day rates or salary figures for the EPAM team members?",
     "salary contractor rates team compensation", "no-hallucination | absent-salaries"),
    ("What is the approved annual budget in EUR or USD for the performance testing engagement?",
     "budget EUR USD annual approved financial cost", "no-hallucination | absent-budget"),
    ("What Gatling Enterprise license fees or LoadRunner license costs are recorded?",
     "Gatling LoadRunner license fee cost annual", "no-hallucination | absent-license-cost"),
]

_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "that", "this", "which", "who", "what",
    "how", "is", "are", "was", "were", "be", "been", "being", "have",
    "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "may", "might", "shall", "can", "not", "us", "we", "it", "its",
    "as", "so", "if", "then", "when", "where", "there", "here", "up",
    "out", "into", "about", "also", "than", "me", "my", "our", "their",
    "them", "they", "he", "she", "his", "her", "you", "your",
})


def _query_suffix_from_fact(verbatim_quote: str) -> str:
    """Extract searchable keywords from a verbatim quote for BM25 retrieval."""
    words = verbatim_quote.replace(",", " ").replace(".", " ").replace(";", " ").split()
    keywords = [
        w.strip("'\"()[]") for w in words
        if len(w) > 3 and w.lower().strip("'\"()[]") not in _STOP_WORDS
    ]
    seen: set = set()
    deduped = []
    for w in keywords:
        key = w.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(w)
    return " ".join(deduped[:8])


def _question_from_fact(verbatim_quote: str, slug: str) -> str:
    """Build a retrieval-friendly question from a verbatim fact and source slug."""
    suffix = _query_suffix_from_fact(verbatim_quote)
    if suffix:
        return f"What was discussed about {suffix} (source: {slug})?"
    return f"What was discussed in this meeting (source: {slug})?"


def load_taxonomy(taxonomy_path):
    """Return (categories_list, eval_config_dict) from taxonomy JSON."""
    d = json.loads(Path(taxonomy_path).read_text(encoding="utf-8"))
    it = d.get("intent_taxonomy", {})
    categories = it.get("l1", [])
    eval_config = it.get("eval_config", {})
    return categories, eval_config


def load_probe_evals(taxonomy_path):
    """Return list of probe eval rows from taxonomy.probe_evals[], or []."""
    d = json.loads(Path(taxonomy_path).read_text(encoding="utf-8"))
    rows = []
    for p in d.get("probe_evals", []):
        rows.append({
            "eval_id":                          p.get("eval_id", "PROBE_???"),
            "category":                         p.get("category", "probe"),
            "scope":                            p.get("scope", "probe"),
            "question":                         p.get("question", ""),
            "query_suffix":                     p.get("query_suffix", ""),
            "expected_answer_must_contain":     p.get("expected_answer_must_contain",
                                                     p.get("expected_must_contain", "")),
            "expected_answer_must_not_contain": p.get("expected_answer_must_not_contain",
                                                     p.get("expected_must_not_contain", "")),
            "ground_truth_source":              p.get("ground_truth_source", ""),
            "notes":                            p.get("notes", ""),
            "min_items":                        p.get("min_items", 1),
        })
    return rows


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
            if not any(t.strip() for t in texts):
                continue
            # Use slug + a concrete keyword from the first non-empty chunk as the two
            # must_contain options.  "not established in slug" was the prior option but
            # it gives LLM judges a false prior: seeing "not established" in the rubric,
            # judges infer the source is empty and then fail any answer with specific
            # numbers as "hallucinated" — even when those numbers are real and present.
            # Fix: use an actual keyword from the chunk so the judge has verifiable ground truth.
            first_text = next((t for t in texts if t.strip()), "")
            # Extract a concrete keyword from the chunk for the grader — strip markdown
            # punctuation so we don't pass ``` or ** as "facts".
            _candidate = ""
            if first_text:
                for word in _query_suffix_from_fact(first_text).split():
                    clean = word.strip("`*_#|>")
                    if len(clean) > 3 and clean.isalpha():
                        _candidate = clean
                        break
            must_contain = "{} | {}".format(slug, _candidate) if _candidate else slug
            rows.append({
                "eval_id": "E{:03d}".format(eval_counter),
                "category": cat,
                "scope": "single-session",
                "question": "{} (source: {})".format(question_tmpl, slug),
                "query_suffix": query_suffix,
                "expected_answer_must_contain": must_contain,
                "expected_answer_must_not_contain": "hallucinated,invented,fabricated",
                "ground_truth_source": slug,
                "notes": "db-mode | {}".format(cat),
                "min_items": 1,
            })
            eval_counter += 1

        # Cross-session eval when >= 2 sources
        # Cap at 2 slugs: with 3 slugs the N-1 threshold = 2, which requires naming two long
        # session titles — a formality the rubric can't reliably enforce on paraphrased answers.
        if len(by_slug) >= 2:
            cross_slugs = list(by_slug.keys())[:2]
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

    # No-hallucination evals — use the module-level _NO_HALLUC constant.
    # Do NOT use per-category KPI questions — many categories (MetricOrKPI, MigrationStatus)
    # contain real numeric data and will correctly surface it, causing false failures.
    for question, suffix, notes in _NO_HALLUC:
        rows.append({
            "eval_id": "E{:03d}".format(eval_counter),
            "category": "no-hallucination",
            "scope": "cross-session",
            "question": question,
            "query_suffix": suffix,
            "expected_answer_must_contain": "not established | not found | no evidence",
            "expected_answer_must_not_contain": "specific percentage,exact figure",
            "ground_truth_source": "none",
            "notes": notes,
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
            facts = [e.get("verbatim_quote", "") for e in exts[:3]]
            facts = [f for f in facts if f.strip()]
            if not facts:
                continue
            product = exts[0].get("product", "CROSS-PRODUCT")
            # Derive question and query_suffix from the primary verbatim fact
            primary_fact = facts[0]
            question = _question_from_fact(primary_fact, slug)
            fact_suffix = _query_suffix_from_fact(primary_fact)
            rows.append({
                "eval_id": f"E{eval_counter:03d}",
                "category": cat,
                "scope": "single-session",
                "question": question,
                "query_suffix": fact_suffix or query_suffix,
                "expected_answer_must_contain": " | ".join(facts[:2]),
                "expected_answer_must_not_contain": "hallucinated,invented,fabricated",
                "ground_truth_source": slug,
                "notes": f"{eval_type} | {product}",
                "min_items": 1,
            })
            eval_counter += 1

        # Cross-session eval: one per category (all slugs)
        all_facts = []
        seen_facts: set = set()
        for slug, ext in items[:5]:
            fact = ext.get("verbatim_quote", "").strip()
            if fact and fact not in seen_facts:
                seen_facts.add(fact)
                all_facts.append(fact)
        if len(all_facts) >= 2:
            slugs = list({s for s, _ in items})
            # Cross-session: derive question and query from combined fact keywords
            cross_suffix = _query_suffix_from_fact(" ".join(all_facts[:2]))
            cross_q = (f"What was discussed about {cross_suffix}?"
                       if cross_suffix else base_q)
            rows.append({
                "eval_id": f"E{eval_counter:03d}",
                "category": cat,
                "scope": "cross-session",
                "question": cross_q,
                "query_suffix": cross_suffix or query_suffix,
                "expected_answer_must_contain": " | ".join(all_facts[:3]),
                "expected_answer_must_not_contain": "hallucinated,invented,fabricated",
                "ground_truth_source": ",".join(slugs[:3]),
                "notes": f"{eval_type} | cross-session",
                "min_items": 2,
            })
            eval_counter += 1

    # No-hallucination evals — use the module-level _NO_HALLUC constant.
    # Do NOT use per-category KPI questions — many categories (MetricOrKPI, MigrationStatus)
    # contain real numeric data and will correctly surface it, causing false failures.
    for question, suffix, notes in _NO_HALLUC:
        rows.append({
            "eval_id": f"E{eval_counter:03d}",
            "category": "no-hallucination",
            "scope": "cross-session",
            "question": question,
            "query_suffix": suffix,
            "expected_answer_must_contain": "not established | not found | no evidence",
            "expected_answer_must_not_contain": "specific percentage,exact figure",
            "ground_truth_source": "none",
            "notes": notes,
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

    # Append probe evals from taxonomy (corpus-specific TDD probes)
    if Path(args.taxonomy).exists():
        rows.extend(load_probe_evals(args.taxonomy))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print("Written {} evals -> {}".format(len(rows), args.out))
    return rows


if __name__ == "__main__":
    main()
