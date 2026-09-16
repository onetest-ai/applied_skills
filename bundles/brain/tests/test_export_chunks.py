"""Tests for export_chunks_to_md.py overwrite guard."""
import sqlite3, sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import export_chunks_to_md as exp


def _make_db(tmp_path):
    """Create a minimal knowledge.sqlite with 2 chunks."""
    db = tmp_path / "knowledge.sqlite"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE chunks(
            id INT PRIMARY KEY, source TEXT, ord INT,
            title TEXT, text TEXT, sha TEXT, image TEXT,
            embedding_content_hash TEXT, created_at TEXT,
            event_date TEXT, valid_from TEXT, valid_to TEXT,
            status TEXT DEFAULT 'ACTIVE',
            parent_heading TEXT, breadcrumb_path TEXT, speaker TEXT
        );
        INSERT INTO chunks(id,source,ord,title,text,status)
        VALUES(1,'doc_a.md',0,'Title A','Body A','ACTIVE');
        INSERT INTO chunks(id,source,ord,title,text,status)
        VALUES(2,'doc_b.md',0,'Title B','Body B','ACTIVE');
    """)
    con.commit()
    con.close()
    return str(db)


def test_export_refuses_nonempty_dir_without_force(tmp_path):
    """export_chunks_to_md must exit 1 if --out already contains .md files."""
    db = _make_db(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "existing.md").write_text("old content")

    import argparse, io, contextlib
    args = argparse.Namespace(db=db, out=str(out), force=False,
                              category=None, limit=None)
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        try:
            exp.main(args)
            assert False, "Should have called sys.exit(1)"
        except SystemExit as e:
            assert e.code == 1
    assert "ERROR" in buf.getvalue()


def test_export_force_overwrites_existing_files(tmp_path):
    """--force must allow writing into a non-empty --out dir."""
    db = _make_db(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "existing.md").write_text("old content")

    import argparse
    args = argparse.Namespace(db=db, out=str(out), force=True,
                              category=None, limit=None)
    exp.main(args)  # must not raise
    md_files = list(out.glob("*.md"))
    assert len(md_files) >= 2  # at least doc_a and doc_b


def test_export_empty_out_dir_proceeds_without_force(tmp_path):
    """Empty --out dir must proceed normally even without --force."""
    db = _make_db(tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    import argparse
    args = argparse.Namespace(db=db, out=str(out), force=False,
                              category=None, limit=None)
    exp.main(args)  # must not raise
    md_files = list(out.glob("*.md"))
    assert len(md_files) >= 2
