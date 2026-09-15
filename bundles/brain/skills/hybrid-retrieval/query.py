#!/usr/bin/env python3
"""Deterministic query helper over the marts (the numeric answering path).

The model selects a governed metric + filters; this returns the exact value(s)
computed by SQLite — never asserted by an LLM. Every answer cites source_file.

Examples (db + catalog are project artifacts; names are the project's choice):
  query.py --db <marts>/knowledge.sqlite --catalog <schema>/metrics.<corpus>.json --list
  query.py --db <marts>/knowledge.sqlite --catalog <schema>/metrics.<corpus>.json \
           --metric <metric> --grain <grain> --entity <NAME> --month 2026-06
  query.py --db <marts>/knowledge.sqlite --catalog <schema>/metrics.<corpus>.json \
           --metric <metric> --grain <grain> --months 2026-06,2026-07
  query.py --db <marts>/knowledge.sqlite --sql "SELECT ..."   # raw escape hatch
"""
import argparse, json, sys
import sqlite3

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--catalog")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--metric")
    ap.add_argument("--grain")
    ap.add_argument("--entity")           # exact
    ap.add_argument("--entity-like")      # substring (e.g. a branch name)
    ap.add_argument("--month")
    ap.add_argument("--months")           # comma list
    ap.add_argument("--sql")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)

    if a.sql:
        cur = con.execute(a.sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        _print(cols, rows, a.json); return

    cat = json.load(open(a.catalog))["metrics"] if a.catalog else {}
    if a.list:
        for k, v in cat.items():
            print(f"{k:26} {v['family']}.{v['metric']:24} {v.get('unit',''):8} {v.get('desc','')}")
        return

    if not a.metric or a.metric not in cat:
        print(f"unknown metric '{a.metric}'. Use --list.", file=sys.stderr); sys.exit(2)
    spec = cat[a.metric]
    where = [f"family = '{spec['family']}'", f"metric = '{spec['metric']}'"]
    if a.grain: where.append(f"grain = '{a.grain}'")
    if a.entity: where.append(f"entity = '{a.entity.replace(chr(39), chr(39)*2)}'")
    if a.entity_like: where.append(f"lower(entity) LIKE '%{a.entity_like.lower()}%'")
    if a.month: where.append(f"month = '{a.month}'")
    if a.months: where.append("month IN (" + ",".join(f"'{m.strip()}'" for m in a.months.split(",")) + ")")
    sql = ("SELECT entity, month, value, source_file FROM facts WHERE "
           + " AND ".join(where) + " ORDER BY entity, month")
    rows = con.execute(sql).fetchall()
    _print(["entity","month",a.metric,"source_file"], rows, a.json)

def _print(cols, rows, as_json):
    if as_json:
        print(json.dumps([dict(zip(cols, r)) for r in rows], indent=2, default=str)); return
    if not rows:
        print("(no rows)"); return
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join(str(x) for x in r))

if __name__ == "__main__":
    main()
