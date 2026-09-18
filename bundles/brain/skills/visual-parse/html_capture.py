#!/usr/bin/env python3
"""Turn a browser capture of an HTML document into the visual lane's artifact layout.

HTML is a continuous medium: a "page" is an artifact of print CSS, not of the
document, so segmentation comes from the DOM and this script never paginates.
No browser is spawned here — the agent captures through Playwright MCP or Claude
in Chrome and this consumes what it produced.
"""
from __future__ import annotations

import json


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
        h = s.get("height")
        if not isinstance(h, (int, float)) or h <= 0:
            problems.append(f"{where} has a non-positive 'height'")
        if "text" not in s:
            problems.append(f"{where} missing 'text'")
    return problems
