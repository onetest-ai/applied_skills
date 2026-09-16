#!/usr/bin/env python3
"""brain_sync — incremental update of the knowledge store when documents change.

The store never recorded what it was built from, so it could only be fully rebuilt.
This adds a `documents` provenance table (content hash per doc) and drives an
INCREMENTAL update keyed off the delta:

  plan   — hash the parsed corpus, diff against `documents`, print added/changed/
           unchanged/deleted. Requires an existing initialized store and is read-only.
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

try:
    import tomllib  # Python 3.11+ (stdlib)
except ModuleNotFoundError:  # pragma: no cover - older interpreters
    import tomli as tomllib

KI = Path(__file__).resolve().parent.parent / "knowledge-index"
sys.path.insert(0, str(KI))


def ensure_meta(c):
    """Durable key/value table for build-time facts (goal, audience). Created with
    IF NOT EXISTS so it survives `knowledge_index.py index --reset` (which only drops
    chunks/chunks_fts/chunks_vec) and older stores that predate it."""
    c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")


def _read_goal_audience(db):
    """Resolve <project> from the db path (<project>/schema/knowledge.sqlite) and read
    the canonical goal (goal.txt) + audience (brain.toml [project].audience). Absent
    files yield empty strings — never crash."""
    project = Path(db).resolve().parent.parent
    goal = ""
    goal_txt = project / "goal.txt"
    if goal_txt.is_file():
        goal = goal_txt.read_text(encoding="utf-8").strip()
    audience = ""
    toml_path = project / "brain.toml"
    if toml_path.is_file():
        try:
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
            audience = str((data.get("project") or {}).get("audience", "") or "")
        except Exception:
            audience = ""
    return goal, audience


def write_meta(c, db):
    """UPSERT goal + audience into meta (idempotent; re-seeding refreshes them)."""
    ensure_meta(c)
    goal, audience = _read_goal_audience(db)
    for key, value in (("goal", goal), ("audience", audience)):
        c.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    return goal, audience


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def ensure_documents(c):
    c.execute("""CREATE TABLE IF NOT EXISTS synced_files(
        doc_id TEXT PRIMARY KEY, sha TEXT, bytes INT, mtime REAL, updated_at TEXT,
        source_id TEXT)""")
    cols = {r[1] for r in c.execute("PRAGMA table_info(synced_files)")}
    if "source_id" not in cols:
        c.execute("ALTER TABLE synced_files ADD COLUMN source_id TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS idx_synced_files_source_id ON synced_files(source_id)")


def _has(c, table):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _safe_rel(value):
    value = str(value).replace("\\", "/")
    p = Path(value)
    if not value or value == "." or p.is_absolute() or any(x in ("", ".", "..") for x in value.split("/")):
        raise ValueError(f"unsafe manifest relative path: {value!r}")
    return p.as_posix()


def manifest_links(parsed, manifest=None, root_key=None):
    path = Path(manifest) if manifest else Path(parsed) / "manifest.json"
    if not path.is_file() or not root_key:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("parse manifest must be a JSON array")
    links = {}
    for item in data:
        if not isinstance(item, dict) or item.get("error") or not item.get("md") or not item.get("source"):
            continue
        doc_id, source = _safe_rel(item["md"]), _safe_rel(item["source"])
        if doc_id in links:
            raise ValueError(f"duplicate parsed document in manifest: {doc_id}")
        links[doc_id] = (root_key, source)
    return links


def source_ids(c, parsed, manifest=None, root_key=None, strict=False):
    links = manifest_links(parsed, manifest, root_key)
    if not links:
        if strict:
            raise ValueError("strict source mode needs --root-key and a parse manifest")
        return {}, []
    if not _has(c, "sources"):
        if strict:
            raise ValueError("strict source mode needs an initialized sources registry")
        return {}, sorted(links)
    out, unmanaged = {}, []
    for doc_id, (key, rel) in links.items():
        row = c.execute("SELECT source_id,state FROM sources WHERE root_key=? AND relative_path=?", (key, rel)).fetchone()
        if row and row[1] == "active":
            out[doc_id] = row[0]
        elif row and row[1] == "removed":
            # A previously linked tombstoned source is a valid strict mapping for deletion.
            # Do not allow a new parsed document to bind to a removed source.
            linked = c.execute("SELECT 1 FROM synced_files WHERE doc_id=? AND source_id=?", (doc_id, row[0])).fetchone()
            if linked:
                out[doc_id] = row[0]
            else:
                unmanaged.append(doc_id)
        else:
            unmanaged.append(doc_id)
    if strict:
        scanned = set(scan(parsed))
        manifest_docs = set(links)
        missing_manifest = sorted(scanned - manifest_docs)
        stale_manifest = sorted(manifest_docs - scanned)
        if missing_manifest or stale_manifest:
            parts = []
            if missing_manifest: parts.append("missing from manifest: " + ", ".join(missing_manifest[:10]))
            if stale_manifest: parts.append("manifest output absent: " + ", ".join(stale_manifest[:10]))
            raise ValueError("strict source manifest mismatch (" + "; ".join(parts) + ")")
        if unmanaged:
            raise ValueError("unmanaged parsed documents: " + ", ".join(unmanaged[:10]))
    return out, sorted(unmanaged)


def scan(parsed):
    out = {}
    for f in sorted(glob.glob(os.path.join(parsed, "**", "*.md"), recursive=True)):
        rel = os.path.relpath(f, parsed)
        st = os.stat(f)
        out[rel] = {"sha": sha_file(f), "bytes": st.st_size, "mtime": st.st_mtime}
    return out


def delta(c, parsed, *, mutate_schema=True):
    if mutate_schema:
        ensure_documents(c)
    elif not _has(c, "synced_files"):
        raise RuntimeError("synced_files table missing; run brain_sync seed first")
    cols = {r[1] for r in c.execute("PRAGMA table_info(synced_files)")}
    select = ("SELECT doc_id, sha, source_id FROM synced_files" if "source_id" in cols
              else "SELECT doc_id, sha, NULL FROM synced_files")
    rows = list(c.execute(select))
    cur = {r[0]: r[1] for r in rows}
    source_for = {r[0]: r[2] for r in rows}
    now = scan(parsed)
    have_sources = _has(c, "sources")
    removed_linked = set()
    if have_sources:
        removed_linked = {r[0] for r in c.execute(
            """SELECT d.doc_id FROM synced_files d JOIN sources s ON s.source_id=d.source_id
               WHERE s.state='removed'""")}
    # A tombstoned source is an explicit deletion even when stale parsed output remains.
    effective_now = {doc: meta for doc, meta in now.items() if doc not in removed_linked}
    added = [d for d in effective_now if d not in cur]
    changed = [d for d in effective_now if d in cur and effective_now[d]["sha"] != cur[d]]
    unchanged = [d for d in effective_now if d in cur and effective_now[d]["sha"] == cur[d]]
    missing = [d for d in cur if d not in effective_now]
    deleted, blocked, legacy = [], [], []
    for doc in missing:
        sid = source_for.get(doc)
        if doc in removed_linked:
            deleted.append(doc)
        elif sid and have_sources:
            blocked.append(doc)
        else:
            deleted.append(doc)
            legacy.append(doc)
    now = effective_now
    return now, {"added": sorted(added), "changed": sorted(changed),
                 "unchanged": sorted(unchanged), "deleted": sorted(deleted),
                 "blocked_missing_parsed": sorted(blocked),
                 "legacy_unlinked_deleted": sorted(legacy)}


def cmd_plan(a):
    db = Path(a.db).expanduser().resolve()
    if not db.is_file():
        raise FileNotFoundError(f"knowledge store does not exist: {db}")
    c = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    _, d = delta(c, a.parsed, mutate_schema=False)
    _, unmanaged = source_ids(c, a.parsed, a.manifest, a.root_key, a.strict_sources)
    for k in ("added", "changed", "deleted", "blocked_missing_parsed", "unchanged"):
        print(f"{k:9} {len(d[k])}" + ("" if k == "unchanged" else "  " + ", ".join(x[:60] for x in d[k][:12])
                                       + (" …" if len(d[k]) > 12 else "")))
    reclass = d["added"] + d["changed"]
    print(f"\n→ would (re)embed {len(reclass)} doc(s), delete {len(d['deleted'])}, "
          f"block {len(d['blocked_missing_parsed'])} linked missing parsed doc(s), "
          f"skip {len(d['unchanged'])} unchanged; {len(reclass)} doc(s) then need reclassify.")
    if d["legacy_unlinked_deleted"]:
        print(f"warning: {len(d['legacy_unlinked_deleted'])} legacy unlinked document(s) use deletion compatibility mode", file=sys.stderr)
    if unmanaged:
        print(f"warning: {len(unmanaged)} parsed document(s) are not linked to an active source", file=sys.stderr)
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
    links, unmanaged = source_ids(c, a.parsed, a.manifest, a.root_key, a.strict_sources)
    if d["blocked_missing_parsed"]:
        c.close()
        raise RuntimeError("active registered source(s) have missing parsed output; restore/assemble them or explicitly tombstone the source: " + ", ".join(d["blocked_missing_parsed"][:10]))
    if unmanaged:
        print(f"warning: {len(unmanaged)} parsed document(s) are not linked to an active source", file=sys.stderr)
    reclass = d["added"] + d["changed"]
    try:
        if d["deleted"]:
            K.delete_docs(c, d["deleted"])
            for doc in d["deleted"]:
                c.execute("DELETE FROM synced_files WHERE doc_id=?", (doc,))
            print(f"deleted {len(d['deleted'])} doc(s)")
        if reclass:
            n_chunks, _n_docs, _skipped = K.index_docs(c, a.model, a.parsed, reclass, a.dim, a.max_chars)
            print(f"(re)embedded {n_chunks} chunks across {len(reclass)} doc(s)")
        if (reclass or d["deleted"]) and not a.no_related:
            nrel = K.build_related(c, commit=False)   # keep the full apply transaction atomic
            print(f"rebuilt related layer: {nrel} semantic edges")
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        for doc in d["added"] + d["changed"] + d["unchanged"]:
            m = now[doc]
            prior = c.execute("SELECT source_id FROM synced_files WHERE doc_id=?", (doc,)).fetchone()
            sid = links.get(doc) or (prior[0] if prior else None)
            c.execute("INSERT OR REPLACE INTO synced_files(doc_id,sha,bytes,mtime,updated_at,source_id) VALUES(?,?,?,?,?,?)",
                      (doc, m["sha"], m["bytes"], m["mtime"], ts, sid))
        c.commit()
    except Exception as e:
        c.rollback(); c.close()
        if not a.no_snapshot and 'snap' in locals():
            shutil.copy2(snap, a.db)
            print(f"apply FAILED ({e}); restored snapshot {snap}", file=sys.stderr)
        else:
            print(f"apply FAILED ({e}); transaction rolled back", file=sys.stderr)
        raise
    # chunk ids that need reclassification (added + changed docs)
    recids = []
    for doc in reclass:
        recids += [r[0] for r in c.execute("SELECT id FROM chunks WHERE source=?", (doc,))]
    plan = {"reclassify_docs": reclass, "reclassify_chunk_ids": recids,
            "source_ids": {doc: links.get(doc) for doc in reclass if links.get(doc)},
            "deleted_docs": d["deleted"], "blocked_missing_parsed": d["blocked_missing_parsed"],
            "unmanaged_docs": unmanaged, "unchanged": len(d["unchanged"])}
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
    import knowledge_index as K
    c = K.connect(a.db)
    K._ensure_schema(c, a.dim)  # migrate legacy brain_sync documents→synced_files if needed
    ensure_documents(c)
    now = scan(a.parsed)
    links, unmanaged = source_ids(c, a.parsed, a.manifest, a.root_key, a.strict_sources)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    for doc, m in now.items():
        prior = c.execute("SELECT source_id FROM synced_files WHERE doc_id=?", (doc,)).fetchone()
        sid = links.get(doc) or (prior[0] if prior else None)
        c.execute("INSERT OR REPLACE INTO synced_files(doc_id,sha,bytes,mtime,updated_at,source_id) VALUES(?,?,?,?,?,?)",
                  (doc, m["sha"], m["bytes"], m["mtime"], ts, sid))
    goal, audience = write_meta(c, a.db)
    c.commit()
    print(f"seeded documents with {len(now)} doc hashes -> {a.db}"
          + (f" ({len(unmanaged)} unmanaged)" if unmanaged else ""))
    print(f"meta refreshed: goal={'set' if goal else 'empty'}, "
          f"audience={'set' if audience else 'empty'}")
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
            p.add_argument("--manifest", help="parse manifest (default: <parsed>/manifest.json)")
            p.add_argument("--root-key", help="source registry root key for manifest source paths")
            p.add_argument("--strict-sources", action="store_true", help="reject parsed docs not linked to active registered sources")
        if name == "apply":
            p.add_argument("--out", help="where to write sync_plan.json (default: next to the db)")
            p.add_argument("--no-snapshot", action="store_true")
            p.add_argument("--no-related", action="store_true", help="skip rebuilding the semantic related layer")
    a = ap.parse_args()
    {"plan": cmd_plan, "apply": cmd_apply, "seed": cmd_seed, "rollback": cmd_rollback}[a.cmd](a)


if __name__ == "__main__":
    main()
