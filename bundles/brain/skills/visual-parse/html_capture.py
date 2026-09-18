#!/usr/bin/env python3
"""Turn a browser capture of an HTML document into the visual lane's artifact layout.

HTML is a continuous medium: a "page" is an artifact of print CSS, not of the
document, so segmentation comes from the DOM and this script never paginates.
No browser is spawned here — the agent captures through Playwright MCP or Claude
in Chrome and this consumes what it produced.
"""
from __future__ import annotations

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
        bbox = s.get("bbox")
        if not isinstance(bbox, dict) or not all(k in bbox for k in ("x", "y", "width", "height")):
            problems.append(f"{where} missing 'bbox' with x/y/width/height")
        if isinstance(bbox, dict):
            bh = bbox.get("height")
            if not isinstance(bh, (int, float)) or bh <= 0:
                problems.append(f"{where} has a non-positive 'bbox.height'")
        h = s.get("height")
        if not isinstance(h, (int, float)) or h <= 0:
            problems.append(f"{where} has a non-positive 'height'")
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
        of = len(offsets)
        for tile, off in enumerate(offsets, 1):
            page += 1
            plan.append({
                "page": page,
                "segment": int(seg["index"]),
                "tile": tile,
                "of": of,
                "clip": {"x": float(box["x"]), "y": top + off,
                         "width": float(box["width"]),
                         "height": min(float(max_px), height - off)},
            })
    return plan


def _slug(source: str) -> str:
    """Stable slug for a file path or URL — crawling later supplies URLs."""
    s = re.sub(r"^[a-z]+://", "", source or "")
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return (s[-60:].strip("-") or "doc")


def _grid(table) -> str:
    rows = table.get("rows") or []
    if not rows:
        return ""
    head, *body = rows
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join(["---"] * len(head)) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    cap = (table.get("caption") or "").strip()
    return (f"**{cap}**\n\n" if cap else "") + "\n".join(out)


def assemble(obj, plan, outdir: str, dpi: int = 96) -> dict:
    """Write the visual lane's artifact layout for one captured document.

    Emits exactly what render_pages.py emits so vision_prep.py needs no change,
    plus an additive `segment` field grouping the tiles of one logical unit.
    """
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
            "page": n, "image": f"p{n:02d}.png",
            "img_sha": hashlib.sha256(open(png, "rb").read()).hexdigest(),
            "text_len": len(text.strip()), "n_drawings": 0, "n_tables": len(grids),
            "img_cover": 1.0, "flagged": True, "why": "html-segment",
            "segment": entry["segment"], "tile": entry["tile"], "of": entry["of"],
        })
    payload = {"doc": obj.get("title") or obj.get("source", ""), "slug": _slug(obj.get("source", "")),
               "dpi": dpi, "source": obj.get("source", ""), "pages": rows}
    with open(os.path.join(outdir, "pages.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return payload
