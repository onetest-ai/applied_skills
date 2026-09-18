#!/usr/bin/env python3
"""Turn a browser capture of an HTML document into the visual lane's artifact layout.

HTML is a continuous medium: a "page" is an artifact of print CSS, not of the
document, so segmentation comes from the DOM and this script never paginates.
No browser is spawned here — the agent captures through Playwright MCP or Claude
in Chrome and this consumes what it produced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re


def validate_segments(obj) -> list[str]:
    """Problems with a segments.json payload; empty list means valid.

    Consumers act on this instead of trusting a payload that came from a browser
    via an agent, where a silent shape change would otherwise surface much later
    as a confusing artifact error.
    """
    problems: list[str] = []
    if not isinstance(obj, dict):
        return ["segments.json must be an object"]
    if not (obj.get("source") or "").strip():
        problems.append("missing 'source' (the file:// or https:// URL captured)")
    segments = obj.get("segments")
    if not isinstance(segments, list) or not segments:
        problems.append("'segments' must be a non-empty list")
        return problems
    for i, s in enumerate(segments):
        where = f"segment[{i}]"
        if not isinstance(s, dict):
            problems.append(f"{where} must be an object")
            continue
        if "index" not in s or not isinstance(s.get("index"), (int, float)):
            problems.append(f"{where} missing an integer 'index'")
        bbox = s.get("bbox")
        if not isinstance(bbox, dict) or not all(k in bbox for k in ("x", "y", "width", "height")):
            problems.append(f"{where} missing 'bbox' with x/y/width/height")
        if isinstance(bbox, dict):
            bh = bbox.get("height")
            # `bh != bh` is the stdlib-free NaN check: a fully hidden segment's
            # bboxUnion() starts from Infinity seeds and can yield NaN, which
            # `bh <= 0` alone does not catch (NaN <= 0 is False).
            if not isinstance(bh, (int, float)) or bh <= 0 or bh != bh:
                problems.append(f"{where} has a non-positive or NaN 'bbox.height'")
        h = s.get("height")
        if not isinstance(h, (int, float)) or h <= 0 or h != h:
            problems.append(f"{where} has a non-positive or NaN 'height'")
        if "text" not in s:
            problems.append(f"{where} missing 'text'")
    return problems


def plan_captures(obj, max_px: int = 1600, overlap: float = 0.1) -> list[dict]:
    """Capture instructions for one document: one entry per image the provider takes.

    A segment shorter than max_px is one capture. A taller one is tiled with
    `overlap` of the tile height shared between neighbours, so a line of text
    straddling a seam appears whole in at least one tile. Tiles of one segment
    share a `segment` value; vision_assemble merges them back into one chunk, so
    a citation resolves to the segment, never to a tile.
    """
    problems = validate_segments(obj)
    if problems:
        raise ValueError("invalid segments.json: " + "; ".join(problems))
    if not (0.0 <= overlap < 0.95):
        raise ValueError(f"overlap must be in [0, 0.95), got {overlap!r}")
    step = max(1, int(max_px * (1.0 - overlap)))
    plan: list[dict] = []
    page = 0
    for seg in obj["segments"]:
        box = seg["bbox"]
        top, height = float(box["y"]), float(box["height"])
        offsets = [0.0] if height <= max_px else [float(o) for o in range(0, int(height), step)]
        # A final offset whose remaining height is a sliver (< 15% of max_px) is a
        # degenerate trailing tile — e.g. a 1px-tall capture. Drop it and let the
        # previous tile's height extend to the true end instead.
        if len(offsets) > 1 and (height - offsets[-1]) < 0.15 * max_px:
            offsets.pop()
        of = len(offsets)
        for tile, off in enumerate(offsets, 1):
            page += 1
            # The last tile always extends to the segment's true end (this is a
            # no-op except right after the drop above, where it absorbs the sliver).
            tile_height = (height - off) if tile == of else min(float(max_px), height - off)
            plan.append({
                "page": page,
                "segment": int(seg["index"]),
                "tile": tile,
                "of": of,
                "clip": {"x": float(box["x"]), "y": top + off,
                         "width": float(box["width"]),
                         "height": tile_height},
            })
    return plan


def _slug(source: str) -> str:
    """Stable, collision-free slug for a file path or URL.

    The readable tail is for humans reading an assets directory; the digest is
    what makes two different sources impossible to confuse, since the slug names
    the directory their captures land in. Truncating alone is not enough: two
    URLs differing only in host share a tail.
    """
    s = re.sub(r"^[a-zA-Z]+://", "", source or "")
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    digest = hashlib.sha256((source or "").encode("utf-8")).hexdigest()[:8]
    tail = s[-48:].strip("-")
    return f"{tail}-{digest}" if tail else digest


def _cell(v) -> str:
    """Escape pipes and normalize whitespace in a cell value."""
    return str(v).replace("|", "\\|").replace("\n", " ").strip()


def _grid(table) -> str:
    """Render a DOM table into Markdown, handling ragged rows and pipes in cells.

    The DOM knows whether a table has a header; Python cannot guess. Tables
    without an explicit header get an empty-cell row, preserving column count.
    """
    rows = [r for r in (table.get("rows") or []) if r]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    has_header = bool(table.get("hasHeader", True))
    head = [_cell(c) for c in rows[0]] if has_header else [""] * width
    body = rows[1:] if has_header else rows
    head += [""] * (width - len(head))
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * width) + "|"]
    for r in body:
        cells = [_cell(c) for c in r]
        cells += [""] * (width - len(cells))  # pad ragged rows
        out.append("| " + " | ".join(cells[:width]) + " |")
    cap = _cell(table.get("caption") or "")
    return (f"**{cap}**\n\n" if cap else "") + "\n".join(out)


def assemble(obj, plan, outdir: str, dpi: int = 96) -> dict:
    """Write the visual lane's artifact layout for one captured document.

    Emits exactly what render_pages.py emits so vision_prep.py needs no change,
    plus an additive `segment` field grouping the tiles of one logical unit.

    `outdir`'s basename must equal the slug `plan` computed from the same
    segments.json — `plan` is the one place that decides the directory name
    (see its `--assets-root`/`--source` flags), so a provider that wrote its
    screenshots somewhere else fails loudly here instead of producing a
    `pages.json` whose image markers point at a directory that doesn't exist.
    """
    slug = _slug(obj.get("source", ""))
    actual = os.path.basename(str(outdir).rstrip("/"))
    if actual != slug:
        raise ValueError(
            f"outdir basename {actual!r} does not match the slug {slug!r} that "
            f"'plan' computed from segments.json's source {obj.get('source', '')!r} "
            f"— the provider must write PNGs into the directory 'plan' printed, "
            f"not a different one"
        )
    os.makedirs(outdir, exist_ok=True)
    by_index = {int(s["index"]): s for s in obj["segments"]}
    rows = []
    for entry in plan:
        n = entry["page"]
        png = os.path.join(outdir, f"p{n:02d}.png")
        if not os.path.exists(png):
            raise FileNotFoundError(f"capture missing: {png}")
        seg = by_index[entry["segment"]]
        text = seg.get("text") or ""
        # Every tile of a segment carries the SAME full segment text on purpose,
        # because a later task merges a segment's tiles into one chunk. Without it
        # a reader cannot tell intent from bug.
        with open(os.path.join(outdir, f"p{n:02d}.txt"), "w", encoding="utf-8") as fh:
            fh.write(text)
        # A grid belongs to the segment, not to a tile: write it once, on tile 1.
        grids = [_grid(t) for t in (seg.get("tables") or [])] if entry["tile"] == 1 else []
        grids = [g for g in grids if g]
        if grids:
            with open(os.path.join(outdir, f"p{n:02d}.tables.md"), "w", encoding="utf-8") as fh:
                fh.write("\n\n".join(f"### Table {i} (extracted, verbatim cells)\n{g}"
                                     for i, g in enumerate(grids, 1)))
        rows.append({
            "page": n,
            # slug-relative, matching render_pages.py's os.path.relpath(png, assets_root):
            # vision_prep.py resolves image via os.path.join(dirname(render_dir), image).
            "image": os.path.join(os.path.basename(str(outdir).rstrip("/")), f"p{n:02d}.png"),
            "img_sha": hashlib.sha256(open(png, "rb").read()).hexdigest(),
            "text_len": len(text.strip()), "n_drawings": 0, "n_tables": len(grids),
            "img_cover": 1.0,
            # HTML segments are deck slides, the equivalent of render_pages.py --all,
            # so every segment goes to the vision model for transcription.
            "flagged": True, "why": "html-segment",
            "segment": entry["segment"], "tile": entry["tile"], "of": entry["of"],
        })
    payload = {"doc": obj.get("title") or obj.get("source", ""), "slug": slug,
               "dpi": dpi, "source": obj.get("source", ""), "pages": rows}
    with open(os.path.join(outdir, "pages.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(description="Plan and assemble HTML captures for the visual lane.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="segments.json -> capture instructions the provider executes")
    p.add_argument("--segments", required=True, help="segments.json from html_segments.js")
    p.add_argument("--out", required=True, help="where to write the capture plan JSON")
    p.add_argument("--source", help="source URL/path to derive the slug from; "
                                     "defaults to segments.json's 'source' field")
    p.add_argument("--assets-root", required=True,
                   help="parent dir; plan creates <assets-root>/<slug>/ and prints it — "
                        "the provider must write its screenshots there, and 'assemble' "
                        "must be pointed at that same directory")
    p.add_argument("--max-px", type=int, default=1600,
                   help="tallest capture before a segment is tiled (default: 1600)")
    p.add_argument("--overlap", type=float, default=0.1,
                   help="fraction of a tile shared with its neighbour (default: 0.1)")

    a = sub.add_parser("assemble", help="plan + captured PNGs -> the visual lane's artifact layout")
    a.add_argument("--segments", required=True)
    a.add_argument("--plan", required=True)
    a.add_argument("--outdir", required=True, help="<assets>/<slug>; the PNGs are already here — "
                                                     "must be the directory 'plan' printed")
    a.add_argument("--dpi", type=int, default=96)

    args = ap.parse_args(argv)
    obj = json.load(open(args.segments, encoding="utf-8"))
    if args.cmd == "plan":
        plan = plan_captures(obj, max_px=args.max_px, overlap=args.overlap)
        source = args.source or obj.get("source", "")
        slug = _slug(source)
        outdir = os.path.join(args.assets_root, slug)
        os.makedirs(outdir, exist_ok=True)
        payload = {"slug": slug, "outdir": outdir, "plan": plan}
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        segs = len({e["segment"] for e in plan})
        print(f"{args.segments}: {segs} segment(s) -> {len(plan)} capture(s) -> {args.out}")
        print(outdir)
        return
    plan_obj = json.load(open(args.plan, encoding="utf-8"))
    plan = plan_obj["plan"] if isinstance(plan_obj, dict) and "plan" in plan_obj else plan_obj
    pages = assemble(obj, plan, args.outdir, dpi=args.dpi)
    print(f"{args.outdir}: {len(pages['pages'])} page(s) written (slug {pages['slug']})")


if __name__ == "__main__":
    main()
