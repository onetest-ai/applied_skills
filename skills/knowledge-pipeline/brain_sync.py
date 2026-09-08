#!/usr/bin/env python3
"""brain_sync — incremental update of the knowledge store when documents change.

The store never recorded what it was built from, so it could only be fully rebuilt.
This adds a `documents` provenance table (content hash per doc) and drives an
INCREMENTAL update keyed off the delta:

  plan   — hash the parsed corpus, diff against `documents`, print added/changed/
           unchanged/deleted (no writes).
  apply  — snapshot the .sqlite, then for the delta: delete removed docs' chunks
           (and their tags/edges), (re)embed only added/changed docs with STABLE
           chunk ids, update `documents`. Emits a work order (sync_plan.json) naming
           the chunks that must be RE-CLASSIFIED — the one step that stays agentic.
  rollback — restore the latest pre-apply snapshot.

Numbers stay deterministic and meaning stays agentic: this script does the DB
surgery + tells the orchestrating agent exactly what to reclassify; it never tags.

After `apply`, the orchestrator finishes the delta (see knowledge-pipeline SKILL):
  classify_prep --chunks <reclassify_chunk_ids> -> Haiku agents -> classify_write
  build_graph (rebuild subclass_of; about edges preserved) ; to_obsidian --clean

Usage:
  brain_sync.py plan   --db K.sqlite --parsed <dir>
  brain_sync.py apply  --db K.sqlite --parsed <dir> [--out <workdir>] [--no-snapshot]
  brain_sync.py rollback --db K.sqlite
"""
import argparse, glob, hashlib, json, os, shutil, sqlite3, sys, time
from pathlib import Path

KI = Path(__file__).resolve().parent.parent / "knowledge-index"
sys.path.insert(0, str(KI))


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def ensure_documents(c):
    c.execute("""CREATE TABLE IF NOT EXISTS documents(
        doc_id TEXT PRIMARY KEY, sha TEXT, bytes INT, mtime REAL, updated_at TEXT)""")


def scan(parsed):
    out = {}
    for f in sorted(glob.glob(os.path.join(parsed, "**", "*.md"), recursive=True)):
        rel = os.path.relpath(f, parsed)
        st = os.stat(f)
        out[rel] = {"sha": sha_file(f), "bytes": st.st_size, "mtime": st.st_mtime}
    return out


def delta(c, parsed):
    ensure_documents(c)
    cur = {r[0]: r[1] for r in c.execute("SELECT doc_id, sha FROM documents")}
    now = scan(parsed)
    added   = [d for d in now if d not in cur]
    changed = [d for d in now if d in cur and now[d]["sha"] != cur[d]]
    unchanged = [d for d in now if d in cur and now[d]["sha"] == cur[d]]
    deleted = [d for d in cur if d not in now]
    return now, {"added": sorted(added), "changed": sorted(changed),
                 "unchanged": sorted(unchanged), "deleted": sorted(deleted)}


def cmd_plan(a):
    c = sqlite3.connect(a.db)
    _, d = delta(c, a.parsed)
    for k in ("added", "changed", "deleted", "unchanged"):
        print(f"{k:9} {len(d[k])}" + ("" if k == "unchanged" else "  " + ", ".join(x[:60] for x in d[k][:12])
                                       + (" …" if len(d[k]) > 12 else "")))
    reclass = d["added"] + d["changed"]
    print(f"\n→ would (re)embed {len(reclass)} doc(s), delete {len(d['deleted'])}, "
          f"skip {len(d['unchanged'])} unchanged; {len(reclass)} doc(s) then need reclassify.")
    c.close()


