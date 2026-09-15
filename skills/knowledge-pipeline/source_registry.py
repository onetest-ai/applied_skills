#!/usr/bin/env python3
"""Portable source registry for a Brain project.

The registry stores metadata and provenance only—never source file bytes. Files are
addressed by (root_key, relative_path); root paths live in relocatable brain.toml.

Commands:
  init     create/migrate registry schema
  list     list registered sources
  get      inspect one source and its derived-document links
  import   copy a chat/local attachment into a managed root and register it
  adopt    register an existing file under a configured root
  plan     compare registered metadata with configured roots (strictly read-only)
  apply    apply safe add/content-change/move actions from a saved plan
  remove   explicitly tombstone sources (optionally delete a managed source file)
  restore  reactivate a tombstoned source that still exists
  migrate  map parse_corpus manifest entries to sources and documents.source_id
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
import uuid
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore

SCHEMA_VERSION = 1
ROOT_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
SOURCE_KINDS = {"narrative", "reporting"}
ROOT_MODES = {"import", "mirror", "managed"}


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def normalize_rel(value: str) -> str:
    value = unicodedata.normalize("NFC", value.replace("\\", "/"))
    p = Path(value)
    if not value or value == "." or p.is_absolute() or "\x00" in value or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError(f"unsafe relative source path: {value!r}")
    return p.as_posix()


def _resolved_inside(root: Path, rel: str, *, must_exist: bool = True) -> Path:
    rel = normalize_rel(rel)
    root = root.resolve()
    candidate = root.joinpath(*rel.split("/"))
    resolved = candidate.resolve(strict=must_exist)
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"source path escapes configured root: {rel}")
    return resolved


def resolve_config(db: str | None, explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if os.getenv("BRAIN_CONFIG"):
        candidates.append(Path(os.environ["BRAIN_CONFIG"]).expanduser())
    if db:
        dbp = Path(db).expanduser().resolve()
        candidates.extend((dbp.parent / "brain.toml", dbp.parent.parent / "brain.toml"))
    candidates.append(Path.cwd() / "brain.toml")
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError("brain.toml not found; pass --config or set BRAIN_CONFIG")


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    if config.get("version") != 1:
        raise ValueError("brain.toml must contain version = 1")
    roots = config.get("sources", {}).get("roots", {})
    if not isinstance(roots, dict) or not roots:
        raise ValueError("brain.toml must define [sources.roots.<key>]")
    out: dict[str, Any] = {"path": path, "roots": {}}
    for key, spec in roots.items():
        if not ROOT_KEY.fullmatch(key) or not isinstance(spec, dict):
            raise ValueError(f"invalid source root key: {key!r}")
        mode = spec.get("mode", "import")
        if mode not in ROOT_MODES:
            raise ValueError(f"invalid mode for source root {key}: {mode!r}")
        raw = os.path.expandvars(os.path.expanduser(str(spec.get("path", ""))))
        if not raw:
            raise ValueError(f"source root {key} has no path")
        root = Path(raw)
        if not root.is_absolute():
            root = path.parent / root
        includes = spec.get("include", ["**/*"])
        if not isinstance(includes, list) or not all(isinstance(x, str) for x in includes):
            raise ValueError(f"source root {key} include must be a string array")
        out["roots"][key] = {"path": root.resolve(), "mode": mode, "include": includes}
    return out


def connect(db: str) -> sqlite3.Connection:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def connect_readonly(db: str) -> sqlite3.Connection:
    path = Path(db).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"knowledge store does not exist: {path}")
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    now = utcnow()
    with con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS brain_schema(
          component TEXT PRIMARY KEY, version INTEGER NOT NULL, migrated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sources(
          source_id TEXT PRIMARY KEY,
          root_key TEXT NOT NULL,
          relative_path TEXT NOT NULL,
          source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64),
          byte_size INTEGER NOT NULL CHECK(byte_size>=0),
          media_type TEXT,
          source_kind TEXT NOT NULL CHECK(source_kind IN ('narrative','reporting')),
          display_name TEXT NOT NULL,
          description TEXT,
          provenance_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(provenance_json)),
          state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','removed')),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          last_seen_at TEXT,
          removed_at TEXT,
          UNIQUE(root_key, relative_path)
        );
        CREATE INDEX IF NOT EXISTS idx_sources_root_state ON sources(root_key,state);
        CREATE INDEX IF NOT EXISTS idx_sources_sha ON sources(source_sha256);
        """)
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='synced_files'").fetchone():
            cols = {r[1] for r in con.execute("PRAGMA table_info(synced_files)")}
            if "source_id" not in cols:
                con.execute("ALTER TABLE synced_files ADD COLUMN source_id TEXT")
            con.execute("CREATE INDEX IF NOT EXISTS idx_synced_files_source_id ON synced_files(source_id)")
        con.execute("INSERT OR REPLACE INTO brain_schema VALUES(?,?,?)", ("source-registry", SCHEMA_VERSION, now))


