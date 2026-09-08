#!/usr/bin/env python3
"""Guided-onboarding helper for the knowledge-pipeline orchestrator.

The *judgment* (questions, the goal string, which docs are narrative vs
reporting) stays with the agent driving the wizard. This script does the
deterministic parts so the wizard is reliable and copy-pasteable:

  scaffold  create the project layout + config templates + a filled-in BRAIN.md
            plan (real paths), then preflight deps and scan the corpus.
  scan      preflight only: check imports + split a docs dir into narrative
            vs reporting by extension (no writes).
  verify    open a built knowledge.sqlite and report per-lane row counts,
            warning on any empty lane + running a smoke retrieval.

The scripts referenced in the printed plan are resolved relative to this
skill's install location, so the plan works wherever the bundle is installed.
"""
import argparse, json, os, shutil, sqlite3, sys, textwrap
from pathlib import Path

# sibling skills live next to this one:  .../skills/<skill>/...
SKILLS = Path(__file__).resolve().parent.parent
CTE = SKILLS / "corpus-taxonomy-extraction"
KI  = SKILLS / "knowledge-index"
TSL = SKILLS / "tabular-semantic-layer"


def brain_py():
    """The interpreter that holds the skills' deps, in priority order:
      1. $BRAIN_PY (explicit override)
      2. the venv next to these installed skills (<host>/venv) — per-project or --user
      3. a shared user-level host venv (~/.claude/venv, ~/.dsh/venv, …)
      4. the current interpreter."""
    if os.environ.get("BRAIN_PY"):
        return os.environ["BRAIN_PY"]
    cands = [SKILLS.parent / "venv" / "bin" / "python"]          # <root>/.<host>/venv
    home = Path.home()
    cands += [home / d / "venv" / "bin" / "python"
              for d in (".claude", ".dsh", ".codex", ".copilot")]
    for c in cands:
        if c.exists():
            return str(c)
    return sys.executable

NARRATIVE_EXT = {".pdf", ".pptx", ".ppt", ".docx", ".doc", ".md", ".txt"}
REPORTING_EXT = {".xlsx", ".xlsm", ".xls", ".csv"}

# module -> pip name (for the preflight message). All torch-free (docling retired).
# .pptx/.docx also need LibreOffice `soffice` (system dep). Install into the skills'
# own venv (install.sh --deps [--user]), not the project's env.
DEPS = [("sqlite_vec", "sqlite-vec"), ("fastembed", "fastembed"),
        ("pymupdf", "pymupdf"), ("openpyxl", "openpyxl"),
        ("pandas", "pandas")]


def _imports():
    ok, missing = [], []
    for mod, pip in DEPS:
        try:
            __import__(mod); ok.append(mod)
        except Exception:
            missing.append(pip)
    return ok, missing


def _scan_docs(docs: Path):
    narrative, reporting, other = [], [], []
    if not docs or not docs.exists():
        return narrative, reporting, other
    for p in sorted(docs.rglob("*")):
        if not p.is_file() or p.name.startswith("~$"):
            continue
        e = p.suffix.lower()
        (narrative if e in NARRATIVE_EXT else reporting if e in REPORTING_EXT else other).append(p)
    return narrative, reporting, other


def cmd_scan(a):
    docs = Path(a.docs).expanduser() if a.docs else None
    ok, missing = _imports()
    print("== preflight ==")
    print(f"  python {sys.version.split()[0]}")
    print(f"  present: {', '.join(ok) or 'none'}")
    print(f"  MISSING: {', '.join(missing) or 'none'}"
          + (f"   →  pip install {' '.join(missing)}" if missing else ""))
    if docs is not None:
        nar, rep, oth = _scan_docs(docs)
        print(f"\n== corpus scan: {docs} ==")
        print(f"  narrative (index+taxonomy): {len(nar)}  {sorted({p.suffix.lower() for p in nar})}")
        print(f"  reporting (marts):          {len(rep)}  {sorted({p.suffix.lower() for p in rep})}")
        if oth:
            print(f"  ignored:                    {len(oth)}  {sorted({p.suffix.lower() for p in oth})}")
        if not nar:
            print("  ⚠ no narrative docs found — the RAG/taxonomy lanes will be empty.")
        if not rep:
            print("  ⚠ no reporting spreadsheets — the numeric (marts) lane will be empty.")
    return 0


