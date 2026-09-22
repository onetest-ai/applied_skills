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
VP  = SKILLS / "visual-parse"

# make the skill dir importable so `import brain_doctor` (single source of truth
# for system tool checks) resolves regardless of the caller's cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))


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

NARRATIVE_EXT = {".pdf", ".pptx", ".ppt", ".docx", ".doc", ".md", ".markdown", ".txt", ".vtt", ".srt", ".html", ".htm",
                 ".mp4", ".mov", ".mkv", ".webm", ".m4v"}
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
    import brain_doctor  # same skill dir; single source of truth for system tools
    scan = brain_doctor.scan_corpus(corpus=str(docs) if docs is not None else None)
    print()
    print(brain_doctor.format_report(brain_doctor.run_checks(scan)))
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

    for sub in ["schema", "parsed", "taxonomy", "classify", "vision", "marts", "vault", ".incoming"]:
        (proj / sub).mkdir(parents=True, exist_ok=True)

    fam = proj / "schema" / f"families.{corpus}.json"
    met = proj / "schema" / f"metrics.{corpus}.json"
    if not fam.exists():
        _copy_template(TSL / "families.example.json", fam, corpus)
    if not met.exists():
        _copy_template(TSL / "metrics.example.json", met, corpus)
    (proj / "goal.txt").write_text((a.goal or "") + "\n")
    (proj / "name.txt").write_text((a.name or "") + "\n")
    config = proj / "brain.toml"
    if not config.exists():
        def rel_or_abs(path):
            if path is None: return ""
            try: return Path(os.path.relpath(path, proj)).as_posix()
            except ValueError: return str(path)
        docs_path = rel_or_abs(docs)
        reporting_path = rel_or_abs(reporting)
        lines = ["version = 1", "", "[paths]", 'parsed = "parsed"', "",
                 # Canonical audience (who consumes the KB): distinct from
                 # [deployment].target (distribution/infra). Drives taxonomy emphasis
                 # + kb answer/artifact altitude. Goal stays authoritative in goal.txt.
                 "[project]", f"audience = {json.dumps(a.audience or '')}", "",
                 "[sources.roots.incoming]", 'path = ".incoming"', 'mode = "managed"',
                 'include = ["**/*"]']
        if docs:
            lines += ["", "[sources.roots.docs]", f"path = {json.dumps(docs_path)}", f"mode = {json.dumps(a.docs_mode)}",
                      'include = ["**/*.pdf", "**/*.ppt", "**/*.pptx", "**/*.doc", "**/*.docx", "**/*.vtt", "**/*.srt", "**/*.json", "**/*.md", "**/*.markdown", "**/*.txt", "**/*.html", "**/*.htm", "**/*.mp4", "**/*.mov", "**/*.mkv", "**/*.webm", "**/*.m4v"]']
        if reporting:
            lines += ["", "[sources.roots.reporting]", f"path = {json.dumps(reporting_path)}", f"mode = {json.dumps(a.reporting_mode)}",
                      'include = ["**/*.xlsx", "**/*.xlsm", "**/*.xls"]']
        # Record the consumption model chosen at onboarding so downstream steps and
        # the operator guide (BRAIN.md/AGENTS.md) don't have to be reshaped later.
        lines += ["", "[deployment]", f"target = {json.dumps(a.deploy_target)}"]
        config.write_text("\n".join(lines) + "\n")

    # drop the self-discovering launcher at the project root so nothing hardcodes
    # an interpreter/path: `./brain which|search|sql|verify|py`
    launcher = Path(__file__).resolve().parent / "brain"
    if launcher.exists():
        shutil.copy2(launcher, proj / "brain")
        os.chmod(proj / "brain", 0o755)

    plan = _plan_text(proj, corpus, db, docs, reporting, fam, met, a.goal, a.deploy_target, a.audience, a.name)
    (proj / "BRAIN.md").write_text(plan)

    print(f"scaffolded project: {proj}")
    print(f"  corpus name : {corpus}")
    print(f"  store (db)  : {db}")
    print(f"  configs     : brain.toml, {fam.name}, {met.name}  (edit workbook mappings as needed)")
    print(f"  plan written: {proj/'BRAIN.md'}\n")
    cmd_scan(argparse.Namespace(docs=str(docs) if docs else None))
    print(f"\nNext: edit schema/{fam.name} to describe your reporting workbooks, then follow BRAIN.md.")
    return 0