def _mime(path: Path) -> str:
    return {
        ".pdf": "application/pdf", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    }.get(path.suffix.lower(), "application/octet-stream")


def _kind(path: Path, explicit: str | None) -> str:
    if explicit:
        if explicit not in SOURCE_KINDS:
            raise ValueError(f"invalid source kind: {explicit}")
        return explicit
    return "reporting" if path.suffix.lower() in {".xlsx", ".xlsm", ".xls"} else "narrative"


def _provenance(raw: str | None) -> str:
    value = json.loads(raw or "{}")
    if not isinstance(value, dict):
        raise ValueError("provenance must be a JSON object")
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def register(con: sqlite3.Connection, root_key: str, rel: str, path: Path, *, source_id: str | None = None,
             kind: str | None = None, name: str | None = None, description: str | None = None,
             provenance: str | None = None, commit: bool = True) -> dict[str, Any]:
    rel = normalize_rel(rel)
    digest, size, now = sha_file(path), path.stat().st_size, utcnow()
    existing = con.execute("SELECT * FROM sources WHERE root_key=? AND relative_path=?", (root_key, rel)).fetchone()
    sid = source_id or (existing["source_id"] if existing else str(uuid.uuid4()))
    created = existing["created_at"] if existing else now
    prov = _provenance(provenance) if provenance is not None else (existing["provenance_json"] if existing else "{}")
    con.execute("""INSERT INTO sources(source_id,root_key,relative_path,source_sha256,byte_size,media_type,
                source_kind,display_name,description,provenance_json,state,created_at,updated_at,last_seen_at,removed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,'active',?,?,?,NULL)
                ON CONFLICT(source_id) DO UPDATE SET root_key=excluded.root_key,relative_path=excluded.relative_path,
                source_sha256=excluded.source_sha256,byte_size=excluded.byte_size,media_type=excluded.media_type,
                source_kind=excluded.source_kind,display_name=excluded.display_name,description=excluded.description,
                provenance_json=excluded.provenance_json,state='active',updated_at=excluded.updated_at,
                last_seen_at=excluded.last_seen_at,removed_at=NULL""",
                (sid, root_key, rel, digest, size, _mime(path), _kind(path, kind), name or path.name,
                 description if description is not None else (existing["description"] if existing else None),
                 prov, created, now, now))
    if commit:
        con.commit()
    return dict(con.execute("SELECT * FROM sources WHERE source_id=?", (sid,)).fetchone())


