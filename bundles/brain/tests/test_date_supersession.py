import importlib.util, json, sqlite3, types
from pathlib import Path
import pytest
import knowledge_index as K

HERE = Path(__file__).resolve().parent.parent / "skills" / "knowledge-pipeline"
_spec = importlib.util.spec_from_file_location("brain_sync", HERE / "brain_sync.py")
S = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(S)

DOCS = {  # md -> (event_date, date_source)
    "old.pdf.md": ("2023-10-24", "text"),
    "new.pdf.md": ("2026-08-18", "filename"),
    "sep.vtt.md": (None, "none"),
    "manual.pdf.md": ("2026-05-01", "text"),
}

@pytest.fixture
def store(tmp_path):
    parsed = tmp_path / "parsed"; parsed.mkdir()
    (parsed / "manifest.json").write_text(json.dumps(
        [{"source": md[:-3], "md": md, "method": "x", "event_date": d, "date_source": s}
         for md, (d, s) in DOCS.items()]))
    c = K.connect(":memory:"); c.row_factory = sqlite3.Row   # vec0 needs sqlite_vec loaded
    K._ensure_schema(c, 384)
    for i, md in enumerate(DOCS):
        for j in range(2):
            c.execute("INSERT INTO chunks(id,source,ord,title,text,status,valid_from) "
                      "VALUES(?,?,?,?,?,'ACTIVE','2026-09-28')", (i * 10 + j, md, j, "t", "body"))
    c.execute("UPDATE chunks SET status='SUPERSEDED', valid_to='2026-06-01' WHERE source='manual.pdf.md'")
    return c, str(parsed)

def status(c):
    return {r["source"]: (r["status"], r["valid_to"]) for r in c.execute(
        "SELECT source, status, valid_to FROM chunks GROUP BY source")}

def test_no_cutoff_changes_no_status(store):
    c, parsed = store
    before = status(c)
    S.apply_dates(c, parsed, None, None)
    assert status(c) == before
    assert c.execute("SELECT event_date FROM chunks WHERE source='old.pdf.md'").fetchone()[0] == "2023-10-24"

def test_old_exact_dated_doc_is_superseded_not_deleted(store):
    c, parsed = store
    n = c.execute("SELECT count(*) FROM chunks").fetchone()[0]
    out = S.apply_dates(c, parsed, None, "2026-01-01")
    assert c.execute("SELECT count(*) FROM chunks").fetchone()[0] == n
    assert status(c)["old.pdf.md"] == ("SUPERSEDED", "2026-01-01")
    assert status(c)["new.pdf.md"] == ("ACTIVE", None)
    assert out["superseded"] == 2   # two chunks of old.pdf.md

def test_undated_and_partial_docs_stay_active(store):
    c, parsed = store
    S.apply_dates(c, parsed, None, "2030-01-01")          # everything dated is "old" now
    assert status(c)["sep.vtt.md"] == ("ACTIVE", None)

def test_removing_cutoff_reactivates_only_own_marks(store):
    c, parsed = store
    S.apply_dates(c, parsed, None, "2026-01-01")
    out = S.apply_dates(c, parsed, None, None)
    assert status(c)["old.pdf.md"] == ("ACTIVE", None)
    assert status(c)["manual.pdf.md"] == ("SUPERSEDED", "2026-06-01")   # manual mark untouched
    assert out["reactivated"] == 2

def test_chunk_ids_unchanged_by_marking_and_unmarking(store):
    c, parsed = store
    ids = {r[0] for r in c.execute("SELECT id FROM chunks")}
    S.apply_dates(c, parsed, None, "2026-01-01"); S.apply_dates(c, parsed, None, None)
    assert {r[0] for r in c.execute("SELECT id FROM chunks")} == ids

def test_bad_cutoff_value_is_refused(tmp_path):
    (tmp_path / "brain.toml").write_text('[corpus]\nsupersede_before = "2026/01/01"\n')
    (tmp_path / "schema").mkdir()
    with pytest.raises(ValueError):
        S.read_supersede_before(str(tmp_path / "schema" / "knowledge.sqlite"))


# --- I1: date_superseded must track chunk ids, not source, so a manually-superseded
# chunk of an otherwise-ACTIVE source is never re-marked or undone by the cutoff. ---

def chunk_status(c, source):
    return {r[0]: (r[1], r[2]) for r in c.execute(
        "SELECT id, status, valid_to FROM chunks WHERE source=? ORDER BY id", (source,))}


def test_manual_supersede_on_one_chunk_of_active_source_survives_cutoff_cycles(store):
    c, parsed = store
    docs = dict(DOCS)
    docs["mixed.pdf.md"] = ("2023-01-01", "text")
    Path(parsed, "manifest.json").write_text(json.dumps(
        [{"source": md[:-3], "md": md, "method": "x", "event_date": d, "date_source": s}
         for md, (d, s) in docs.items()]))
    c.execute("INSERT INTO chunks(id,source,ord,title,text,status,valid_from,valid_to) "
              "VALUES(9000,'mixed.pdf.md',0,'t','body','SUPERSEDED','2026-09-28','2025-06-01')")
    c.execute("INSERT INTO chunks(id,source,ord,title,text,status,valid_from) "
              "VALUES(9001,'mixed.pdf.md',1,'t','body','ACTIVE','2026-09-28')")

    S.apply_dates(c, parsed, None, "2026-01-01")
    S.apply_dates(c, parsed, None, "2026-01-01")  # second identical run (idempotent refresh)
    st = chunk_status(c, "mixed.pdf.md")
    assert st[9000] == ("SUPERSEDED", "2025-06-01")   # manual mark never re-marked
    assert st[9001] == ("SUPERSEDED", "2026-01-01")   # auto-hidden by the cutoff

    S.apply_dates(c, parsed, None, None)              # cutoff removed
    st = chunk_status(c, "mixed.pdf.md")
    assert st[9000] == ("SUPERSEDED", "2025-06-01")   # manual mark still untouched
    assert st[9001] == ("ACTIVE", None)               # only the auto-hidden chunk comes back