def _plan_text(proj, corpus, db, docs, reporting, fam, met, goal, deploy_target="local", audience="", name=""):
    docs_s = str(docs) if docs else "<docs-dir>"
    rep_s = str(reporting) if reporting else "<reporting-dir>"
    py = brain_py()
    # Seeding is build correctness, not a name-dependent nicety: `brain_sync.py seed`
    # populates the `synced_files` tracking table that every later maintenance pass
    # requires. An anonymous project (no --name) still needs it — skipping it left
    # anonymous projects' first maintenance run failing with "synced_files table
    # missing; run brain_sync seed first". So step 9 is ALWAYS emitted; only the note
    # about also recording the display name is conditional on `name`.
    # step9 is interpolated into the OUTER textwrap.dedent(f"""...""") below at column 0
    # (no leading spaces before "{step9}" in that template), and the f-string is evaluated
    # BEFORE the outer dedent runs. So step9's own lines must already carry the same
    # 4-space indentation as every other line in that literal — an inner dedent()-then-drop
    # (zero-indent) string would make the common leading prefix across the WHOLE outer
    # document "", turning the outer dedent into a no-op and indenting the entire generated
    # BRAIN.md by four spaces (which Markdown then renders as one indented code block).
    if name:
        name_note = textwrap.dedent(f"""
        #     This also records the Brain's display name into the durable `meta` table via
        #     name.txt, not raw SQL: it was written to `{proj/'name.txt'}` by scaffold, and
        #     brain_sync's seed/apply path (see SKILL.md's plan/apply/seed sequence) UPSERTs
        #     it into meta.name on this same seed call.""").strip("\n")
    else:
        name_note = ""
    step9_body_lines = [
        "# 9 · seed the `synced_files` tracking table — required before any maintenance",
        "#     pass; an unseeded store fails its first maintenance run with \"synced_files",
        "#     table missing\". Always run this, named project or not.",
    ]
    if name_note:
        step9_body_lines.append(name_note)
    step9_body_lines.append(
        f'"$PY" "{Path(__file__).resolve().parent/"brain_sync.py"}" seed --db "$DB" '
        f'--parsed "{proj/"parsed"}" --require-goal'
    )
    step9_body = "\n".join(step9_body_lines)
    step9 = "\n" + textwrap.indent(step9_body, "    ") + "\n"
    deploy_section = textwrap.dedent({
        "local": """
    ## Deployment target: local
    This brain is consumed **locally** — an answering agent queries the store over stdio.
    Register the MCP with `./brain mcp-config` (stdio) and answer via the hybrid-retrieval
    skill. No hosting, auth, or TLS needed. If this later becomes a hosted service, switch
    `[deployment].target` in brain.toml to `hosted-mcp` and follow the hosted guidance.
    """,
        "hosted-mcp": """
    ## Deployment target: hosted-mcp
    This brain will be served as a **governed MCP to remote clients** (e.g. Copilot Studio).
    Plan for this from the start so the operator guide isn't rewritten later:
    - Author operator docs for a *server* (auth, transport, image build/revisions), not a
      local Q&A agent.
    - Enable HTTP transport deliberately; set `BRAIN_API_KEY` (X-API-Key), require TLS,
      authorization, key rotation, rate limits, and auditing (see `mcp/brain/README.md`).
    - Use the **brain-maintenance** skill's deployment profile (`profile.example.toml`);
      deployment stays an agent-owned, human-gated external step.
    - Never update the deployed store in place — ship an immutable image/revision, and
      **resync every distributable copy** of `knowledge.sqlite` when the store rebuilds.
    """,
    }[deploy_target])
    return deploy_section + "\n" + textwrap.dedent(f"""\
    # Brain build plan — {corpus}

    **Goal (noise filter):** {goal or "<state your analytical goal>"}

    **Audience:** {audience or "<who will consume the KB — roles/personas>"} — refines
    taxonomy emphasis (secondary lens under the goal) and how the `kb` plugin sets answer
    altitude/vocabulary and authored-artifact tone/depth. Canonical in `brain.toml`
    `[project].audience`; distinct from `[deployment].target` (distribution/infra).

    **Name (optional):** {name or "<none — this Brain is anonymous>"} — the display name
    a client shows when several Brains are connected. Written to `name.txt`. A Brain with
    no name is fully functional; a client juggling several just tells them apart by
    connector name and goal instead.

    **Store:** `{db}` — one portable SQLite file (chunks+FTS+vector · facts · graph).
    **Source config:** `{proj/'brain.toml'}` — named roots with paths relative to this project.
    Rule: *meaning is agentic, numbers are computed.* Every answer cited or "not modeled".

    **Python:** `$PY` below is the skills' own project-local venv (isolated from your
    project's deps). Create it once with:
    `./install.sh --bundle brain --deps` (or `npx … init --bundle brain --deps`).

    ## Source roots and registry

    `brain.toml` was generated with these defaults:
    - `incoming` → `.incoming`, mode `managed` (Brain-owned chat attachments)
    - `docs` → the supplied narrative folder, mode `import`
    - `reporting` → the supplied workbook folder, mode `import`

    `import` discovers add/change but never infers deletion from absence. Change a root to
    `mirror` only if that available directory is authoritative and missing members should
    become human-approved removal candidates. If a root is unavailable, stop; never treat
    it as an empty corpus. Paths are resolved relative to `brain.toml`, so moving the project
    and its local mother-source folders preserves their logical identities.

    ```bash
    DB="{db}"
    PY="{py}"          # the brain venv interpreter (BRAIN_PY)

    # Initialize source metadata schema, inspect discovery, then approve safe metadata actions.
    ./brain source init
    ./brain source plan --out "{proj/'source_plan.json'}"
    ./brain source apply --plan "{proj/'source_plan.json'}"

    # One explicit existing file:
    # ./brain source adopt --root docs "relative/path/inside/docs.pdf" --description "..."
    # Chat attachment (temporary host path → durable project-local .incoming):
    # ./brain source import "/temporary/attachment.pdf" --root incoming \
    #   --provenance '{{"attachment_id":"…","conversation_id":"…"}}'
    ```

    ## Build sequence
    Steps marked 🤖 are **low-tier agents** (judgment), the rest are deterministic scripts.

    ```bash
    DB="{db}"
    PY="{py}"          # the brain venv interpreter (BRAIN_PY)

    # 0 · preflight — every system tool THIS corpus needs; stop if it exits 1
    "$PY" "{Path(__file__).resolve().parent/'brain_doctor.py'}" --config "{proj/'brain.toml'}"

    # 1a · parse transcripts → Markdown (VTT/SRT corpora only — skip if no transcripts)
    #      --merge-cues joins same-speaker cues into speaker turns; omitting it produces
    #      ~25k single-line chunks that agents classify as [] and retrieval quality collapses.
    "$PY" "{CTE/'parse_corpus.py'}" --corpus "{docs_s}" --out "{proj/'parsed'}" --formats vtt,srt --merge-cues 10

    # 1b · parse narrative docs → Markdown (pymupdf text; visual pages via visual-parse)
    #      If the corpus has meeting recordings, run step 1m (below) BEFORE this step, so a
    #      Teams transcript .docx is consumed by its recording rather than parsed by soffice.
    #      md/markdown/txt pass through untouched — already-Markdown corpora need no conversion.
    "$PY" "{CTE/'parse_corpus.py'}" --corpus "{docs_s}" --out "{proj/'parsed'}" --formats pptx,docx,pdf,md,markdown,txt

    # 1m · meeting recordings (.mp4/.mov/…) — the visual-parse "Meeting recordings" lane
    #      (run it BEFORE 1b when the corpus has recordings):
    #      probe → (transcribe, only when no transcript: same-stem .vtt/.srt/.docx, or a Teams
    #      .docx whose first line is the recording name) → frames → vision_prep → 🤖 VLM → assemble
    #      "$PY" "{VP/'video_capture.py'}" probe --video <rel> --rel-to "{docs_s}" --work "{proj/'video'}"
    #      (full sequence: visual-parse/SKILL.md → Meeting recordings)
    #      assemble retires the recording's transcript doc; later step-1a/1b runs skip that
    #      .vtt/.srt/.docx on their own (only while the recording has a video-lane doc in parsed/
    #      whose manifest inputs list that file — no flag).

    # 2 · 🤖 induce taxonomy (map→reduce→judge→emit) → taxonomy/taxonomy_v0.json
    #     see corpus-taxonomy-extraction/SKILL.md; goal = above. Dispatch Haiku subagents.
    #     Keep the reduce output for review: consolidate.py --out "{proj/'taxonomy'/'work'/'consolidated.json'}"

    # 2b · 👤 human review of the draft — the local review app runs until the reviewer submits.
    #      Agent: run `serve` in the BACKGROUND and end the turn; its exit wakes you.
    #      serve binds 127.0.0.1 on a free port, prints "review app: <url>" on stderr and opens
    #      the browser; it exits on submit, on cancel, or after --timeout seconds (default 3600).
    #      Apply only when its JSON line says "submitted"; otherwise run serve again to continue.
    #      First build only — skip if taxonomy/current.json already exists.
    REVIEW=$("$PY" "{CTE/'taxonomy_review.py'}" plan --mode draft --taxonomy "{proj/'taxonomy'/'taxonomy_v0.json'}" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["review"])')
    "$PY" "{CTE/'taxonomy_review.py'}" serve --review "$REVIEW"
    "$PY" "{CTE/'taxonomy_merge.py'}" --review "$REVIEW" --apply      # → taxonomy_v1.json + taxonomy/current.json

    # 3 · narrative index — heading-aware sections → chunks + FTS5 + vector
    "$PY" "{KI/'knowledge_index.py'}" index --db "$DB" --corpus "{proj/'parsed'}" --reset

    # 4 · taxonomy graph (L1/L2 vertices) into the SAME db — always from current.json
    "$PY" "{CTE/'build_graph.py'}" --taxonomy "{proj/'taxonomy'/'current.json'}" --db "$DB"

    # 5 · 🤖 per-section tags — prep, dispatch Haiku subagents, write
    "$PY" "{CTE/'classify_prep.py'}" --db "$DB" --taxonomy "{proj/'taxonomy'/'current.json'}" --out "{proj/'classify'}" --batches 25
    #     → N Haiku subagents read classify/{{instructions,vocab,batch_k}} → write classify/result_k.json
    "$PY" "{CTE/'classify_write.py'}" --db "$DB" --results "{proj/'classify'}"

    # 6 · temporal fact intake (docs + transcripts → evidence-backed assertions)
    "$PY" "{KI/'fact_prep.py'}" --db "$DB" --out "{proj/'facts'}"
    # 🤖 dispatch low-tier agents: read facts/instructions.md + facts/batch_*.json → facts/result_*.json
    "$PY" "{KI/'fact_write.py'}" --db "$DB" --results "{proj/'facts'}" --report "{proj/'facts'/'fact_intake_report.json'}" --apply

    # 7 · numeric marts (Excel → facts) into the SAME db   [edit {fam.name} first!]
    "$PY" "{TSL/'build_marts.py'}" --root "{rep_s}" --config "{fam}" --out-dir "{proj/'marts'}" --db "$DB" --strict

    # 8 · Obsidian vault = a VIEW of the store
    "$PY" "{CTE/'to_obsidian.py'}" --db "$DB" --out "{proj/'vault'}"
{step9}
    # verify the built store
    "$PY" "{Path(__file__).resolve()}" verify --db "$DB"
    ```

    ## Taxonomy review (after the first build)
    Every later taxonomy change goes through the same local review app. Ask Claude to
    "review the taxonomy" (the health review: every problem with a proposed fix) or to
    "open the taxonomy editor" (browse: change categories and metrics yourself). Claude
    follows corpus-taxonomy-extraction/SKILL.md → "Reviewing and editing the taxonomy":
    it plans the review, runs `serve` in the background, applies what you submit, then
    rebuilds the graph and reclassifies the affected sections. To open the editor by hand:

    ```bash
    "$PY" "{CTE/'taxonomy_review.py'}" plan --mode browse --taxonomy "{proj/'taxonomy'/'current.json'}" --db "$DB"
    "$PY" "{CTE/'taxonomy_review.py'}" serve --review <the "review" path it printed> --db "$DB"
    "$PY" "{CTE/'taxonomy_merge.py'}" --review <that path> --apply        # after you submit
    "$PY" "{CTE/'build_graph.py'}" --taxonomy "{proj/'taxonomy'/'current.json'}" --db "$DB"
    # then, if taxonomy/work/reclassify.json exists, reclassify those chunks (see the skill)
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

    # Classification completeness: batch validation only proves the DISPATCHED
    # chunks came back — it never checks coverage of the whole population. A
    # build can look "complete" with a large share of chunks carrying no topic,
    # silently degrading taxonomy-routed answers. Surface it here.
    total = count("chunks")
    unclassified = None
    no_topic = None
    if total and "chunk_topics" in tables:
        try:
            has_v = "chunk_verdicts" in tables
            unclassified = con.execute(
                "SELECT COUNT(*) FROM chunks c "
                "WHERE NOT EXISTS (SELECT 1 FROM chunk_topics t WHERE t.chunk_id = c.id)"
                + (" AND NOT EXISTS (SELECT 1 FROM chunk_verdicts v WHERE v.chunk_id = c.id AND v.verdict = 'no_topic')"
                   if has_v else "")
            ).fetchone()[0]
            # only no_topic verdicts, and only those whose chunk still exists
            no_topic = con.execute(
                "SELECT COUNT(*) FROM chunk_verdicts v WHERE v.verdict = 'no_topic' "
                "AND EXISTS (SELECT 1 FROM chunks c WHERE c.id = v.chunk_id)").fetchone()[0] if has_v else None
        except Exception:
            unclassified = None
    if unclassified is not None and total:
        pct = 100.0 * unclassified / total
        warn = " ⚠ high — review the classification pass" if pct > 10 else ""
        print(f"  {'unclassified':18s} {unclassified}/{total} chunks ({pct:.1f}%)" + (f" · {no_topic} no-topic" if no_topic else "") + warn)

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

    tdir = Path(a.taxonomy_dir).expanduser() if a.taxonomy_dir else db.parent.parent / "taxonomy"
    if (tdir / "PROVISIONAL").exists():
        print("\n  ⚠ PROVISIONAL taxonomy: the first-build review has not been applied — run the health review "
              "(corpus-taxonomy-extraction → \"A. First-build review\")")
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description="Guided onboarding for the knowledge-pipeline (brain) orchestrator.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scaffold", help="create project layout + config templates + BRAIN.md plan, then preflight & scan")
    s.add_argument("--project", required=True, help="project dir to create/populate")
    s.add_argument("--goal", default="", help="analytical goal string (the noise filter)")
    s.add_argument("--name", help="display name for this Brain, e.g. 'ACME Contact Centre' "
                   "— what a client shows when several Brains are connected")
    s.add_argument("--audience", default="", help="who will consume the KB — roles/personas "
                   "(e.g. 'call-center ops managers and workforce planners'). Canonical in "
                   "brain.toml [project].audience; drives taxonomy emphasis + kb answer/artifact "
                   "style. Distinct from --deploy-target (distribution/infra).")
    s.add_argument("--docs", help="dir of narrative docs (pdf/pptx/docx)")
    s.add_argument("--reporting", help="dir of reporting spreadsheets (defaults to --docs)")
    s.add_argument("--docs-mode", choices=("import", "mirror"), default="import",
                   help="source-root semantics for narrative docs (default: safe import)")
    s.add_argument("--reporting-mode", choices=("import", "mirror"), default="import",
                   help="source-root semantics for reporting files (default: safe import)")
    s.add_argument("--corpus", help="corpus name for config filenames (default: project dir name)")
    s.add_argument("--deploy-target", choices=("local", "hosted-mcp"), default="local",
                   help="how the brain will be consumed: 'local' (answering agent queries the "
                        "local store over stdio) or 'hosted-mcp' (governed MCP served to remote "
                        "clients, e.g. Copilot Studio — needs auth/TLS + a deployment profile). "
                        "Shapes BRAIN.md guidance; ask this at onboarding so the operator doc "
                        "isn't rewritten later.")
    s.set_defaults(func=cmd_scaffold)

    s = sub.add_parser("scan", help="preflight deps + split a docs dir into narrative vs reporting (no writes)")
    s.add_argument("--docs", help="dir to scan")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("verify", help="report per-lane counts of a built knowledge.sqlite + smoke query")
    s.add_argument("--db", required=True)
    s.add_argument("--taxonomy-dir", help="taxonomy directory (defaults to <db parent's parent>/taxonomy)")
    s.add_argument("--query", default="overview", help="smoke retrieval query")
    s.set_defaults(func=cmd_verify)

    a = ap.parse_args()
    sys.exit(a.func(a))


if __name__ == "__main__":
    main()