def iter_files(root: Path, includes: list[str]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for pattern in includes:
        for p in root.glob(pattern):
            if not p.is_file() or p.is_symlink():
                continue
            rel = normalize_rel(p.relative_to(root).as_posix())
            found[rel] = _resolved_inside(root, rel)
    return dict(sorted(found.items()))


def config_fingerprint(path: Path) -> str:
    return sha_file(path)


def build_plan(con: sqlite3.Connection, config: dict[str, Any], root_filter: str | None = None) -> dict[str, Any]:
    rows = [dict(r) for r in con.execute("SELECT * FROM sources WHERE state='active' ORDER BY root_key,relative_path")]
    if root_filter:
        if root_filter not in config["roots"]:
            raise ValueError(f"unknown source root: {root_filter}")
        rows = [r for r in rows if r["root_key"] == root_filter]
    roots_out, actions = [], []
    selected = [root_filter] if root_filter else sorted(config["roots"])
    for key in selected:
        spec = config["roots"][key]
        root: Path = spec["path"]
        registered = {r["relative_path"]: r for r in rows if r["root_key"] == key}
        if not root.is_dir():
            roots_out.append({"root_key": key, "mode": spec["mode"], "status": "root_unavailable"})
            continue
        disk = iter_files(root, spec["include"])
        roots_out.append({"root_key": key, "mode": spec["mode"], "status": "available", "files": len(disk)})
        missing = set(registered) - set(disk)
        new = set(disk) - set(registered)
        # Conservative rename detection: one-to-one unmatched SHA only.
        missing_by_sha: dict[str, list[str]] = {}
        for rel in missing:
            missing_by_sha.setdefault(registered[rel]["source_sha256"], []).append(rel)
        new_by_sha: dict[str, list[str]] = {}
        disk_meta: dict[str, tuple[str, int]] = {}
        for rel in new | (set(disk) & set(registered)):
            digest = sha_file(disk[rel]); size = disk[rel].stat().st_size
            disk_meta[rel] = (digest, size)
            if rel in new:
                new_by_sha.setdefault(digest, []).append(rel)
        moved_old, moved_new = set(), set()
        for digest, olds in missing_by_sha.items():
            news = new_by_sha.get(digest, [])
            if len(olds) == len(news) == 1:
                old, nxt = olds[0], news[0]
                actions.append({"action": "move", "source_id": registered[old]["source_id"], "root_key": key,
                                "from": old, "relative_path": nxt, "sha256": digest, "byte_size": disk_meta[nxt][1]})
                moved_old.add(old); moved_new.add(nxt)
        for rel in sorted(new - moved_new):
            digest, size = disk_meta[rel]
            actions.append({"action": "add", "root_key": key, "relative_path": rel, "sha256": digest, "byte_size": size})
        for rel in sorted((set(disk) & set(registered))):
            digest, size = disk_meta[rel]
            if digest != registered[rel]["source_sha256"]:
                actions.append({"action": "content_change", "source_id": registered[rel]["source_id"], "root_key": key,
                                "relative_path": rel, "old_sha256": registered[rel]["source_sha256"],
                                "sha256": digest, "byte_size": size})
        for rel in sorted(missing - moved_old):
            action = "remove_candidate" if spec["mode"] == "mirror" else "corrupt" if spec["mode"] == "managed" else "missing"
            actions.append({"action": action, "source_id": registered[rel]["source_id"], "root_key": key,
                            "relative_path": rel, "sha256": registered[rel]["source_sha256"]})
    return {"version": 1, "created_at": utcnow(), "config_sha256": config_fingerprint(config["path"]),
            "roots": roots_out, "actions": actions}


def json_out(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def cmd_init(a) -> None:
    with connect(a.db) as con:
        ensure_schema(con)
    print(f"source registry schema v{SCHEMA_VERSION} ready -> {a.db}")


def cmd_list(a) -> None:
    with connect_readonly(a.db) as con:
        state_sql, params = "", []
        if a.state != "all": state_sql = " WHERE state=?"; params.append(a.state)
        if a.root:
            state_sql += " AND" if state_sql else " WHERE"
            state_sql += " root_key=?"; params.append(a.root)
        rows = [dict(r) for r in con.execute("SELECT * FROM sources" + state_sql + " ORDER BY display_name,source_id", params)]
    if a.json: json_out(rows)
    else:
        for r in rows: print(f"{r['source_id']}  {r['state']:7}  {r['source_kind']:9}  {r['root_key']}:{r['relative_path']}  {r['display_name']}")


def cmd_get(a) -> None:
    with connect_readonly(a.db) as con:
        row = con.execute("SELECT * FROM sources WHERE source_id=?", (a.source_id,)).fetchone()
        if not row: raise KeyError(f"unknown source_id: {a.source_id}")
        result = dict(row)
        docs = []
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='synced_files'").fetchone():
            cols = {r[1] for r in con.execute("PRAGMA table_info(synced_files)")}
            if "source_id" in cols:
                for d in con.execute("SELECT doc_id FROM synced_files WHERE source_id=? ORDER BY doc_id", (a.source_id,)):
                    chunks = con.execute("SELECT COUNT(*) FROM chunks WHERE source=?", (d[0],)).fetchone()[0] if con.execute("SELECT 1 FROM sqlite_master WHERE name='chunks'").fetchone() else 0
                    docs.append({"doc_id": d[0], "chunks": chunks})
        result["documents"] = docs
    config = load_config(resolve_config(a.db, a.config))
    spec = config["roots"].get(result["root_key"])
    if spec:
        try:
            p = _resolved_inside(spec["path"], result["relative_path"], must_exist=False)
            result["available"] = p.is_file()
            result["resolved_path"] = str(p)
        except ValueError:
            result["available"] = False
    json_out(result)


def cmd_adopt(a) -> None:
    config = load_config(resolve_config(a.db, a.config))
    if a.root not in config["roots"]: raise ValueError(f"unknown source root: {a.root}")
    root = config["roots"][a.root]["path"]
    path = _resolved_inside(root, a.path)
    if not path.is_file() or path.is_symlink(): raise ValueError("source must be a regular non-symlink file")
    with connect(a.db) as con:
        ensure_schema(con)
        row = register(con, a.root, normalize_rel(a.path), path, source_id=a.source_id, kind=a.kind,
                       name=a.name, description=a.description, provenance=a.provenance)
    json_out(row)


def sanitize_name(name: str) -> str:
    base = Path(name).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip(".-")
    return stem or "attachment"


def cmd_import(a) -> None:
    src = Path(a.attachment).expanduser().resolve(strict=True)
    if not src.is_file() or src.is_symlink(): raise ValueError("attachment must be a regular non-symlink file")
    config = load_config(resolve_config(a.db, a.config))
    if a.root not in config["roots"]: raise ValueError(f"unknown source root: {a.root}")
    spec = config["roots"][a.root]
    if spec["mode"] != "managed": raise ValueError("source import requires a managed root")
    root: Path = spec["path"]
    root.mkdir(parents=True, exist_ok=True)
    digest = sha_file(src)
    name = sanitize_name(a.name or src.name)
    rel = normalize_rel(f"{digest[:12]}-{name}")
    dest = _resolved_inside(root, rel, must_exist=False)
    if dest.exists() and sha_file(dest) != digest: raise FileExistsError(f"managed destination collision: {rel}")
    copied = False
    if not dest.exists():
        fd, tmpname = tempfile.mkstemp(prefix=".incoming-", dir=root)
        os.close(fd)
        tmp = Path(tmpname)
        try:
            shutil.copyfile(src, tmp)
            if sha_file(tmp) != digest: raise IOError("attachment changed during copy")
            os.replace(tmp, dest); copied = True
        finally:
            if tmp.exists(): tmp.unlink()
    try:
        with connect(a.db) as con:
            ensure_schema(con)
            row = register(con, a.root, rel, dest, source_id=a.source_id, kind=a.kind,
                           name=a.name or src.name, description=a.description, provenance=a.provenance)
    except Exception:
        if copied and dest.exists(): dest.unlink()
        raise
    json_out(row)


def cmd_plan(a) -> None:
    config_path = resolve_config(a.db, a.config)
    config = load_config(config_path)
    with connect_readonly(a.db) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sources'").fetchone():
            raise RuntimeError("source registry is not initialized; run source init")
        plan = build_plan(con, config, a.root)
    if a.out:
        Path(a.out).write_text(json.dumps(plan, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    json_out(plan)


def cmd_apply(a) -> None:
    plan_path = Path(a.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    config_path = resolve_config(a.db, a.config)
    config = load_config(config_path)
    if plan.get("config_sha256") != config_fingerprint(config_path): raise ValueError("brain.toml changed since this plan was created")
    if plan.get("version") != 1 or not isinstance(plan.get("actions"), list):
        raise ValueError("unsupported or malformed source plan")
    safe = {"add", "content_change", "move"}
    con = connect(a.db)
    try:
        ensure_schema(con)
        con.commit()
        prepared = []
        for item in plan["actions"]:
            if item.get("action") not in safe: continue
            action = item["action"]
            key, rel = item["root_key"], normalize_rel(item["relative_path"])
            if key not in config["roots"]: raise ValueError(f"unknown source root: {key}")
            path = _resolved_inside(config["roots"][key]["path"], rel)
            if sha_file(path) != item.get("sha256"): raise ValueError(f"source changed since plan: {key}:{rel}")
            old = None
            if action == "add":
                if con.execute("SELECT 1 FROM sources WHERE root_key=? AND relative_path=?", (key, rel)).fetchone():
                    raise ValueError(f"stale add action: {key}:{rel} is already registered")
            else:
                old = con.execute("SELECT * FROM sources WHERE source_id=?", (item.get("source_id"),)).fetchone()
                if not old or old["state"] != "active": raise ValueError(f"stale source action: {item.get('source_id')}")
                if action == "content_change":
                    if old["root_key"] != key or old["relative_path"] != rel or old["source_sha256"] != item.get("old_sha256"):
                        raise ValueError(f"stale content_change action: {item.get('source_id')}")
                elif old["root_key"] != key or old["relative_path"] != item.get("from") or old["source_sha256"] != item.get("sha256"):
                    raise ValueError(f"stale move action: {item.get('source_id')}")
            prepared.append((item, path, old))
        con.execute("BEGIN IMMEDIATE")
        for item, path, old in prepared:
            if item["action"] == "add":
                register(con, item["root_key"], item["relative_path"], path, commit=False)
            else:
                register(con, item["root_key"], item["relative_path"], path, source_id=item["source_id"],
                         kind=old["source_kind"], name=old["display_name"], description=old["description"],
                         provenance=old["provenance_json"], commit=False)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    print("applied safe source metadata actions; removal candidates require `source remove`")


def cmd_remove(a) -> None:
    with connect(a.db) as con:
        ensure_schema(con)
        rows = [con.execute("SELECT * FROM sources WHERE source_id=?", (sid,)).fetchone() for sid in a.source_id]
        missing = [sid for sid, row in zip(a.source_id, rows) if not row]
        if missing: raise KeyError("unknown source_id(s): " + ", ".join(missing))
        impacts = []
        have_docs = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='synced_files'").fetchone()
        doc_cols = {r[1] for r in con.execute("PRAGMA table_info(synced_files)")} if have_docs else set()
        have_chunks = bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks'").fetchone())
        for row in rows:
            docs = con.execute("SELECT doc_id FROM synced_files WHERE source_id=?", (row["source_id"],)).fetchall() if "source_id" in doc_cols else []
            chunks = sum(con.execute("SELECT COUNT(*) FROM chunks WHERE source=?", (d[0],)).fetchone()[0] for d in docs) if have_chunks else 0
            impacts.append({"source_id": row["source_id"], "source": f"{row['root_key']}:{row['relative_path']}", "documents": len(docs), "chunks": chunks})
        json_out({"will_tombstone": impacts})
        if not a.yes:
            raise RuntimeError("confirmation required; rerun with --yes")
        config = load_config(resolve_config(a.db, a.config)) if a.delete_managed_file else None
        managed_paths = []
        if a.delete_managed_file:
            for row in rows:
                spec = config["roots"].get(row["root_key"])
                if not spec or spec["mode"] != "managed":
                    raise ValueError("--delete-managed-file is valid only for managed roots")
                p = _resolved_inside(spec["path"], row["relative_path"], must_exist=False)
                if p.is_symlink() or not p.is_file():
                    raise RuntimeError(f"managed source file unavailable: {row['relative_path']}")
                if sha_file(p) != row["source_sha256"]:
                    raise RuntimeError(f"managed source changed; refusing to delete: {row['relative_path']}")
                managed_paths.append(p)
        now = utcnow()
        with con:
            for row in rows:
                con.execute("UPDATE sources SET state='removed',removed_at=?,updated_at=? WHERE source_id=?", (now, now, row["source_id"]))
        for p in managed_paths:
            p.unlink()
    print("source(s) tombstoned; run brain_sync plan/apply to remove linked derived documents")


def cmd_restore(a) -> None:
    config = load_config(resolve_config(a.db, a.config))
    with connect(a.db) as con:
        ensure_schema(con)
        row = con.execute("SELECT * FROM sources WHERE source_id=?", (a.source_id,)).fetchone()
        if not row: raise KeyError(f"unknown source_id: {a.source_id}")
        spec = config["roots"].get(row["root_key"])
        if not spec: raise ValueError(f"unknown source root: {row['root_key']}")
        path = _resolved_inside(spec["path"], row["relative_path"])
        if sha_file(path) != row["source_sha256"]: raise ValueError("source bytes differ; use source plan/apply instead")
        now = utcnow()
        with con:
            con.execute("UPDATE sources SET state='active',removed_at=NULL,updated_at=?,last_seen_at=? WHERE source_id=?", (now, now, a.source_id))
    print(f"restored {a.source_id}")


def cmd_migrate(a) -> None:
    config = load_config(resolve_config(a.db, a.config))
    if a.root not in config["roots"]: raise ValueError(f"unknown source root: {a.root}")
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    if not isinstance(manifest, list): raise ValueError("manifest must be a JSON array")
    con = connect(a.db)
    mapped = []
    try:
        ensure_schema(con); con.commit(); con.execute("BEGIN IMMEDIATE")
        for item in manifest:
            src_rel, doc_id = normalize_rel(item["source"]), normalize_rel(item["md"])
            path = _resolved_inside(config["roots"][a.root]["path"], src_rel)
            if not con.execute("SELECT 1 FROM synced_files WHERE doc_id=?", (doc_id,)).fetchone():
                raise ValueError(f"documents row not found: {doc_id}")
            row = register(con, a.root, src_rel, path, commit=False)
            con.execute("UPDATE synced_files SET source_id=? WHERE doc_id=?", (row["source_id"], doc_id))
            mapped.append({"source_id": row["source_id"], "doc_id": doc_id})
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    json_out({"mapped": mapped, "problems": []})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Portable source registry for a Brain store")
    ap.add_argument("--db", required=True)
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(func=cmd_init)
    p = sub.add_parser("list"); p.add_argument("--root"); p.add_argument("--state", choices=("active","removed","all"), default="active"); p.add_argument("--json", action="store_true"); p.set_defaults(func=cmd_list)
    p = sub.add_parser("get"); p.add_argument("source_id"); p.add_argument("--json", action="store_true"); p.set_defaults(func=cmd_get)
    for name in ("adopt", "import"):
        p = sub.add_parser(name)
        if name == "adopt": p.add_argument("--root", required=True); p.add_argument("path")
        else: p.add_argument("attachment"); p.add_argument("--root", default="incoming")
        p.add_argument("--source-id"); p.add_argument("--kind", choices=sorted(SOURCE_KINDS)); p.add_argument("--name")
        p.add_argument("--description"); p.add_argument("--provenance")
        p.set_defaults(func=cmd_adopt if name == "adopt" else cmd_import)
    p = sub.add_parser("plan"); p.add_argument("--root"); p.add_argument("--out"); p.add_argument("--json", action="store_true"); p.set_defaults(func=cmd_plan)
    p = sub.add_parser("apply"); p.add_argument("--plan", required=True); p.set_defaults(func=cmd_apply)
    p = sub.add_parser("remove"); p.add_argument("source_id", nargs="+"); p.add_argument("--yes", action="store_true"); p.add_argument("--delete-managed-file", action="store_true"); p.set_defaults(func=cmd_remove)
    p = sub.add_parser("restore"); p.add_argument("source_id"); p.set_defaults(func=cmd_restore)
    p = sub.add_parser("migrate"); p.add_argument("--root", required=True); p.add_argument("--manifest", required=True); p.set_defaults(func=cmd_migrate)
    a = ap.parse_args(argv)
    try:
        a.func(a); return 0
    except (ValueError, KeyError, FileNotFoundError, FileExistsError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"source registry error: {exc}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
