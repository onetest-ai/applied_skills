#!/usr/bin/env python3
"""Batch corpus chunks + instructions for Sonnet fact-extraction agents.

Open-category extraction: pull any salient fact (WHO/WHAT + concrete action,
decision, risk, finding, date, number, status). Skip questions and chit-chat.
Agents write result_<k>.json: [{chunk_id, entity, predicate, value, evidence,
sentiment, stance}]. fact_write.py consumes those.
"""
import argparse, json, os, sqlite3

INSTRUCTIONS = """# Extract facts from each chunk (open-category)

For every chunk, extract 0-N assertions it genuinely states. Each assertion:
- entity: the thing the fact is about (short noun phrase)
- predicate: the attribute/relation (short)
- value: the asserted value (string)
- evidence: a verbatim quote (<=200 chars) from the chunk supporting it
- sentiment: one of positive|neutral|negative|mixed
- stance: optional short stance toward the entity (may be "")

QUALITY BAR: every assertion must state WHO/WHAT + a concrete action, decision,
risk, finding, date, number, or status. Skip questions, hedges, greetings,
scheduling and chit-chat. If a chunk states nothing concrete, emit nothing.

Output ONE JSON file result_<k>.json: a list of objects, each including the
integer "chunk_id" it came from. Example:
[{"chunk_id": 1, "entity": "v2", "predicate": "ship date", "value": "Friday",
  "evidence": "We shipped v2 on Friday.", "sentiment": "positive", "stance": ""}]
"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--batches", type=int, default=5); ap.add_argument("--preview", type=int, default=400)
    ap.add_argument("--docs"); ap.add_argument("--chunks"); ap.add_argument("--sources")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "instructions.md"), "w") as f:
        f.write(INSTRUCTIONS)
    con = sqlite3.connect(a.db)
    where, params = [], [a.preview]
    if a.docs:
        d = [s.strip() for s in a.docs.split(",") if s.strip()]
        where.append("source IN (" + ",".join("?" * len(d)) + ")"); params += d
    elif a.chunks:
        ids = [int(x) for x in a.chunks.split(",") if x.strip()]
        where.append("id IN (" + ",".join("?" * len(ids)) + ")"); params += ids
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = con.execute(
        f"SELECT id, source, title, substr(text,1,?), speaker, event_date FROM chunks{clause} ORDER BY id",
        params).fetchall()
    con.close()
    if a.sources:
        exts = tuple("." + s.strip().lower().lstrip(".") for s in a.sources.split(",") if s.strip())
        rows = [r for r in rows if str(r[1]).lower().endswith(exts)]
    if not rows:
        print(f"no chunks match -> {a.out}"); return
    n = max(1, a.batches); size = (len(rows) + n - 1) // n
    for k in range(n):
        batch = rows[k*size:(k+1)*size]
        if not batch: break
        items = [{"id": r[0], "source": r[1], "title": r[2],
                  "event_date": r[5], "speaker": r[4],
                  "preview": " ".join((r[3] or "").split())} for r in batch]
        with open(os.path.join(a.out, f"batch_{k}.json"), "w") as bf:
            json.dump(items, bf, indent=1)
    print(f"prepared {len(rows)} chunks -> {a.out}")


if __name__ == "__main__":
    main()
