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
import os
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


def build_persona_block(brain_context):
    """Build a project-context block from brain_context.persona + stakeholders.

    Returns empty string when brain_context is None or lacks both fields,
    so callers can safely prepend without special-casing the no-taxonomy path.
    """
    if not brain_context:
        return ""
    lines = []
    persona = brain_context.get("persona", "").strip()
    if persona:
        lines.append(persona)
    stakeholders = brain_context.get("stakeholders")
    if stakeholders:
        if lines:
            lines.append("")
        lines.append("Key stakeholders:")
        for name, role in stakeholders.items():
            lines.append("- {}: {}".format(name, role))
    return "\n".join(lines)


def build_prompt_template(persona_block=""):
    """Return the prompt template string, optionally prefixed with project context."""
    if not persona_block:
        return PROMPT_TEMPLATE
    return (
        "<project_context>\n"
        + persona_block
        + "\n</project_context>\n\n"
        + PROMPT_TEMPLATE
    )


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


def build_rubric(row, brain_context=None):
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
    if brain_context:
        goal = brain_context.get("goal", "")
        audience = brain_context.get("audience", "")
        if goal or audience:
            rubric = (
                "Brain goal: {}\nAudience: {}\n\n".format(goal, audience)
                + rubric
            )
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
    parser.add_argument("--taxonomy", default=None,
                        help="Optional taxonomy JSON with brain_context for persona injection")
    args = parser.parse_args(argv)

    brain_context = None
    if args.taxonomy:
        import json as _json
        _taxo = _json.loads(Path(args.taxonomy).read_text(encoding="utf-8"))
        brain_context = _taxo.get("brain_context")

    persona_block = build_persona_block(brain_context)
    prompt_template = build_prompt_template(persona_block)

    js_path = Path(args.context_js).resolve()
    js_file_ref = f"file://{js_path}"

    brain_api_key = os.environ.get("BRAIN_API_KEY", "")

    with open(args.csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    tests = []
    for row in rows:
        vars_ = {
            "question": row["question"],
            "context": js_file_ref,
            "BRAIN_URL": args.brain_url,
        }
        if brain_api_key:
            vars_["BRAIN_API_KEY"] = brain_api_key
        existing_suffix = row.get("query_suffix", "").strip()
        derived = _derive_query_terms(row.get("expected_answer_must_contain", ""))
        vars_["query_suffix"] = "{} {}".format(existing_suffix, derived).strip() if existing_suffix else derived
        tests.append({
            "description": f"[{row['eval_id']}] {row['category']} | {row['scope']} | {row['question'][:60]}",
            "vars": vars_,
            "assert": [{"type": "llm-rubric", "value": build_rubric(row, brain_context=brain_context)}],
            "metadata": {
                "eval_id": row["eval_id"],
                "category": row["category"],
                "scope": row["scope"],
                "ground_truth_source": row["ground_truth_source"],
            },
        })

    desc = f"Brain evals — {len(tests)} adversarial tests · judge: Sonnet 4.6"
    if brain_context and brain_context.get("goal"):
        desc = "{} | {}".format(desc, brain_context["goal"])

    config = {
        "description": desc,
        "prompts": [prompt_template],
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
