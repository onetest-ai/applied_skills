#!/usr/bin/env python3
"""Advisory match of the induced metric inventory against the governed metric layer.

A `computable` metric with no governed definition is exactly what kb answers "not modeled".
This never writes schema/metrics.<corpus>.json — that stays hand-authored.
"""
import difflib
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from taxo_io import norm  # noqa: E402

YES, CANDIDATE = 0.92, 0.80


def find_governed(tax_dir):
    hits = [h for h in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(tax_dir)), "schema",
                                                      "metrics.*.json")))
            if not h.endswith("metrics.example.json")]
    return hits[0] if len(hits) == 1 else None


def load_governed(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    ms = data.get("metrics", data) if isinstance(data, dict) else data
    pairs = ms.items() if isinstance(ms, dict) else ((m.get("metric"), m) for m in ms)
    return [{"key": k, "desc": (v or {}).get("desc", ""), "family": (v or {}).get("family")}
            for k, v in pairs if k and not str(k).startswith("_")]


def match(entry, governed):
    names = [entry.get("metric") or ""] + list(entry.get("variants") or [])
    best_score, best_key = 0.0, None
    for g in governed:
        for cand in (g["key"].replace("_", " "), g["desc"]):
            b = norm(cand)
            for n in names:
                a = norm(n)
                if not a or not b:
                    continue
                s = 1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio()
                if s > best_score:
                    best_score, best_key = s, g["key"]
    status = "yes" if best_score >= YES else "candidate" if best_score >= CANDIDATE else "no"
    return {"status": status, "key": best_key if status != "no" else None, "score": round(best_score, 2)}


def annotate(metrics, governed):
    return [{**m, "governed": match(m, governed)} for m in metrics]


def gap_markdown(metrics, governed):
    rows = [m for m in annotate(metrics, governed)
            if m.get("source_type") in ("computable", "both") and m["governed"]["status"] != "yes"]
    lines = ["# Ungoverned computable metrics", "",
             "Induced from the corpus with no matching entry in the governed metric layer, so kb answers them "
             "as \"not modeled\". Matching is a heuristic — check each before adding it to "
             "`schema/metrics.<corpus>.json`.", "",
             "| metric | source_type | grain | definition | nearest governed | sources |",
             "|---|---|---|---|---|---|"]
    for m in rows:
        g = m["governed"]
        nearest = f"{g['key']} ({g['score']})" if g["key"] else "—"
        definition = (m.get("definition") or "").replace("|", "/")
        lines.append(f"| {m['metric']} | {m['source_type']} | {m.get('grain') or ''} | {definition} | {nearest} | "
                     f"{len(m.get('sources') or [])} |")
    return "\n".join(lines) + "\n"
