#!/usr/bin/env python3
"""Render a document's pages to images and FLAG the ones whose meaning is visual.

The scary truth: slide/diagram pages (flows, timelines, circle process charts)
carry their meaning in the LAYOUT, so a text extractor returns disconnected
fragments. This step renders each page to a PNG and decides which pages a
vision model should transcribe (the rest are fine via plain text extraction).

Deterministic, no LLM. PDF via PyMuPDF; PPTX/PPT/DOCX first converted to PDF via
LibreOffice `soffice` (must be on PATH or at /opt/homebrew/bin/soffice).

Writes <out>/<doc>/p<NN>.png for every page and <out>/<doc>/pages.json:
  [{page, image, img_sha, text_len, n_drawings, img_cover, flagged}]

A page is flagged VISUAL (→ vision model) when the text layer likely misses the
meaning: thin text with no extracted table, OR many vector drawings (a flow /
timeline / diagram). Image-area `cover` is deliberately NOT used — full-bleed
backgrounds make it meaningless. All thresholds are tunable per corpus.

Usage:
  render_pages.py --doc <file.pdf|pptx> --out <assets_dir> [--dpi 150]
                  [--min-text 220] [--hi-draw 60] [--mid-draw 28] [--mid-text 1000] [--all]
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

def kebab(s): return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "doc"

def doc_slug(doc_path):
    """Asset slug for --doc, derived from its FULL given path (dir + stem), not just the
    basename — so `a/report.pdf` and `b/report.pdf` land in distinct asset dirs instead of
    overwriting each other. Each path component is kebabed individually and joined with
    `__` (a sequence kebab() never itself produces, since it strips underscores) so a `-`
    inside one component can't be confused with a directory boundary. A bare filename with
    no directory component (or one passed as a plain basename) yields the same slug as
    before, so single-flat-directory setups are unaffected."""
    norm = os.path.normpath(doc_path).replace("\\", "/")
    d, base = os.path.split(norm)
    stem = os.path.splitext(base)[0]
    parts = [p for p in d.split("/") if p and p != "."]
    return "__".join(kebab(p) for p in parts + [stem]) or "doc"

def soffice_bin():
    for c in ("soffice", "libreoffice", "/opt/homebrew/bin/soffice",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if shutil.which(c) or os.path.exists(c):
            return c
    return None

def to_pdf(path):
    """Convert non-PDF (pptx/ppt/docx) to a temp PDF via LibreOffice; returns its path."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return path, None
    so = soffice_bin()
    if not so:
        sys.exit("error: need LibreOffice (soffice) to render non-PDF; install it or pre-convert to PDF")
    tmp = tempfile.mkdtemp(prefix="vparse_")
    profile = tempfile.mkdtemp(prefix="vparse_soffice_")
    try:
        try:
            subprocess.run([so, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--convert-to", "pdf", "--outdir", tmp, path],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        out = os.path.join(tmp, os.path.splitext(os.path.basename(path))[0] + ".pdf")
        if not os.path.exists(out):
            sys.exit(f"error: soffice did not produce {out}")
        return out, tmp
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", required=True)
    ap.add_argument("--out", required=True, help="assets root; images go to <out>/<doc-slug>/")
    ap.add_argument("--dpi", type=int, default=150)
    # Flag a page as VISUAL (needs a vision model) when the text layer likely misses the
    # meaning. `cover` (image area) is NOT used — full-bleed background images make it
    # meaningless. The signal is thin text OR many vector drawings (flows/timelines/diagrams).
    ap.add_argument("--min-text", type=int, default=220, help="flag if fewer extracted chars (title/pure-visual)")
    ap.add_argument("--hi-draw", type=int, default=60, help="flag if at least this many vector drawings (dense diagram/timeline), any text")
    ap.add_argument("--mid-draw", type=int, default=28, help="with --mid-text: flag a lighter diagram")
    ap.add_argument("--mid-text", type=int, default=1000, help="drawings>=mid-draw AND text<mid-text -> flag")
    ap.add_argument("--all", action="store_true", help="flag EVERY page (treat as a slide deck)")
    a = ap.parse_args()
    import pymupdf

    pdf, tmp = to_pdf(a.doc)
    slug = doc_slug(a.doc)
    outdir = os.path.join(a.out, slug)
    os.makedirs(outdir, exist_ok=True)
    doc = pymupdf.open(pdf)
    zoom = a.dpi / 72.0
    mat = pymupdf.Matrix(zoom, zoom)
    pages = []
    for i, page in enumerate(doc):
        png = os.path.join(outdir, f"p{i+1:02d}.png")
        page.get_pixmap(matrix=mat).save(png)
        img_sha = hashlib.sha256(open(png, "rb").read()).hexdigest()
        text = page.get_text() or ""
        # per-page text sidecar (PyMuPDF text layer replaces docling/pypdf for text pages)
        open(os.path.join(outdir, f"p{i+1:02d}.txt"), "w").write(text)
        # DETERMINISTIC table extraction (the factual layer — a VLM misreads dense
        # tables). Real cell grids as Markdown; the answer path cites THESE, not prose.
        n_tables = 0
        try:
            tabs = page.find_tables()
            grids = []
            for ti, tb in enumerate(getattr(tabs, "tables", []) or [], 1):
                try:
                    md = tb.to_markdown()
                except Exception:
                    md = ""
                if md and md.strip():
                    grids.append(f"### Table {ti} (extracted, verbatim cells)\n{md}")
            if grids:
                open(os.path.join(outdir, f"p{i+1:02d}.tables.md"), "w").write("\n\n".join(grids))
                n_tables = len(grids)
        except Exception:
            pass
        try:
            n_draw = len(page.get_drawings())
        except Exception:
            n_draw = 0
        parea = abs(page.rect.width * page.rect.height) or 1.0
        cover = 0.0
        try:
            for im in page.get_image_info():
                x0, y0, x1, y1 = im["bbox"]
                cover += abs((x1 - x0) * (y1 - y0)) / parea
        except Exception:
            pass
        tl = len(text.strip())
        # thin-text flags a page UNLESS it's a pure data table we already extracted (grid + cells captured)
        thin = tl < a.min_text and n_tables == 0
        flagged = (a.all
                   or thin                                         # thin text: title / pure diagram (no extracted table)
                   or n_draw >= a.hi_draw                          # dense diagram/timeline (even with fragmented labels)
                   or (n_draw >= a.mid_draw and tl < a.mid_text))  # lighter diagram with modest text
        why = ("all" if a.all else "thin-text" if thin
               else "dense-draw" if n_draw >= a.hi_draw
               else "diagram" if (n_draw >= a.mid_draw and tl < a.mid_text) else "")
        pages.append({"page": i + 1, "image": os.path.relpath(png, a.out), "img_sha": img_sha,
                      "text_len": tl, "n_drawings": n_draw, "n_tables": n_tables,
                      "img_cover": round(cover, 3), "flagged": bool(flagged), "why": why})
    json.dump({"doc": os.path.basename(a.doc), "slug": slug, "dpi": a.dpi, "pages": pages},
              open(os.path.join(outdir, "pages.json"), "w"), indent=2)
    doc.close()
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    nflag = sum(p["flagged"] for p in pages)
    print(f"{a.doc}: {len(pages)} pages -> {outdir}  ({nflag} flagged visual for VLM transcription)")

if __name__ == "__main__":
    main()
