"""The format-free half of fact-check extraction: risk tags, sentence split, batching and the files
every reader writes (sections.json, stats.json, batch_<k>.json, batches.json).

A reader (sections.py for .docx, pptx_sections.py for .pptx) returns the internal section list:
    {section_id, section, heading_path, level, heading_p_id, words,
     blocks: [(kind, record, words)]}   kind "p" paragraph, "r" table row, "f" figure
and calls write_outputs(). A paragraph or row record may carry `ctx` (the context a claim needs to
stand alone: title, parent, header, row_label, footnote, doc_date) and `pos` (its shape box, deck only);
both pass through unchanged.

This file ships byte-identical in doc-fact-check/ and deck-fact-check/ (a drift test pins the copies):
change both together.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

# Risk tags (spec §13.4). The ONLY definition of these patterns; SKILL.md and the spec refer to them.
# "one" in an idiom counts nothing ("one-off", "one of", "one-to-one", "one-on-one", "one-time", "one another")
_NUM_WORDS = r"(?<!to-)(?<!on-)one(?!-(?:off|to-one|on-one|time)\b|\s+(?:of|another)\b)|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozen|hundred|thousand|million|billion"
RISK_PATTERNS = {
    "num": re.compile(r"(?:[~≈<>]\s*)?\b\d[\d,.]*\s*(?:%|k|m|bn|million|billion|thousand)?(?!\w)|\b(?:" + _NUM_WORDS + r")\b", re.I),
    "date": re.compile(r"\b(?:jan(?:uary)?|feb(?:ruary)?|march|apr(?:il)?|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
                       r"|\bQ[1-4]\b|\bFY\d{2,4}\b|\b(?:19|20)\d{2}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
                       r"|\b(?:today|currently|now|as of)\b", re.I),
    "absolute": re.compile(r"\b(?:never|always|all|every|none|no one|nothing|only|sole|solely|entire|entirely|fully|completely|consistent across)\b", re.I),
    "ownership": re.compile(r"\b(?:owns?|owned|owner|operates?|operated by|responsible|accountable|attend(?:s|ed)?|decided|approved|leads?|maintains?|managed by)\b", re.I),
    "topo": re.compile(r"→|->|↔|<->|\b(?:push(?:es|ed)?|pull(?:s|ed)?|calls?|sends?|sent|feeds?|syncs?|"
                       r"synchroni[sz](?:es|ed)?|integrat\w*|via|through|into|hosts?|hosted|run(?:s|ning)? on|"
                       r"connects?|routes?|relay(?:s|ed)?|reads?|writes?)\b", re.I),
    "gap": re.compile(r"\b(?:undecided|unclear|unconfirmed|not (?:yet )?(?:established|decided|confirmed|clear|known|"
                      r"covered|isolated|defined)|did not|does not|no (?:decision|owner)|open question|tbd|tbc|"
                      r"remains? (?:open|undecided))\b", re.I),
    "quote": re.compile(r"[\"“][^\"“”]{8,}[\"”]"),
    "numword": re.compile(r"\b(?:half|quarter|third|majority|most of|roughly|approximately)\b", re.I),
    "scope": re.compile(r"\b(?:end state|decommission\w*|retire[sd]?|retirement|replac(?:e|es|ed|ement)|"
                        r"exclud(?:e|es|ed|ing)|out of scope|in scope|committed|funded|system of record|"
                        r"master(?:s|ed)?)\b", re.I),
}
_NUMBERING = re.compile(r"§\s*\d[\d.]*|\b(?:section|figure|fig\.|table|phase|step|version|v)\s*\d[\d.]*", re.I)
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9\"“(])|\n+")


def tag_risk(text: str) -> list[str]:
    t = _NUMBERING.sub(" ", text)
    return [tag for tag, rx in RISK_PATTERNS.items() if rx.search(t)]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s and s.strip()]


def estimate_minutes(statements_total: int, statements_risk: int, chunk: int = 15, wave: int = 10) -> dict:
    """Minutes for the v2 pipeline: 5 fixed (extract, figures, merge, annotate) + 6 per verify wave of `wave`
    chunks of `chunk` claims. Provisional constants; recalibrated from run.json timings (plan task E5)."""
    def run(claims: int) -> int:
        size = chunk(claims) if callable(chunk) else chunk     # a reader may pass its automatic chunk size
        return 5 + 6 * math.ceil(math.ceil(claims / size) / wave) if claims else 5
    return {"fast": run(statements_risk), "deep": run(statements_total)}


def _words(text: str) -> int:
    return len(text.split())


def _render(sec: dict, blocks, words: int, part: tuple[int, int] | None = None) -> dict:
    paragraphs, tables, figures = [], [], []
    for kind, rec, _ in blocks:
        if kind == "p":
            paragraphs.append(rec)
        elif kind == "f":
            figures.append(rec)
        else:
            if not tables or tables[-1]["t_id"] != rec["t_id"]:
                tables.append({"t_id": rec["t_id"], "rows": []})
            tables[-1]["rows"].append({k: rec[k] for k in ("r_id", "cells", "s_id", "risk", "ctx", "pos", "at") if k in rec})
    out = {k: sec[k] for k in ("section_id", "section", "heading_path", "level", "heading_p_id")}
    if part:
        out["part"], out["parts"] = part
    out.update(paragraphs=paragraphs, tables=tables, figures=figures, words=words)
    if sec.get("slide_context"):                  # a deck slide's title, lede and key (spec §11 Y1)
        out["slide_context"] = sec["slide_context"]
    return out


def _units(sec: dict, max_words: int) -> list[dict]:
    if sec["words"] <= max_words:
        return [_render(sec, sec["blocks"], sec["words"])]
    heading = sec["words"] - sum(w for _, _, w in sec["blocks"])
    chunks: list[tuple[list, int]] = []
    cur, cur_w, has_content = [], heading, False
    for b in sec["blocks"]:
        if has_content and b[2] and cur_w + b[2] > max_words:
            chunks.append((cur, cur_w))
            cur, cur_w, has_content = [], 0, False
        cur.append(b)
        cur_w += b[2]
        has_content = has_content or bool(b[2])
    chunks.append((cur, cur_w))
    n = len(chunks)
    return [_render(sec, blocks, w, (i, n)) for i, (blocks, w) in enumerate(chunks, 1)]


def build_batches(sections: list[dict], max_words: int) -> list[list[dict]]:
    batches: list[list[dict]] = []
    cur_w = 0
    for sec in sections:
        for u in _units(sec, max_words):
            if not batches or (batches[-1] and cur_w + u["words"] > max_words):
                batches.append([])
                cur_w = 0
            batches[-1].append(u)
            cur_w += u["words"]
    return batches


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")



def core_date(core_properties) -> dict | None:
    """The document date from package core properties (python-docx and python-pptx share the shape): modified,
    else created; always `inferred`, since file metadata is not a date the author wrote."""
    for d in (getattr(core_properties, "modified", None), getattr(core_properties, "created", None)):
        if d is not None:
            return {"value": d.date().isoformat(), "inferred": True}
    return None


def write_outputs(source_path, out_dir, sections: list[dict], max_words: int = 1500, *,
                  extra_stats: dict | None = None, doc_date: dict | None = None, chunk: int = 15) -> list[dict]:
    """Write sections.json, stats.json, batch_<k>.json and batches.json for a reader's sections; return the
    batch index. The caller has cleared stale outputs; stale batch files are removed here. `doc_date`
    ({value: "YYYY-MM-DD", inferred: bool}) is the document's own date, written into stats and every batch so a
    claim's period ("current", "this year") can be read against it."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    batches = build_batches(sections, max_words)
    for stale in out_dir.glob("batch_*.json"):
        stale.unlink()
    _dump(out_dir / "sections.json", [_render(s, s["blocks"], s["words"]) for s in sections])
    statements = [s for sec in sections for kind, rec, _ in sec["blocks"]
                  for s in (rec["sentences"] if kind == "p" else [rec] if kind == "r" else [])]
    total, risky = len(statements), sum(1 for s in statements if s["risk"])
    tag_counts = {t: sum(1 for s in statements if t in s["risk"]) for t in [*RISK_PATTERNS, "label"]}
    _dump(out_dir / "stats.json", {"sections_total": len(sections), "statements_total": total,
                                   "statements_risk": risky, "tag_counts": tag_counts,
                                   "estimate_minutes": estimate_minutes(total, risky, chunk=chunk),
                                   "source_sha256": hashlib.sha256(Path(source_path).read_bytes()).hexdigest(),
                                   **({"doc_date": doc_date} if doc_date else {}), **(extra_stats or {})})
    index = []
    for k, units in enumerate(batches, 1):
        name = f"batch_{k}.json"
        _dump(out_dir / name, {"batch": k, "document": Path(source_path).name,
                               **({"doc_date": doc_date} if doc_date else {}),
                               "max_words": max_words, "sections": units})
        index.append({"batch": k, "file": name, "words": sum(u["words"] for u in units),
                      "sections": [{"section_id": u["section_id"], "section": u["section"],
                                    **({"part": u["part"]} if "part" in u else {})} for u in units]})
    _dump(out_dir / "batches.json", index)
    return index
