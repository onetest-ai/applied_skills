"""Extract verbatim facts per taxonomy category from parsed Markdown files.

Usage:
  python extract_facts.py \
    --parsed  /path/to/parsed/     # directory of *.md files
    --taxonomy /path/to/taxonomy.json \
    --out     /path/to/extractions/ # writes <slug>_extraction.json per file
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Bedrock inference profile id for fact extraction. Matches the Sonnet id used
# elsewhere in this repo (see generate_promptfoo.py PROVIDERS); override with --model.
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"


_EXTRACT_PROMPT_TEMPLATE = """\
You are a fact-extraction assistant. Given a meeting transcript in Markdown format \
and a list of taxonomy categories, extract the most informative verbatim quotes \
that belong to each category.

## Taxonomy categories
{categories}

## Transcript
{transcript}

## Instructions
- For each category, find 1-3 verbatim quotes from the transcript that clearly belong \
to that category.
- QUALITY BAR — every quote must pass this test: could a new team member read this \
quote alone and learn something concrete? If not, skip it.
  GOOD: "we need to migrate the legacy platform to the new CMS by end of Q1 — management has approved the budget"
  GOOD: "the publish button gives an error when mandatory fields are empty — reproduced in staging"
  GOOD: "assign the logging task to Jordan by Friday"
  BAD: "we as a Scrum master" (fragment — no concrete information)
  BAD: "can you be able to reproduce it now?" (question — no fact stated)
  BAD: "I will share you the links" (vague — no subject, no context)
- A quote must contain: WHO or WHAT, plus a concrete action, decision, risk, or finding.
- Prefer longer complete sentences over short fragments — 15-60 words is ideal.
- Skip a category if no quote meets the quality bar.
- Return ONLY a JSON array. No markdown fences, no commentary.
- Schema: {{"category": "<category>", "verbatim_quote": "<quote>"}}
- Example: [{{"category": "ActionItem", "verbatim_quote": "assign the logging task to Jordan by Friday"}}]
"""


def _build_prompt(md_text: str, taxonomy_l1: list[str]) -> str:
    categories_str = "\n".join(f"- {c}" for c in taxonomy_l1)
    return _EXTRACT_PROMPT_TEMPLATE.format(
        categories=categories_str,
        transcript=md_text[:8000],  # keep tokens manageable
    )


def _make_bedrock_llm(model_id: str = DEFAULT_MODEL_ID):
    """Return a callable(prompt) → str using Bedrock (Sonnet by default). May raise if creds absent."""
    import boto3  # noqa: PLC0415
    import json as _json

    client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    def _call(prompt: str) -> str:
        body = _json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}],
        })
        resp = client.invoke_model(modelId=model_id, body=body)
        payload = _json.loads(resp["body"].read())
        return payload["content"][0]["text"]

    return _call


def extract_facts_from_md(
    md_text: str,
    taxonomy_l1: list[str],
    llm_fn,  # callable(prompt: str) -> str
) -> list[dict]:
    """Call llm_fn with md_text and taxonomy_l1, return parsed list of extraction dicts."""
    prompt = _build_prompt(md_text, taxonomy_l1)
    raw = llm_fn(prompt)
    # Strip any accidental markdown fences
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```", 2)
        if len(parts) >= 2:
            candidate = parts[1].lstrip("json").strip()
            if candidate:
                raw = candidate
            elif len(parts) >= 3 and parts[2].strip():
                # empty fenced block — fall back to content after the closing fence
                raw = parts[2].strip()
            # else: fall through and try parsing the full response
    items = json.loads(raw)
    if not isinstance(items, list):
        raise TypeError(f"expected JSON array from LLM, got {type(items).__name__}")
    # Filter to only items with required keys and non-empty values
    result = []
    for item in items:
        cat = str(item.get("category", "")).strip()
        quote = str(item.get("verbatim_quote", "")).strip()
        if cat and quote and cat in taxonomy_l1:
            result.append({"category": cat, "verbatim_quote": quote})
    return result


def run_extractions(
    parsed_dir: str,
    taxonomy: dict,
    out_dir: str,
    llm_fn=None,
    model_id: str = DEFAULT_MODEL_ID,
) -> list[str]:
    """Process all *.md files in parsed_dir; write *_extraction.json to out_dir.

    Returns list of written file paths.
    """
    if llm_fn is None:
        llm_fn = _make_bedrock_llm(model_id)

    taxonomy_l1 = taxonomy.get("intent_taxonomy", {}).get("l1", [])
    if not taxonomy_l1:
        print("WARNING: taxonomy has no intent_taxonomy.l1 — nothing to extract", file=sys.stderr)
        return []

    parsed_path = Path(parsed_dir)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    written = []
    for md_file in sorted(parsed_path.glob("*.md")):
        slug = md_file.stem
        out_file = out_path / f"{slug}_extraction.json"
        if out_file.exists():
            print(f"  [skip] {md_file.name} — extraction already exists", file=sys.stderr)
            continue
        md_text = md_file.read_text(encoding="utf-8")
        try:
            facts = extract_facts_from_md(md_text, taxonomy_l1, llm_fn)
        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as exc:
            print(f"WARNING: failed to extract facts from {md_file.name}: {exc}", file=sys.stderr)
            continue
        if not facts:
            print(f"  {md_file.name}: no facts extracted — skipping", file=sys.stderr)
            continue
        out_file.write_text(
            json.dumps({"file_slug": slug, "extractions": facts}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(str(out_file))
        print(f"  {md_file.name}: {len(facts)} facts → {out_file.name}")
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Extract verbatim facts from parsed Markdown using Bedrock Sonnet"
    )
    ap.add_argument("--parsed", required=True, help="Directory of *.md files")
    ap.add_argument("--taxonomy", required=True, help="Path to taxonomy.json")
    ap.add_argument("--out", required=True, help="Output directory for *_extraction.json files")
    ap.add_argument("--model", default=DEFAULT_MODEL_ID, help=f"Bedrock model id (default: {DEFAULT_MODEL_ID})")
    args = ap.parse_args(argv)

    taxonomy = json.loads(Path(args.taxonomy).read_text(encoding="utf-8"))
    written = run_extractions(args.parsed, taxonomy, args.out, model_id=args.model)
    print(f"\n{len(written)} extraction files written to {args.out}")


if __name__ == "__main__":
    main()
