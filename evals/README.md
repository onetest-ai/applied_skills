# Second-brain evaluations

These evaluations keep retrieval quality separate from answer synthesis. The
FastMCP `/api/v1/search` endpoint returns ranked evidence chunks; it does not
write a synthesized answer.

## Philips retrieval baseline

Start the brain server on port 8002, then run the deterministic Recall@15 gate:

```bash
promptfoo eval \
  -c evals/promptfooconfig.retrieval.yaml \
  -t /Users/Karen_Florykian/projects/kt-docs/brain/csv/evals_v2_generated.csv \
  --no-cache --max-concurrency 4 --no-share \
  -o /tmp/philips-retrieval.json
```

The local assertion splits `expected_answer_must_contain` on `|`, counts exact
normalized concepts present in the returned context, and rejects literal
forbidden concepts. It has no model, AWS, or API-key dependency. The initial
2026-09-14 baseline is 14/65 passing with zero provider errors. This strict
number is lower than the 16/65 LLM-judged run because the LLM accepted some
paraphrases.

Use the LLM config only for a separate qualitative semantic-coverage run:

```bash
promptfoo eval \
  -c evals/promptfooconfig.retrieval.llm.yaml \
  -t /Users/Karen_Florykian/projects/kt-docs/brain/csv/evals_v2_generated.csv \
  --no-cache --max-concurrency 1 --no-share \
  -o /tmp/philips-retrieval-llm.json
```

That run requires current AWS credentials for the configured Bedrock judge.
An `UnrecognizedClientException` is an evaluation-infrastructure error, not a
retrieval failure.

The 65 generated cases are broad answer/evidence coverage cases. They expose
the need for cross-session aggregation or query decomposition. They should not
be relabeled as synthesized-answer tests, and the baseline should not be raised
by increasing K without reporting the new K.

## Deterministic index benchmark

The index CLI measures source-level Hit Rate@K and MRR without an LLM:

```bash
python skills/knowledge-index/knowledge_index.py benchmark \
  --db knowledge.sqlite --evals evals/retrieval.example.csv --k 10 --json
```

`temporal_cross_session.csv` documents the expected current/as-of facts,
unresolved conflict behavior, and cross-meeting question resolution covered by
the automated temporal-memory tests.
