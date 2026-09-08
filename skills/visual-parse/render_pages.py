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

Usage:
  render_pages.py --doc <file.pdf|pptx> --out <assets_dir> [--dpi 150]
                  [--min-text 400] [--draw-thresh 40] [--cover 0.45] [--all]
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile

def kebab(s): return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "doc"

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
    subprocess.run([so, "--headless", "--convert-to", "pdf", "--outdir", tmp, path],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out = os.path.join(tmp, os.path.splitext(os.path.basename(path))[0] + ".pdf")
    if not os.path.exists(out):
        sys.exit(f"error: soffice did not produce {out}")
    return out, tmp

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", required=True)
    ap.add_argument("--out", required=True, help="assets root; images go to <out>/<doc-slug>/")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--min-text", type=int, default=400, help="flag a page with fewer extracted chars")
    ap.add_argument("--draw-thresh", type=int, default=40, help="flag a page with more vector drawings")
    ap.add_argument("--cover", type=float, default=0.45, help="flag a page whose images cover more area")
    ap.add_argument("--all", action="store_true", help="flag EVERY page (treat as a slide deck)")
    a = ap.parse_args()
    import pymupdf

    pdf, tmp = to_pdf(a.doc)
    slug = kebab(os.path.splitext(os.path.basename(a.doc))[0])
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
        flagged = a.all or (len(text.strip()) < a.min_text) or (n_draw > a.draw_thresh) or (cover > a.cover)
        pages.append({"page": i + 1, "image": os.path.relpath(png, a.out), "img_sha": img_sha,
                      "text_len": len(text.strip()), "n_drawings": n_draw, "n_tables": n_tables,
                      "img_cover": round(cover, 3), "flagged": bool(flagged)})
    json.dump({"doc": os.path.basename(a.doc), "slug": slug, "dpi": a.dpi, "pages": pages},
              open(os.path.join(outdir, "pages.json"), "w"), indent=2)
    doc.close()
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    nflag = sum(p["flagged"] for p in pages)
    print(f"{a.doc}: {len(pages)} pages -> {outdir}  ({nflag} flagged visual for VLM transcription)")

if __name__ == "__main__":
    main()
