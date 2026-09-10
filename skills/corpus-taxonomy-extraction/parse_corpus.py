#!/usr/bin/env python3
"""Deterministic corpus parser for corpus-taxonomy-extraction.

Converts a heterogeneous document corpus into uniform Markdown that a
low-tier model can read. No LLM, no torch (docling is retired):
  - .pdf         -> PyMuPDF text layer
  - .pptx/.docx  -> LibreOffice (soffice) -> PDF -> PyMuPDF text layer
  - .xlsx/.xlsm  -> openpyxl read_only structure-dump
This is the TEXT-layer path. Visual/diagram pages (flows, timelines, complex
tables) collapse under any text extractor — those go through the `visual-parse`
skill (render page -> VLM transcription + deterministic table extraction).
Writes one .md per source file plus a manifest.json.

Usage:
  parse_corpus.py --corpus <dir> --out <dir> [--xlsx-max-mb 20] [--sample-rows 8]
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, warnings, traceback
from pathlib import Path
warnings.filterwarnings("ignore")

def _soffice():
    for c in ("soffice", "libreoffice", "/opt/homebrew/bin/soffice",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if shutil.which(c) or os.path.exists(c):
            return c
    return None

def parse_pdf_pymupdf(path):
    """Text layer via PyMuPDF (torch-free). Replaces docling/pypdf. For visual/diagram
    pages this is thin — that's the visual-parse skill's job (render + VLM)."""
    import pymupdf
    doc = pymupdf.open(path)
    parts = []
    for i, pg in enumerate(doc, 1):
        t = (pg.get_text() or "").strip()
        if t:
            parts.append(f"\n\n## [page {i}]\n\n{t}")
    doc.close()
    return "".join(parts)

def parse_office_pymupdf(path):
    """pptx/docx → PDF via LibreOffice, then PyMuPDF text (torch-free, no docling)."""
    so = _soffice()
    if not so:
        raise RuntimeError("need LibreOffice (soffice) for pptx/docx, or pre-convert to PDF")
    tmp = tempfile.mkdtemp(prefix="parse_")
    profile = tempfile.mkdtemp(prefix="parse_soffice_")
    try:
        subprocess.run([so, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--convert-to", "pdf", "--outdir", tmp, path],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pdf = os.path.join(tmp, os.path.splitext(os.path.basename(path))[0] + ".pdf")
        return parse_pdf_pymupdf(pdf)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)

def parse_xlsx_structure(path, sample_rows):
    """Large-workbook structure dump: sheet names, header row, a few sample rows.
    Constant-memory via openpyxl read_only. This is a MAP for 'what is in here',
    not the numeric source of truth (that path is openpyxl->Parquet->SQLite)."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"\n\n## sheet: {ws.title}  (dims={ws.dimensions})")
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            rows.append(row)
            if i >= sample_rows:
                break
        for r in rows:
            cells = [("" if c is None else str(c)) for c in r]
            if any(cells):
                out.append("| " + " | ".join(cells) + " |")
    wb.close()
    return "".join(out)

def parse_one(path, xlsx_max_mb, sample_rows):
    ext = os.path.splitext(path)[1].lower()
    size_mb = os.path.getsize(path) / 1e6
    if ext in (".pptx", ".docx", ".ppt", ".doc"):
        return parse_office_pymupdf(path), "soffice+pymupdf"
    if ext == ".pdf":
        return parse_pdf_pymupdf(path), "pymupdf"
    if ext in (".xlsx", ".xlsm"):
        return parse_xlsx_structure(path, sample_rows), "openpyxl-structure"
    return None, "skipped"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--xlsx-max-mb", type=float, default=20.0)
    ap.add_argument("--sample-rows", type=int, default=8)
    ap.add_argument("--formats", default="pptx,docx,pdf,xlsx,xlsm",
                    help="comma-separated extensions (no dot) to include")
    a = ap.parse_args()
    allow = {"." + e.strip().lower().lstrip(".") for e in a.formats.split(",") if e.strip()}
    os.makedirs(a.out, exist_ok=True)
    manifest = []
    for root, _, files in os.walk(a.corpus):
        for fn in sorted(files):
            if fn.startswith(".") or fn.startswith("~$"):
                continue
            src = os.path.join(root, fn)
            rel = os.path.relpath(src, a.corpus)
            ext = os.path.splitext(fn)[1].lower()
            if ext not in allow:
                continue
            try:
                md, method = parse_one(src, a.xlsx_max_mb, a.sample_rows)
                if md is None:
                    continue
                safe = rel.replace(os.sep, "__") + ".md"
                outp = os.path.join(a.out, safe)
                with open(outp, "w") as f:
                    f.write(f"# SOURCE: {rel}\n# method: {method}\n\n{md}")
                manifest.append({"source": rel, "md": safe, "method": method,
                                 "chars": len(md), "size_mb": round(os.path.getsize(src)/1e6, 2)})
                print(f"[ok] {method:20} {len(md):>8} chars  {rel}", file=sys.stderr)
            except Exception as e:
                print(f"[ERR] {rel}: {e}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                manifest.append({"source": rel, "error": str(e)})
    with open(os.path.join(a.out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    ok = [m for m in manifest if "error" not in m]
    print(f"\nparsed {len(ok)}/{len(manifest)} files -> {a.out}", file=sys.stderr)

if __name__ == "__main__":
    main()
