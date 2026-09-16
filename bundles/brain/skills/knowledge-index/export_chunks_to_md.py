#!/usr/bin/env python3
"""Export all VTT/SRT chunks from knowledge.sqlite back to Markdown files.

Usage:
  python export_chunks_to_md.py --db /path/to/knowledge.sqlite --out /tmp/exported_md
  python export_chunks_to_md.py --db /path/to/knowledge.sqlite --out /tmp/exported_md --category ActionItem
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path


def export(db_path, out_dir, category_filter=None, force=False):
    if not Path(db_path).exists():
        sys.exit(f"error: DB not found: {db_path}")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if out_dir.exists() and any(out_dir.glob("*.md")) and not force:
        print(
            f"ERROR: {out_dir} already contains .md files. "
            f"Pass --force to overwrite or choose an empty directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    with sqlite3.connect(db_path) as c:
        if category_filter:
            has_topics = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_topics'"
            ).fetchone()
            if not has_topics:
                sys.exit(
                    "error: chunk_topics table not found — run taxonomy classification first "
                    "(classify_prep → classify agents → classify_write → build_graph)"
                )
            sources = [
                r[0] for r in c.execute(
                    "SELECT DISTINCT c.source FROM chunk_topics ct "
                    "JOIN chunks c ON c.id = ct.chunk_id "
                    "WHERE ct.category_label = ? "
                    "AND COALESCE(c.status, 'ACTIVE') = 'ACTIVE' "
                    "ORDER BY c.source",
                    (category_filter,),
                ).fetchall()
            ]
        else:
            sources = [
                r[0] for r in c.execute(
                    "SELECT DISTINCT source FROM chunks "
                    "WHERE COALESCE(status, 'ACTIVE') = 'ACTIVE' "
                    "ORDER BY source"
                ).fetchall()
            ]

        print(f"Exporting {len(sources)} sources → {out_dir}")

        for source in sources:
            chunks = c.execute(
                "SELECT title, speaker, text, event_date FROM chunks "
                "WHERE source = ? AND COALESCE(status, 'ACTIVE') = 'ACTIVE' "
                "ORDER BY ord",
                (source,),
            ).fetchall()

            lines = [f"# SOURCE: {source}\n"]

            event_date = next((ch[3] for ch in chunks if ch[3]), None)
            if event_date:
                lines.append(f"event_date: {event_date}\n\n")

            for title, speaker, text, _ in chunks:
                lines.append(f"\n## {title or ''}\n\n")
                if speaker:
                    lines.append(f"<!-- speaker: {speaker} -->\n\n")
                lines.append(f"{text}\n")

            safe_name = re.sub(r"[\\/]", "__", source)
            if not safe_name.endswith(".md"):
                safe_name += ".md"
            out_path = out_dir / safe_name
            out_path.write_text("".join(lines), encoding="utf-8")
            print(f"  wrote {len(chunks):3d} chunks → {safe_name}")


def main(args=None):
    if args is None:
        ap = argparse.ArgumentParser()
        ap.add_argument("--db",       required=True, help="Path to knowledge.sqlite")
        ap.add_argument("--out",      required=True, help="Output directory for .md files")
        ap.add_argument("--category", help="Only export sources tagged with this category (optional)")
        ap.add_argument("--force", action="store_true",
                        help="overwrite existing .md files in --out dir")
        args = ap.parse_args()
    export(
        args.db,
        args.out,
        category_filter=getattr(args, "category", None),
        force=getattr(args, "force", False),
    )


if __name__ == "__main__":
    main()