def _copy_template(src: Path, dst: Path, corpus: str):
    """Copy an example config, retargeting the family/metric names lightly."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        data = src.read_text()
    else:
        data = "{}\n"
    dst.write_text(data)


def cmd_scaffold(a):
    proj = Path(a.project).expanduser().resolve()
    corpus = a.corpus or proj.name.lower().replace(" ", "_")
    docs = Path(a.docs).expanduser().resolve() if a.docs else None
    reporting = Path(a.reporting).expanduser().resolve() if a.reporting else docs
    db = proj / "schema" / "knowledge.sqlite"

    for sub in ["schema", "parsed", "taxonomy", "classify", "marts", "vault"]:
        (proj / sub).mkdir(parents=True, exist_ok=True)

    fam = proj / "schema" / f"families.{corpus}.json"
    met = proj / "schema" / f"metrics.{corpus}.json"
    if not fam.exists():
        _copy_template(TSL / "families.example.json", fam, corpus)
    if not met.exists():
        _copy_template(TSL / "metrics.example.json", met, corpus)
    (proj / "goal.txt").write_text((a.goal or "") + "\n")

    # drop the self-discovering launcher at the project root so nothing hardcodes
    # an interpreter/path: `./brain which|search|sql|verify|py`
    launcher = Path(__file__).resolve().parent / "brain"
    if launcher.exists():
        shutil.copy2(launcher, proj / "brain")
        os.chmod(proj / "brain", 0o755)

    plan = _plan_text(proj, corpus, db, docs, reporting, fam, met, a.goal)
    (proj / "BRAIN.md").write_text(plan)

    print(f"scaffolded project: {proj}")
    print(f"  corpus name : {corpus}")
    print(f"  store (db)  : {db}")
    print(f"  configs     : {fam.name}, {met.name}  (edit these to match your workbooks)")
    print(f"  plan written: {proj/'BRAIN.md'}\n")
    cmd_scan(argparse.Namespace(docs=str(docs) if docs else None))
    print(f"\nNext: edit schema/{fam.name} to describe your reporting workbooks, then follow BRAIN.md.")
    return 0


def _plan_text(proj, corpus, db, docs, reporting, fam, met, goal):
    docs_s = str(docs) if docs else "<docs-dir>"
    rep_s = str(reporting) if reporting else "<reporting-dir>"
    py = brain_py()
    return textwrap.dedent(f"""\
    # Brain build plan — {corpus}

    **Goal (noise filter):** {goal or "<state your analytical goal>"}

    **Store:** `{db}` — one portable SQLite file (chunks+FTS+vector · facts · graph).
    Rule: *meaning is agentic, numbers are computed.* Every answer cited or "not modeled".

    **Python:** `$PY` below is the skills' own project-local venv (isolated from your
    project's deps). Create it once with:
    `./install.sh --bundle brain --deps` (or `npx … init --bundle brain --deps`).

    ## Build sequence
    Steps marked 🤖 are **low-tier agents** (judgment), the rest are deterministic scripts.

    ```bash
    DB="{db}"
    PY="{py}"          # the brain venv interpreter (BRAIN_PY)

    # 1 · parse narrative docs → Markdown (pymupdf text; visual pages via visual-parse)
    "$PY" "{CTE/'parse_corpus.py'}" --corpus "{docs_s}" --out "{proj/'parsed'}" --formats pptx,docx,pdf

    # 2 · 🤖 induce taxonomy (map→reduce→judge→emit) → taxonomy/taxonomy_v0.json
    #     see corpus-taxonomy-extraction/SKILL.md; goal = above. Dispatch Haiku subagents.

    # 3 · narrative index — heading-aware sections → chunks + FTS5 + vector
    "$PY" "{KI/'knowledge_index.py'}" index --db "$DB" --corpus "{proj/'parsed'}" --reset

    # 4 · taxonomy graph (L1/L2 vertices) into the SAME db
    "$PY" "{CTE/'build_graph.py'}" --taxonomy "{proj/'taxonomy'/'taxonomy_v0.json'}" --db "$DB"

    # 5 · 🤖 per-section tags — prep, dispatch Haiku subagents, write
    "$PY" "{CTE/'classify_prep.py'}" --db "$DB" --taxonomy "{proj/'taxonomy'/'taxonomy_v0.json'}" --out "{proj/'classify'}" --batches 5
    #     → N Haiku subagents read classify/{{instructions,vocab,batch_k}} → write classify/result_k.json
    "$PY" "{CTE/'classify_write.py'}" --db "$DB" --results "{proj/'classify'}"

    # 6 · numeric marts (Excel → facts) into the SAME db   [edit {fam.name} first!]
    "$PY" "{TSL/'build_marts.py'}" --root "{rep_s}" --config "{fam}" --out-dir "{proj/'marts'}" --db "$DB" --strict

    # 7 · Obsidian vault = a VIEW of the store
    "$PY" "{CTE/'to_obsidian.py'}" --db "$DB" --out "{proj/'vault'}"

    # verify the built store
    "$PY" "{Path(__file__).resolve()}" verify --db "$DB"
    ```

    ## Answer
    Route each question via the **hybrid-retrieval** skill over `$DB`
    (numbers→facts SQL · narrative→RRF RAG · relations→graph JOINs · both→reconcile).
    """)


def cmd_verify(a):
    db = Path(a.db).expanduser()
    if not db.exists():
        print(f"error: no store at {db}"); return 1
    con = sqlite3.connect(str(db))
    try:
        con.enable_load_extension(True)
        import sqlite_vec  # noqa
        sqlite_vec.load(con)
    except Exception:
        pass
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}

    def count(t):
        if t not in tables:
            return None
        try:
            return con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception:
            return "?"

    lanes = {
        "narrative (RAG)": ["chunks", "chunks_fts", "chunks_vec"],
        "taxonomy graph": ["graph_nodes", "graph_edges", "chunk_topics"],
        "numbers (marts)": ["facts"],
    }
    print(f"== store: {db} ==")
    empty = []
    for lane, ts in lanes.items():
        parts = []
        for t in ts:
            c = count(t)
            parts.append(f"{t}={'—' if c is None else c}")
        counts = [count(t) for t in ts]
        primary = counts[0]
        if primary in (None, 0):
            empty.append(lane)
        flag = " ⚠ EMPTY" if primary in (None, 0) else ""
        print(f"  {lane:18s} {'  '.join(parts)}{flag}")

    # smoke retrieval
    if count("chunks"):
        try:
            sys.path.insert(0, str(KI))
            import knowledge_index as K  # noqa
            k = K.connect(str(db))
            res = K.search(k, K.DEFAULT_MODEL, a.query, 1)
            top = res["results"][0] if res["results"] else None
            print(f"\n  smoke query «{a.query}» → fts={res['fts_hits']} vec={res['vec_hits']}"
                  + (f", top: «{(top['title'] or '')[:48]}»" if top else " (no hits)"))
        except Exception as e:
            print(f"\n  smoke query skipped ({e})")

    print("\n" + ("✅ all lanes populated." if not empty
                   else f"⚠ empty lane(s): {', '.join(empty)} — run the missing build step(s)."))
    con.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description="Guided onboarding for the knowledge-pipeline (brain) orchestrator.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scaffold", help="create project layout + config templates + BRAIN.md plan, then preflight & scan")
    s.add_argument("--project", required=True, help="project dir to create/populate")
    s.add_argument("--goal", default="", help="analytical goal string (the noise filter)")
    s.add_argument("--docs", help="dir of narrative docs (pdf/pptx/docx)")
    s.add_argument("--reporting", help="dir of reporting spreadsheets (defaults to --docs)")
    s.add_argument("--corpus", help="corpus name for config filenames (default: project dir name)")
    s.set_defaults(func=cmd_scaffold)

    s = sub.add_parser("scan", help="preflight deps + split a docs dir into narrative vs reporting (no writes)")
    s.add_argument("--docs", help="dir to scan")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("verify", help="report per-lane counts of a built knowledge.sqlite + smoke query")
    s.add_argument("--db", required=True)
    s.add_argument("--query", default="overview", help="smoke retrieval query")
    s.set_defaults(func=cmd_verify)

    a = ap.parse_args()
    sys.exit(a.func(a))


if __name__ == "__main__":
    main()
