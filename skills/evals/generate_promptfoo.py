"""
Corpus-agnostic promptfoo YAML generator.
Reads eval CSV, emits promptfooconfig.yaml pointing at any brain REST shim.

Usage:
  python generate_promptfoo.py --csv <path> --out <yaml_path>
      --brain-url http://localhost:8002
      --context-js <path_to_load_brain_context.js>
"""
import argparse
import csv
import yaml
from pathlib import Path

BEDROCK_CONFIG = {"region": "us-east-1"}

PROVIDERS = [
    {"id": "bedrock:us.anthropic.claude-haiku-4-5-20251001-v1:0",
     "label": "brain + claude-haiku-4.5 (low)", "config": BEDROCK_CONFIG},
    {"id": "bedrock:us.anthropic.claude-sonnet-4-6",
     "label": "brain + claude-sonnet-4.6 (med)", "config": BEDROCK_CONFIG},
    {"id": "bedrock:us.anthropic.claude-opus-4-8",
     "label": "brain + claude-opus-4.8 (high)", "config": BEDROCK_CONFIG},
]

PROMPT_TEMPLATE = """\
You answer questions using the retrieved knowledge-brain context below.

Rules:
- Use only facts supported by the context.
- Answer the specific question directly and synthesize across relevant sources.
- If the context lacks the requested fact, say it is not established in the retrieved evidence.
- Do not invent owners, dates, metrics, or decisions.

<question>
{{question}}
</question>

<retrieved_context>
{{context}}
</retrieved_context>"""


def _derive_query_terms(must_have_raw):
    """Extract up to 8 meaningful keywords (>3 chars) from the first 2 pipe-separated facts."""
    terms = []
    for fact in must_have_raw.split("|")[:2]:
        words = fact.strip().split()[:6]
        terms.extend(w for w in words if len(w) > 3)
    seen = set()
    deduped = []
    for w in terms:
        if w.lower() not in seen:
            seen.add(w.lower())
            deduped.append(w)
    return " ".join(deduped[:8])


def build_rubric(row):
    question = row["question"]
    category = row["category"]
    scope = row["scope"]
    must_have = row["expected_answer_must_contain"]
    must_not = row["expected_answer_must_not_contain"]
    source = row["ground_truth_source"]
    notes = row["notes"]
    min_items = int(row.get("min_items") or 1)

    fact_list = [p.strip() for p in must_have.split("|") if p.strip()]
    n_facts = len(fact_list)

    bullets = "\n".join("  - {}".format(f) for f in fact_list)

    # N-1 of N threshold: require at least (n-1) facts, minimum 1
    threshold = max(1, n_facts - 1)

    rubric = "Question: {}\nCategory: {} | Scope: {}\n".format(question, category, scope)
    if min_items == 0:
        rubric += (
            "This is a NO-HALLUCINATION eval. The answer must acknowledge absence of data.\n"
            "The answer SHOULD convey:\n{}\n".format(bullets)
        )
    else:
        if threshold >= n_facts:
            threshold_text = "ALL {}".format(n_facts)
        else:
            threshold_text = "AT LEAST {} of {}".format(threshold, n_facts)
        rubric += (
            "The answer MUST semantically cover {} of these facts "
            "(paraphrasing acceptable but must be concrete — generic answers FAIL):\n"
            "{}\n".format(threshold_text, bullets)
        )

    if threshold >= n_facts:
        grade_instruction = (
            "Grade PASS only if ALL facts are specifically addressed with concrete detail."
        )
    else:
        grade_instruction = (
            "Grade PASS if AT LEAST {} of the {} facts are addressed with concrete detail. "
            "Missing 1 secondary fact is acceptable.".format(threshold, n_facts)
        )

    rubric += (
        "\nThe answer must NOT contain or invent: {}\n"
        "\nSource ground truth: {}\n"
        "Notes: {}\n"
        "\n{}".format(must_not, source, notes, grade_instruction)
    )
    return rubric


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--brain-url", default="http://localhost:8002")
    parser.add_argument("--context-js", required=True,
                        help="Path to load_brain_context.js (becomes file:// var)")
    args = parser.parse_args(argv)

    js_path = Path(args.context_js).resolve()
    js_file_ref = f"file://{js_path}"

    with open(args.csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    tests = []
    for row in rows:
        vars_ = {
            "question": row["question"],
            "context": js_file_ref,
            "BRAIN_URL": args.brain_url,
        }
        existing_suffix = row.get("query_suffix", "").strip()
        derived = _derive_query_terms(row.get("expected_answer_must_contain", ""))
        vars_["query_suffix"] = "{} {}".format(existing_suffix, derived).strip() if existing_suffix else derived
        tests.append({
            "description": f"[{row['eval_id']}] {row['category']} | {row['scope']} | {row['question'][:60]}",
            "vars": vars_,
            "assert": [{"type": "llm-rubric", "value": build_rubric(row)}],
            "metadata": {
                "eval_id": row["eval_id"],
                "category": row["category"],
                "scope": row["scope"],
                "ground_truth_source": row["ground_truth_source"],
            },
        })

    config = {
        "description": f"Brain evals — {len(tests)} adversarial tests · judge: Sonnet 4.6",
        "prompts": [PROMPT_TEMPLATE],
        "providers": PROVIDERS,
        "defaultTest": {
            "options": {
                "provider": {
                    "id": "bedrock:us.anthropic.claude-sonnet-4-6",
                    "config": BEDROCK_CONFIG,
                }
            }
        },
        "tests": tests,
    }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        yaml.dump(config, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    print(f"Written {len(tests)} tests → {args.out}")


if __name__ == "__main__":
    main()