def test_migrates_old_per_source_schema_before_dropping_it(store):
    """A store built by this branch's own earlier (unreleased) version has the old
    per-source date_superseded table. Its rows must be migrated to the new chunk-keyed
    schema — provably-ours chunks only (status SUPERSEDED with valid_to == the recorded
    cutoff) — before the table is replaced, or auto-marks are silently orphaned: they
    stay SUPERSEDED forever, indistinguishable from a manual mark, violating "undo only
    our own marks"."""
    c, parsed = store
    c.execute("DROP TABLE IF EXISTS date_superseded")
    c.execute("CREATE TABLE date_superseded(source TEXT PRIMARY KEY, cutoff TEXT NOT NULL)")
    c.execute("INSERT INTO date_superseded VALUES('old.pdf.md','2026-01-01')")
    # Two chunks of old.pdf.md auto-hidden by an earlier run under the old schema...
    c.execute("UPDATE chunks SET status='SUPERSEDED', valid_to='2026-01-01' WHERE source='old.pdf.md'")
    # ...plus a third chunk of the SAME source, manually superseded at a different date.
    c.execute("INSERT INTO chunks(id,source,ord,title,text,status,valid_from,valid_to) "
              "VALUES(9100,'old.pdf.md',2,'t','body','SUPERSEDED','2026-09-28','2025-06-01')")

    S.apply_dates(c, parsed, None, None)   # no cutoff -> reactivate only the auto marks

    st = chunk_status(c, "old.pdf.md")
    assert st[0] == ("ACTIVE", None)
    assert st[1] == ("ACTIVE", None)
    assert st[9100] == ("SUPERSEDED", "2025-06-01")   # manual mark untouched
    assert c.execute("SELECT count(*) FROM date_superseded").fetchone()[0] == 0


# --- M3: `superseded` counts only ACTIVE -> SUPERSEDED transitions. ---

def test_second_identical_run_reports_zero_newly_superseded(store):
    c, parsed = store
    out1 = S.apply_dates(c, parsed, None, "2026-01-01")
    out2 = S.apply_dates(c, parsed, None, "2026-01-01")
    assert out1["superseded"] == 2
    assert out2["superseded"] == 0
    assert status(c)["old.pdf.md"] == ("SUPERSEDED", "2026-01-01")


# --- I4(b): apply_dates must not clobber an event_date knowledge_index already derived,
# nor push valid_from past an existing non-null valid_to. ---

def test_apply_dates_does_not_overwrite_existing_non_null_event_date(store):
    c, parsed = store
    c.execute("UPDATE chunks SET event_date='2020-05-05' WHERE source='new.pdf.md' AND ord=0")
    S.apply_dates(c, parsed, None, None)
    rows = dict(c.execute("SELECT ord, event_date FROM chunks WHERE source='new.pdf.md'").fetchall())
    assert rows[0] == "2020-05-05"        # untouched: knowledge_index's own derived date wins
    assert rows[1] == "2026-08-18"        # sibling chunk with no prior event_date still gets it


def test_apply_dates_does_not_set_valid_from_after_existing_valid_to(store):
    c, parsed = store
    c.execute("UPDATE chunks SET valid_to='2020-01-01' WHERE source='new.pdf.md' AND ord=0")
    S.apply_dates(c, parsed, None, None)
    row = c.execute("SELECT event_date, valid_from FROM chunks WHERE source='new.pdf.md' AND ord=0").fetchone()
    assert row[0] is None                 # blocked: 2026-08-18 would be later than valid_to
    assert row[1] == "2026-09-28"         # original valid_from left alone


# --- M1: the cutoff is read and validated up front, before any indexing/embedding. ---

def test_bad_cutoff_exits_before_indexing(tmp_path):
    project = tmp_path / "proj"
    (project / "schema").mkdir(parents=True)
    (project / "brain.toml").write_text('[corpus]\nsupersede_before = "01/01/2026"\n')
    db = project / "schema" / "knowledge.sqlite"
    parsed = tmp_path / "parsed"; parsed.mkdir()
    (parsed / "manifest.json").write_text("[]")
    a = types.SimpleNamespace(
        db=str(db), require_goal=False, no_snapshot=True, parsed=str(parsed),
        manifest=None, root_key=None, strict_sources=False, model="x", dim=384,
        max_chars=1200, out=None, no_related=True,
    )
    with pytest.raises(SystemExit):
        S.cmd_apply(a)
    assert not db.exists()   # refused before K.connect()/embedding ever touched the store


def test_bad_cutoff_exits_seed_before_indexing(tmp_path):
    project = tmp_path / "proj"
    (project / "schema").mkdir(parents=True)
    (project / "brain.toml").write_text('[corpus]\nsupersede_before = "not-a-date"\n')
    db = project / "schema" / "knowledge.sqlite"
    parsed = tmp_path / "parsed"; parsed.mkdir()
    (parsed / "manifest.json").write_text("[]")
    a = types.SimpleNamespace(
        db=str(db), require_goal=False, parsed=str(parsed), manifest=None,
        root_key=None, strict_sources=False, model="x", dim=384, max_chars=1200,
    )
    with pytest.raises(SystemExit):
        S.cmd_seed(a)
    assert not db.exists()
