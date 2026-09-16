#!/usr/bin/env python3
"""Convert extraction JSON files to heading-structured Markdown.

Takes a directory of *.json extraction files (--input) and writes one .md file
per JSON into the output directory (--out).  Optionally backs up originals to
<out>/orig/ before writing (default); pass --no-backup to skip.

knowledge_index.py's chunker splits on ## headings.  Raw extraction JSON has
no headings — each file becomes one giant chunk (~20K chars) that overflows the
MCP response cap.  This script rewrites each .json as structured Markdown so
the chunker produces ~1 chunk per extraction record (~200-400 chars each).

Usage:
    python3 extraction_to_md.py --input ~/projects/my-brain/extractions \
                                 --out   ~/projects/my-brain/parsed
    # Backs up originals to <out>/orig/ before writing.
"""
import argparse
import json
import os
import shutil
from pathlib import Path


def serialize_one(data: dict) -> str:
    session_date = data.get("session_date", "unknown")
    title = data.get("title", "").replace("_", " ")
    slug = data.get("file_slug", "")
    axes = data.get("session_axes", {})
    products = ", ".join(axes.get("products", [])) or "UNRESOLVED"
    phases = ", ".join(axes.get("sdlc_phases", [])) or "UNRESOLVED"
    extractions = data.get("extractions", [])

    lines = [
        f"# {title}",
        f"",
        f"**Session:** {session_date}  **Slug:** {slug}",
        f"**Products:** {products}  **Phases:** {phases}",
        f"",
    ]

    for i, rec in enumerate(extractions, 1):
        category = rec.get("category", "Unknown")
        product = rec.get("product", "UNRESOLVED")
        sdlc = rec.get("sdlc_phase", "")
        verbatim = rec.get("verbatim_quote", "").strip()
        context = rec.get("context", "").strip()
        priority = rec.get("priority", "")
        owner = rec.get("owner", "")

        lines.append(f"## {category} — {product} (record {i})")
        lines.append(f"")
        if verbatim:
            lines.append(f"> {verbatim}")
            lines.append(f"")
        if context:
            lines.append(f"{context}")
            lines.append(f"")
        meta_parts = []
        if sdlc:
            meta_parts.append(f"sdlc_phase: {sdlc}")
        if priority:
            meta_parts.append(f"priority: {priority}")
        if owner:
            meta_parts.append(f"owner: {owner}")
        if meta_parts:
            lines.append("  ".join(meta_parts))
            lines.append(f"")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to directory of *.json extraction files")
    ap.add_argument("--out", required=True, help="Output directory for .md files")
    ap.add_argument("--no-backup", action="store_true", help="Skip backup (do not copy originals to <out>/orig/)")
    args = ap.parse_args()

    input_dir = Path(args.input).expanduser().resolve()
    if not input_dir.is_dir():
        print(f"ERROR: {input_dir} is not a directory")
        return 1

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    orig = out_dir / "orig"
    if not args.no_backup:
        orig.mkdir(exist_ok=True)

    files = sorted(Path(args.input).expanduser().resolve().glob("*.json"))
    print(f"Found {len(files)} .json files in {input_dir}")

    converted = 0
    skipped = 0
    for f in files:
        raw = f.read_text(encoding="utf-8")
        # Skip files that are already structured Markdown (have ## headings)
        if "## " in raw and not raw.strip().startswith("{"):
            print(f"  [skip] {f.name} — already Markdown")
            skipped += 1
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f"  [skip] {f.name} — not valid JSON, leaving untouched")
            skipped += 1
            continue

        md = serialize_one(data)
        out_file = out_dir / (f.stem + ".md")
        if not args.no_backup:
            shutil.copy2(f, orig / f.name)
        out_file.write_text(md, encoding="utf-8")
        n_records = len(data.get("extractions", []))
        print(f"  [ok]   {f.name} — {n_records} records → {len(md):,} chars")
        converted += 1

    print(f"\nDone: {converted} converted, {skipped} skipped.")
    if not args.no_backup:
        print(f"Originals backed up to {orig}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