def cmd_apply(a):
    import knowledge_index as K
    if not a.no_snapshot and os.path.exists(a.db):
        snap = f"{a.db}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(a.db, snap)
        print(f"snapshot: {snap}")
    c = K.connect(a.db)
    K._ensure_schema(c, a.dim)
    now, d = delta(c, a.parsed)
    reclass = d["added"] + d["changed"]
    try:
        if d["deleted"]:
            K.delete_docs(c, d["deleted"])
            for doc in d["deleted"]:
                c.execute("DELETE FROM documents WHERE doc_id=?", (doc,))
            print(f"deleted {len(d['deleted'])} doc(s)")
        if reclass:
            n_chunks, _ = K.index_docs(c, a.model, a.parsed, reclass, a.dim, a.max_chars)
            print(f"(re)embedded {n_chunks} chunks across {len(reclass)} doc(s)")
        if (reclass or d["deleted"]) and not a.no_related:
            nrel = K.build_related(c)   # cheap vector-only kNN; keeps the related layer current
            print(f"rebuilt related layer: {nrel} semantic edges")
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        for doc in d["added"] + d["changed"] + d["unchanged"]:
            m = now[doc]
            c.execute("INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?)",
                      (doc, m["sha"], m["bytes"], m["mtime"], ts))
        c.commit()
    except Exception as e:
        c.rollback(); print(f"apply FAILED ({e}); DB left unchanged. Restore a snapshot if needed.", file=sys.stderr)
        raise
    # chunk ids that need reclassification (added + changed docs)
    recids = []
    for doc in reclass:
        recids += [r[0] for r in c.execute("SELECT id FROM chunks WHERE source=?", (doc,))]
    plan = {"reclassify_docs": reclass, "reclassify_chunk_ids": recids,
            "deleted_docs": d["deleted"], "unchanged": len(d["unchanged"])}
    out = a.out or os.path.dirname(os.path.abspath(a.db))
    os.makedirs(out, exist_ok=True)
    pf = os.path.join(out, "sync_plan.json")
    json.dump(plan, open(pf, "w"), indent=2)
    c.close()
    print(f"\napplied. work order -> {pf}")
    if reclass:
        print(f"NEXT (agentic): reclassify {len(recids)} chunk(s) of {len(reclass)} changed doc(s):")
        print(f"  classify_prep.py --db {a.db} --taxonomy <tax> --out <cls> "
              f"--chunks {','.join(map(str, recids[:6]))}{',…' if len(recids) > 6 else ''}")
        print("  → Haiku agents → classify_write.py (incremental) → build_graph.py → to_obsidian.py --clean")
    else:
        print("no doc changes — RAG lane already current.")


def cmd_seed(a):
    """Record the current parsed corpus's hashes into `documents` WITHOUT re-embedding
    — run once right after a full build so later plan/apply can compute deltas."""
    c = sqlite3.connect(a.db)
    ensure_documents(c)
    now = scan(a.parsed)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    for doc, m in now.items():
        c.execute("INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?)",
                  (doc, m["sha"], m["bytes"], m["mtime"], ts))
    c.commit()
    print(f"seeded documents with {len(now)} doc hashes -> {a.db}")
    c.close()


def cmd_rollback(a):
    snaps = sorted(glob.glob(f"{a.db}.bak-*"))
    if not snaps:
        print("no snapshot found", file=sys.stderr); sys.exit(1)
    latest = snaps[-1]
    shutil.copy2(latest, a.db)
    print(f"restored {a.db} from {latest}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "apply", "seed", "rollback"):
        p = sub.add_parser(name)
        p.add_argument("--db", required=True)
        if name != "rollback":
            p.add_argument("--parsed", required=True, help="dir of parsed *.md (the RAG corpus)")
            p.add_argument("--model", default="BAAI/bge-small-en-v1.5")
            p.add_argument("--dim", type=int, default=384)
            p.add_argument("--max-chars", type=int, default=1200)
        if name == "apply":
            p.add_argument("--out", help="where to write sync_plan.json (default: next to the db)")
            p.add_argument("--no-snapshot", action="store_true")
            p.add_argument("--no-related", action="store_true", help="skip rebuilding the semantic related layer")
    a = ap.parse_args()
    {"plan": cmd_plan, "apply": cmd_apply, "seed": cmd_seed, "rollback": cmd_rollback}[a.cmd](a)


if __name__ == "__main__":
    main()
