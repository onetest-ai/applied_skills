# VTT/SRT second-brain pipeline review

**Date:** 2026-09-14  
**Branch:** `feat/vtt-srt-corpus-support`

## Goal

Accept meeting transcripts, update a growing brain incrementally, retrieve with
structural and temporal constraints, and reason across sessions without losing history.

## Plan audit

| Capability | Status | Implementation and acceptance evidence |
| --- | --- | --- |
| VTT/SRT ingestion | Implemented | cue parser and speaker tests |
| Extraction JSON to bounded Markdown chunks | Implemented | serializer and raw-JSON guard tests |
| Incremental updates | Implemented | document and embedding hashes; unchanged documents and chunks skip embedding |
| Temporal chunk lifecycle | Implemented | event/validity fields, `ACTIVE`/`SUPERSEDED`, `--as-of`, `--latest-only` |
| Faceted retrieval | Implemented | safe source substring, tag, date, and status filters before ranking |
| Parent-child context | Implemented | `parent_heading` and `breadcrumb_path` returned with every hit |
| Speaker attribution | Implemented | WebVTT voice tags and conservative `Name:` prefixes become chunk metadata |
| Cross-session discovery | Implemented | cosine `SIMILAR` edges across documents |
| Typed cross-session reasoning | Implemented | directional `ANSWERS`, `SUPERSEDES`, `REFERENCES`, `CONTRADICTS` edges |
| Fact and question lifecycle | Implemented | temporal ledger, current/as-of fact and open/resolved question MCP tools |
| Retrieval benchmark | Implemented | CSV runner reports Hit Rate at K and MRR |

## Correction to the earlier design

Cosine similarity is discovery, not reasoning. A similar chunk may concern the same
subject, but similarity cannot prove that one claim replaces another or that a later
statement answers a specific earlier question. Typed links now carry that meaning.

The system keeps all dated claims. An explicit `SUPERSEDES` relation retires an earlier
claim from the latest view while preserving it for historical queries. An unresolved
conflict returns `conflicted` without choosing a value. Event time controls `as_of`;
ingestion time remains audit metadata, so a late-imported old transcript cannot become
the newest truth.

Questions use stable IDs. An answer closes a question only through an explicit link,
and the result cites both the question meeting and the answer meeting. Similar wording
alone cannot close it.

## Evaluation boundary

`search_knowledge` returns retrieved evidence. It does not synthesize an answer. The
REST shim labels its payload `retrieved_context` and returns all source names.

Retrieval evals measure Hit Rate at K, MRR, source recall, filters, and cross-session
coverage. End-to-end answer evals belong at the client-agent layer and measure factual
faithfulness, completeness, conflict disclosure, correct as-of behavior, question
status, and citations. Adding synthesis inside FastMCP would mix these failure domains.

## Required temporal policy

1. Preserve old and new meeting statements with source and segment citations.
2. Store event time separately from ingestion time.
3. Apply `SUPERSEDES` only when a correction is explicit and keys match.
4. Mark unresolved disagreement as `CONTRADICTS`; do not pick a winner by timestamp.
5. Filter `SUPERSEDED` chunks from current searches while retaining them for `as_of`.
6. Use stable assertion, question, and answer IDs so retries are idempotent.
7. Require citations from both sessions when a later meeting resolves a question.

## Acceptance commands

```bash
python -m pytest -q

python skills/knowledge-index/knowledge_index.py benchmark \
  --db knowledge.sqlite \
  --evals evals/retrieval.example.csv \
  --k 5 --output retrieval-report.json

python skills/knowledge-index/knowledge_index.py search \
  --db knowledge.sqlite --query "budget" \
  --as-of 2025-09-21 --latest-only \
  --source-contains meetings --tag Budget --json
```

The benchmark CSV tests retrieval. A synthesized-answer suite must call a real agent
that uses MCP; it must not feed raw chunks to a rubric that expects prose answers.

For project-generated CSVs with columns such as `question`, `min_items`, and
`expected_answer_must_contain`, run the credential-free Promptfoo retrieval config:

```bash
promptfoo eval \
  -c evals/promptfooconfig.retrieval.yaml \
  -t /absolute/path/to/evals_v2_generated.csv \
  --no-cache --max-concurrency 4 --no-share \
  -o retrieval-results.json -o retrieval-results.html
```

The config performs normalized exact-concept coverage locally, so AWS credentials and
an LLM judge cannot turn infrastructure errors into retrieval failures. The initial
Recall@15 baseline on `evals_v2_generated.csv` is 14/65 with zero provider errors.
The separate LLM config accepts semantic paraphrases and produced 16/65 before its
Bedrock credentials expired. Keep both results labeled with their grader and K.

The CSV does not need `__expected` columns because `defaultTest.assert` reads each
row's variables. Use `--filter-first-n 3` for a smoke run.
