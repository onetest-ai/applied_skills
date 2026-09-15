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


def build_rubric(row):
    question = row["question"]
    category = row["category"]
    scope = row["scope"]
    must_have = row["expected_answer_must_contain"]
    must_not = row["expected_answer_must_not_contain"]
    source = row["ground_truth_source"]
    notes = row["notes"]
    min_items = int(row.get("min_items") or 1)

    bullets = "\n".join(
        f"  - {p.strip()}"
        for p in must_have.split("|")
        if p.strip()
    )

    rubric = f"Question: {question}\nCategory: {category} | Scope: {scope}\n"
    if min_items == 0:
        rubric += (
            "This is a NO-HALLUCINATION eval. The answer must acknowledge absence of data.\n"
            f"The answer SHOULD convey:\n{bullets}\n"
        )
    else:
        rubric += (
            f"The answer MUST semantically cover ALL of these specific facts "
            f"(paraphrasing acceptable but must be concrete — generic answers FAIL):\n"
            f"{bullets}\n"
        )
    rubric += (
        f"\nThe answer must NOT contain or invent: {must_not}\n"
        f"\nSource ground truth: {source}\n"
        f"Notes: {notes}\n"
        f"\nIMPORTANT: Grade PASS only if EVERY required fact is specifically addressed "
        f"with concrete detail. A generic answer that mentions the topic without the "
        f"specific fact FAILS."
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
        }
        if row.get("query_suffix"):
            vars_["query_suffix"] = row["query_suffix"]
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
