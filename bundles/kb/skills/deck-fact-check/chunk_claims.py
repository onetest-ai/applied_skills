"""Turn stage-E claim files into stage-V verify chunks for /kb:doc-fact-check and /kb:deck-fact-check (spec §15).

    python chunk_claims.py <dir> [--scope all|risk] [--chunk-size 15|auto] [--stride]

<dir> is the directory the reader (sections.py or pptx_sections.py) wrote. For every batch k in batches.json it reads
claims_batch_<k>.json (claims of that batch, written by stage E), fills each claim's `risk` from the
batch file as the union of the tags of its `s_id` and its `s_ids` (never trusted from the model), keeps the claims in scope (`all`, or `risk` =
non-empty risk), and writes chunk_<j>.json ({chunk, scope, claims}) of at most --chunk-size claims
in document order, plus chunks.json ([{chunk, file, claim_ids}]). Exits 1 and lists every problem
(missing/malformed file, claim id not prefixed B<k>-, duplicate claim id, s_id not in its batch, a section
of a batch with neither a claim nor a `coverage_batch_<k>.json` entry, a section entry for a section that has
claims, and, with --scope risk, a risk-tagged sentence that is no claim's s_id/s_ids and has no waiver).
`claims_figures.json` (the main session's figure and embedded-object claims, `I*` ids, `section` "Figure") is also read when
present: those claims are always in scope (both scopes), carry `risk: ["figure"]`, are not looked up in the batch files (their
`s_id`, and any `s_ids`, must be a figure `p_id` of sections.json or of a batch file, or for `kind: "embedded"` any paragraph, sentence or row id; `kind: "figure"` (the default) also carries the int `figure` number), and are chunked after the text claims.
Every claim (text and `I*`) must carry `questions`: 1-3 non-empty strings, the facts it rests on as plain questions,
and `claim`: the statement made self-contained (subject, period, scope and unit taken from the quote, its block's `ctx`
and its section), at most MAX_CLAIM_WORDS words, with an optional `unresolved` list naming what no context supplied.
A text claim's `claim` may hold no number that is absent from that context (invented context is refused).
With --scope risk (Fast) a sentence is the anchor `s_id` of at most one in-scope text claim (non-empty risk); Deep may make several.
A claim may list every sentence it spans in an optional `s_ids` (anchor `s_id` included). It deletes the stale chunk_*.json and
findings_chunk_*.json first: after re-running a batch's stage E, every chunk is verified again.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling imports work under python -I too

import run_clock  # noqa: E402
from fact_check_invariants import COVERAGE_REASON, _norm, section_coverage


def _load(path: Path, errors: list[str]):
    if not path.exists():
        errors.append(f"{path.name}: missing")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        errors.append(f"{path.name}: malformed JSON ({e})")
        return None


def questions_problem(c: dict) -> str | None:
    """Every claim names the 1-3 facts it rests on, as plain questions stage V searches one by one."""
    q = c.get("questions")
    if isinstance(q, list) and 1 <= len(q) <= 3 and all(isinstance(x, str) and x.strip() for x in q):
        return None
    return "questions must be a list of 1-3 non-empty strings (the facts the claim rests on, as plain questions)"


MAX_CLAIM_WORDS = 40
DECK_MAX_CLAIM_WORDS = 55        # a deck claim also names its slide's scenario (spec §11 Y1)
UNRESOLVED = ("subject", "period", "scope", "unit")
_NUMBER = re.compile(r"\d(?:[\d,.]*\d)?")


_WORDS = {w: str(n) for n, w in enumerate(("zero one two three four five six seven eight nine ten eleven twelve "
                                            "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty").split())}
_SHORT_YEAR = re.compile(r"(?:\bFY\s?|')(\d{2})\b", re.I)


def _numbers(text: str) -> set[str]:
    return {m.replace(",", "") for m in _NUMBER.findall(str(text))}


def _known_numbers(text: str) -> set[str]:
    """The numbers a claim may use given its context: as written, number words as digits, and a short year (FY25,
    '25) as its full year. The claim side is normalised by _claim_numbers, never by widening this set."""
    t = str(text)
    out = _numbers(t)
    out |= {_WORDS[w] for w in re.findall(r"[a-z]+", t.casefold()) if w in _WORDS}
    out |= {f"20{y}" for y in _SHORT_YEAR.findall(t)}
    return out


def _claim_numbers(text: str) -> set[str]:
    """A claim's numbers, a short year (FY25, '25) read as its full year."""
    t = _SHORT_YEAR.sub(lambda m: m.group(0)[:-2] + "20" + m.group(1), str(text))
    return _numbers(t)


def _flat(value) -> list[str]:
    if isinstance(value, dict):
        return [x for v in value.values() for x in _flat(v)]
    if isinstance(value, list):
        return [x for v in value for x in _flat(v)]
    return [] if value is None else [str(value)]


def _section_text(sec: dict) -> str:
    parts = [str(sec.get("section", "")), *_flat(sec.get("heading_path"))]
    for p in sec.get("paragraphs", []) or []:
        parts += _flat(p.get("ctx")) + [str(x.get("text", "")) for x in p.get("sentences", []) or [] if isinstance(x, dict)]
    for t in sec.get("tables", []) or []:
        for r in t.get("rows", []) or []:
            if isinstance(r, dict):
                parts += _flat(r.get("cells")) + _flat(r.get("ctx"))
    return " ".join(parts)


def known_text_by_s_id(batch: dict, whole: dict[str, dict] | None = None) -> dict[str, str]:
    """Every s_id of a batch -> the text a claim on it may draw from: its section's heading path, sentences,
    table rows and every `ctx` value in that section, plus the batch's document date. `whole` (section_id ->
    sections.json record) supplies the full section when a long section was split across batches."""
    out: dict[str, str] = {}
    date = " ".join(_flat(batch.get("doc_date")))
    for sec in batch.get("sections", []) or []:
        full = (whole or {}).get(sec.get("section_id"))
        extra = _section_text(full) if full else ""
        parts = [date, str(sec.get("section", "")), *_flat(sec.get("heading_path"))]
        ids: list[str] = []
        for p in sec.get("paragraphs", []) or []:
            parts += _flat(p.get("ctx"))
            for x in p.get("sentences", []) or []:
                if isinstance(x, dict):
                    parts.append(str(x.get("text", "")))
                    ids.append(x.get("s_id"))
        for t in sec.get("tables", []) or []:
            for r in t.get("rows", []) or []:
                if isinstance(r, dict):
                    parts += _flat(r.get("cells")) + _flat(r.get("ctx"))
                    ids.append(r.get("s_id"))
        text = " ".join(parts) + " " + extra
        out.update({i: text for i in ids if isinstance(i, str)})
    return out


def claim_problem(c: dict, known_text: str | None = None) -> str | None:
    """Every claim carries `claim`, its self-contained restatement (what stage V checks); `quote` stays the verbatim
    anchor. A number in a text claim's `claim` must come from its quote or its context (known_text): None skips that
    check (figure claims, whose source is the image)."""
    stmt = c.get("claim")
    if not isinstance(stmt, str) or not stmt.strip():
        return "claim must be the statement made self-contained (subject, period, scope from the quote and its context)"
    limit = DECK_MAX_CLAIM_WORDS if re.match(r"^s\d+(?:-|$)", str(c.get("s_id") or "")) else MAX_CLAIM_WORDS
    if len(stmt.split()) > limit:
        return f"claim is {len(stmt.split())} words (limit {limit})"
    unresolved = c.get("unresolved", [])
    if not isinstance(unresolved, list) or any(u not in UNRESOLVED for u in unresolved):
        return f"unresolved must be a list drawn from {', '.join(UNRESOLVED)}"
    if known_text is not None:
        invented = sorted(_claim_numbers(stmt) - _known_numbers(f"{c.get('quote', '')} {known_text}"))
        if invented:
            return (f"claim holds {', '.join(invented)}, found in neither the quote nor its context: "
                    "take subject, period and scope only from the document, or list the gap in unresolved")
    return None


def _risk_by_s_id(batch: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for sec in batch.get("sections", []) or []:
        for p in sec.get("paragraphs", []) or []:
            for s in p.get("sentences", []) or []:
                if isinstance(s, dict) and s.get("s_id"):
                    out[s["s_id"]] = list(s.get("risk", []))
        for t in sec.get("tables", []) or []:
            for r in t.get("rows", []) or []:
                key = r.get("s_id") or r.get("r_id") if isinstance(r, dict) else None
                if key:
                    out[key] = list(r.get("risk", []))
    return out


def claim_risk(c: dict, risk: dict[str, list[str]]) -> list[str]:
    """A text claim's risk: the union of the batch-file tags over its anchor `s_id` and every id in `s_ids`
    (first-seen order, no duplicates). The one copy, used by chunk_claims.py and merge_findings.py."""
    ids = [c.get("s_id"), *(c.get("s_ids") if isinstance(c.get("s_ids"), list) else [])]
    out: list[str] = []
    for i in ids:
        if isinstance(i, str):
            out.extend(t for t in risk.get(i, []) if t not in out)
    return out


def risk_coverage_errors(k: int, batch: dict, claims: list, cov: list, cname: str, scope: str) -> list[str]:
    """Per-statement coverage for batch k; the one copy, called by chunk_claims.py (before stage V) and by
    merge_findings.py. Always validates the batch's waivers (`{s_id, reason: "no checkable statement"}`, the
    s_id a real one of the batch). In scope `risk`, every risk-tagged sentence or row must also be the `s_id`
    of a claim, listed in a claim's `s_ids`, waived, or inside a section a coverage entry covers."""
    errors: list[str] = []
    risk = _risk_by_s_id(batch)
    waived: set[str] = set()
    covered_sections: set[str] = set()
    for c in cov:
        if not isinstance(c, dict):
            continue
        if c.get("section_id"):
            if isinstance(c["section_id"], str):
                covered_sections.add(c["section_id"])
        elif c.get("s_id"):
            if not isinstance(c["s_id"], str):
                errors.append(f"{cname}: waiver s_id must be a string, got {c['s_id']!r}")
            elif _norm(str(c.get("reason", ""))) != COVERAGE_REASON:
                errors.append(f"{cname}: waiver for {c['s_id']!r}: reason {c.get('reason')!r} is not {COVERAGE_REASON!r}")
            elif c["s_id"] not in risk:
                errors.append(f"{cname}: waiver s_id {c['s_id']!r} is not a sentence or row of batch {k}")
            else:
                waived.add(c["s_id"])
    if scope != "risk":
        return errors
    claimed: set[str] = set()
    for c in claims:
        if isinstance(c, dict):
            if isinstance(c.get("s_id"), str):
                claimed.add(c["s_id"])
            if isinstance(c.get("s_ids"), list):
                claimed.update(x for x in c["s_ids"] if isinstance(x, str))
    for sec in batch.get("sections", []) or []:
        if not isinstance(sec, dict) or sec.get("section_id") in covered_sections:
            continue
        for s_id, tags in _risk_by_s_id({"sections": [sec]}).items():
            if tags and s_id not in claimed and s_id not in waived:
                errors.append(f"batch {k}: high-risk statement {s_id} ({', '.join(tags)}) in section "
                              f"{sec.get('section')!r} is neither claimed (anchor s_id or listed in a claim's s_ids) "
                              f"nor waived with {{s_id, reason: {COVERAGE_REASON!r}}} in {cname}")
    return errors


MAX_QUOTE_WORDS = 25
_QUOTE_FOLD = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-"})


def _fold(t: str) -> str:
    return " ".join(str(t).translate(_QUOTE_FOLD).split()).casefold()


def quote_warnings(k: int, batch: dict, claims: list) -> list[str]:
    """Warn (never fail) on text quotes over MAX_QUOTE_WORDS words, or not verbatim in the sentences they
    span. Table rows are checked for length only (a row quote may join cells). Figure claims are not text
    claims and are not checked here."""
    text: dict[str, str] = {}
    for sec in batch.get("sections", []) or []:
        for par in sec.get("paragraphs", []) or []:
            for sen in par.get("sentences", []) or []:
                if isinstance(sen, dict) and isinstance(sen.get("s_id"), str):
                    text[sen["s_id"]] = str(sen.get("text", ""))
    out: list[str] = []
    for c in claims:
        if not isinstance(c, dict):
            continue
        cid, quote = c.get("claim_id"), str(c.get("quote", ""))
        n = len(quote.split())
        if n > MAX_QUOTE_WORDS:
            out.append(f"claims_batch_{k}.json: {cid}: quote has {n} words (limit {MAX_QUOTE_WORDS})")
        sid = c.get("s_id")
        extra = c.get("s_ids") if isinstance(c.get("s_ids"), list) else []
        ids = [sid, *[x for x in extra if isinstance(x, str) and x != sid]]
        # malformed ids are reported as errors by chunk(); here they are simply not checked
        if isinstance(sid, str) and sid in text and _fold(quote) not in _fold(" ".join(text.get(i, "") for i in ids)):
            out.append(f"claims_batch_{k}.json: {cid}: quote is not verbatim in {', '.join(map(str, ids))} "
                       "(a comment anchored on its quote may be skipped)")
    return out


FIGURES_CLAIMS_FILE = "claims_figures.json"


def figure_claims(d: Path, batches: list[dict], errors: list[str]) -> list[dict]:
    """The `I*` claims of claims_figures.json, validated (id starts with I, unique, s_id/s_ids are figure holder ids)."""
    path = d / FIGURES_CLAIMS_FILE
    if not path.exists():
        return []
    data = _load(path, errors)
    if data is None:
        return []
    if not isinstance(data, list):
        errors.append(f"{FIGURES_CLAIMS_FILE}: must be a top-level JSON list")
        return []
    holders: dict[str, int] = {}          # figure holder p_id -> figure number
    elements: dict[str, set[int]] = {}    # deck figure holder -> shape ids of its pictures, charts, diagrams
    ids: set[str] = set()                 # every paragraph, sentence and row id (embedded objects anchor on any)
    sources = [d / "sections.json"] if (d / "sections.json").exists() else []
    sources += [d / f"batch_{b['batch']}.json" for b in batches if isinstance(b, dict) and isinstance(b.get("batch"), int)
                and (d / f"batch_{b['batch']}.json").exists()]
    for src in sources:
        try:
            doc = json.loads(src.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue                                   # a malformed batch file is reported by the text-claims pass
        for sec in (doc.get("sections", []) if isinstance(doc, dict) else doc if isinstance(doc, list) else []):
            if not isinstance(sec, dict):
                continue
            for f in sec.get("figures", []) or []:
                if isinstance(f, dict) and isinstance(f.get("p_id"), str):
                    ids.add(f["p_id"])
                    if isinstance(f.get("figure"), int):
                        holders[f["p_id"]] = f["figure"]
                    els = {e.get("shape_id") for e in f.get("elements") or [] if isinstance(e, dict)}
                    if els:
                        elements[f["p_id"]] = els
            for p in sec.get("paragraphs", []) or []:
                if isinstance(p, dict):
                    ids.update(x for x in [p.get("p_id"), *[s.get("s_id") for s in p.get("sentences", []) or []
                                                              if isinstance(s, dict)]] if isinstance(x, str))
            for tb in sec.get("tables", []) or []:
                for r in (tb.get("rows", []) if isinstance(tb, dict) else []) or []:
                    if isinstance(r, dict):
                        ids.update(x for x in (r.get("s_id"), r.get("r_id")) if isinstance(x, str))
    out: list[dict] = []
    seen: set[str] = set()
    for c in data:
        if not isinstance(c, dict):
            errors.append(f"{FIGURES_CLAIMS_FILE}: item {c!r} is not a JSON object")
            continue
        cid = str(c.get("claim_id", ""))
        if not cid.startswith("I"):
            errors.append(f"{FIGURES_CLAIMS_FILE}: {cid or '(no claim_id)'}: claim_id must start with I")
            continue
        if cid in seen:
            errors.append(f"{FIGURES_CLAIMS_FILE}: {cid}: duplicate claim_id")
            continue
        kind = c.get("kind", "figure")
        if kind not in ("figure", "embedded"):
            errors.append(f"{FIGURES_CLAIMS_FILE}: {cid}: kind {kind!r} must be 'figure' or 'embedded'")
            continue
        known = holders if kind == "figure" else ids
        what = ("the holder paragraph or row of a figure" if kind == "figure"
                else "a paragraph, sentence or row")
        sid = c.get("s_id")
        if not isinstance(sid, str) or sid not in known:
            errors.append(f"{FIGURES_CLAIMS_FILE}: {cid}: s_id {sid!r} is not {what} recorded in sections.json")
            continue
        if kind == "figure" and sid in elements:     # a deck slide with visual elements: the claim names one
            m = re.fullmatch(re.escape(sid) + r"-sh(\d+)", str(c.get("p_id") or ""))
            if not m or int(m.group(1)) not in elements[sid]:
                errors.append(f"{FIGURES_CLAIMS_FILE}: {cid}: p_id {c.get('p_id')!r} must name the slide element the "
                              f"claim reads, {sid}-sh<id> with id one of {sorted(elements[sid])} (figures[].elements "
                              "in sections.json)")
                continue
        if kind == "figure":
            fig = c.get("figure")
            if not isinstance(fig, int) or isinstance(fig, bool) or (sid in holders and holders[sid] != fig):
                errors.append(f"{FIGURES_CLAIMS_FILE}: {cid}: figure {fig!r} must be the int figure number of {sid} "
                              f"({holders.get(sid)!r} in sections.json)")
                continue
        extra = c.get("s_ids")
        if extra is not None:
            bad = [x for x in extra if not isinstance(x, str) or x not in known] if isinstance(extra, list) else [extra]
            if bad:
                errors.append(f"{FIGURES_CLAIMS_FILE}: {cid}: s_ids {bad!r} are not {what} recorded in sections.json")
                continue
        if why := questions_problem(c) or claim_problem(c):
            errors.append(f"{FIGURES_CLAIMS_FILE}: {cid}: {why}")
            continue
        seen.add(cid)
        out.append({**c, "risk": ["figure"]})
    return out


def auto_size(n: int) -> int:
    """Claims per chunk for `--chunk-size auto`: 3 for up to 20 in-scope claims (a one-slide deck's figure and its
    hardest text claim no longer share a worker), 4 for up to 80 (short decks verify in parallel), 6 beyond (a large
    deck keeps the number of chunks, and of main-session turns, bounded)."""
    return 3 if n <= 20 else 4 if n <= 80 else 6


def stride(claims: list[dict], size: int) -> list[list[dict]]:
    """Deal claims round-robin into ceil(n/size) chunks: figure claims (I*) first, then text claims in document order,
    so neighbours (one slide's hard claims) and figure claims land in different chunks and the verify stage does not
    wait on one heavy chunk (spec §12.4 T1). Each chunk keeps document order inside. Deterministic."""
    if not claims:
        return []
    n = -(-len(claims) // size)
    order = [c for c in claims if str(c.get("claim_id", "")).startswith("I")] + \
            [c for c in claims if not str(c.get("claim_id", "")).startswith("I")]
    chunks: list[list[dict]] = [[] for _ in range(n)]
    for k, c in enumerate(order):
        chunks[k % n].append(c)
    rank = {id(c): i for i, c in enumerate(claims)}
    return [sorted(ch, key=lambda c: rank[id(c)]) for ch in chunks]


CONTEXT_MAX_WORDS = 80


def slide_context_line(c: dict, whole: dict[str, dict]) -> str:
    """A deck claim's slide context as one line for stage V (spec §11 Y1): "Slide N — <title>. <lede> Key: <key>",
    at most CONTEXT_MAX_WORDS words. "" for a Word claim (its sections have no slide_context)."""
    m = re.match(r"^s(\d+)(?:-|$)", str(c.get("s_id") or ""))
    sec = whole.get(f"s{int(m.group(1)):02d}") if m else None
    ctx = (sec or {}).get("slide_context") or {}
    if not ctx:
        return ""
    line = f"Slide {int(m.group(1))} — " + " ".join(x for x in (
        ctx.get("title", "").rstrip(".") + "." if ctx.get("title") else "", ctx.get("lede", ""),
        f"Key: {ctx['key']}" if ctx.get("key") else "") if x)
    words = line.split()
    return " ".join(words[:CONTEXT_MAX_WORDS]) + ("…" if len(words) > CONTEXT_MAX_WORDS else "")


FIGURES_DROPPED_FILE = "figures_dropped.json"
DUPLICATE_SHARE = 0.9                     # a figure quote this much present in one block of the slide repeats it
_DECK_HOLDER = re.compile(r"^s(\d+)$")


def _sq(text: str) -> str:
    return re.sub(r"\W+", "", str(text).casefold())


def _repeats(quote: str, text: str) -> bool:
    """Does one block's text repeat the quote? Its letters (10-letter windows, the last one ending at the quote's
    end) are at least DUPLICATE_SHARE present AND every figure the quote states is in that text (a different number
    is a contradiction, not a repeat). Quotes under 10 letters are never judged duplicates."""
    q, t = _sq(quote), _sq(text)
    if len(q) < 10:
        return False
    starts = sorted(set(range(0, len(q) - 9, 5)) | {len(q) - 10})
    if sum(q[i:i + 10] in t for i in starts) / len(starts) < DUPLICATE_SHARE:
        return False
    nums = set(re.findall(r"\d+(?:[.,]\d+)*", str(text)))
    return all(n in nums for n in re.findall(r"\d+(?:[.,]\d+)*", str(quote)))


def duplicate_figures(d: Path, claims: list[dict], whole: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Deck figure claims that only repeat one text block of their slide (or its navigation) say nothing new:
    (kept, dropped). An omission claim ("… absent from figure …") is never dropped. Dropped ones are reported,
    never silently lost (spec §10 X3)."""
    try:
        chrome = json.loads((d / "stats.json").read_text(encoding="utf-8")).get("chrome", {}).get("texts", [])
    except (OSError, ValueError, AttributeError):
        chrome = []
    kept, dropped = [], []
    for c in claims:
        m = _DECK_HOLDER.match(str(c.get("s_id") or ""))
        sec = whole.get(f"s{int(m.group(1)):02d}") if m and c.get("kind", "figure") == "figure" else None
        if sec is None or "absent from figure" in str(c.get("quote", "")) + str(c.get("claim", "")):
            kept.append(c)
            continue
        blocks = [(p.get("p_id"), p.get("text", "")) for p in sec.get("paragraphs", []) or []]
        blocks += [(r.get("r_id"), " ".join(r.get("cells", []))) for t in sec.get("tables", []) or []
                   for r in t.get("rows", []) or []]
        blocks += [("navigation", t) for t in chrome]
        hit = next((pid for pid, text in blocks if _repeats(c.get("quote", ""), text)), None)
        if hit is None:
            kept.append(c)
        else:
            dropped.append({"claim_id": c["claim_id"], "quote": c.get("quote", ""), "duplicate_of": hit})
    return kept, dropped


def chunk(d: Path, scope: str = "all", size: int = 15, warnings: list[str] | None = None,
          dry_run: bool = False, spread: bool = False) -> tuple[list[dict], list[str]]:
    """Validate every batch's claims and write the chunks. dry_run: validate only, delete and write nothing."""
    if scope not in ("all", "risk"):
        raise ValueError(f"scope must be 'all' or 'risk', got {scope!r}")
    # A failed run must leave no chunk files behind, so clear them before validating.
    # Stage-V findings belong to the chunks being replaced; stale ones would be merged against new chunks.
    if not dry_run:
        for stale in (*d.glob("chunk_*.json"), *d.glob("findings_chunk_*.json")):
            stale.unlink()
        (d / "chunks.json").unlink(missing_ok=True)
        (d / FIGURES_DROPPED_FILE).unlink(missing_ok=True)
    errors: list[str] = []
    index = _load(d / "batches.json", errors)
    if index is None:
        index = []
    elif not isinstance(index, list):
        errors.append("batches.json: must be a top-level JSON list")
        index = []
    claims: list[dict] = []
    seen: set[str] = set()
    anchors: dict[str, str] = {}          # Fast scan: anchor s_id -> the claim that holds it
    try:                                  # whole sections, for the context of a section split across batches
        whole = {s["section_id"]: s for s in json.loads((d / "sections.json").read_text(encoding="utf-8"))
                 if isinstance(s, dict) and s.get("section_id")}
    except (OSError, ValueError, TypeError):
        whole = {}
    for b in index:
        k = b.get("batch") if isinstance(b, dict) else None
        if not isinstance(k, int) or isinstance(k, bool):
            errors.append(f"batches.json: record {b!r} has no integer 'batch'")
            continue
        batch = _load(d / f"batch_{k}.json", errors)
        found = _load(d / f"claims_batch_{k}.json", errors)
        if batch is None or found is None:
            continue
        if not isinstance(batch, dict):
            errors.append(f"batch_{k}.json: must be a JSON object")
            continue
        if not isinstance(found, list):
            errors.append(f"claims_batch_{k}.json: must be a top-level JSON list")
            continue
        risk = _risk_by_s_id(batch)
        known = known_text_by_s_id(batch, whole)
        cname = f"coverage_batch_{k}.json"
        cov = _load(d / cname, errors) if (d / cname).exists() else []
        if not isinstance(cov, list):
            if cov is not None:
                errors.append(f"{cname}: must be a top-level JSON list")
            cov = []
        if isinstance(b.get("sections"), list):
            _, cov_errors = section_coverage(k, b["sections"], [c for c in found if isinstance(c, dict)], cov, cname)
            errors.extend(cov_errors)
        errors.extend(risk_coverage_errors(k, batch, found, cov, cname, scope))
        if warnings is not None:
            warnings.extend(quote_warnings(k, batch, found))
        for c in found:
            if not isinstance(c, dict):
                errors.append(f"claims_batch_{k}.json: item {c!r} is not a JSON object")
                continue
            cid = str(c.get("claim_id", ""))
            if not cid.startswith(f"B{k}-"):
                errors.append(f"claims_batch_{k}.json: {cid or '(no claim_id)'}: claim_id must start with B{k}-")
                continue
            if cid in seen:
                errors.append(f"claims_batch_{k}.json: {cid}: duplicate claim_id")
                continue
            sid = c.get("s_id")
            if not isinstance(sid, str) or not sid:
                errors.append(f"claims_batch_{k}.json: {cid}: missing s_id")
                continue
            if sid not in risk:
                errors.append(f"claims_batch_{k}.json: {cid}: s_id {sid!r} is not a sentence or row of batch {k}")
                continue
            extra = c.get("s_ids")
            if extra is not None:
                if not isinstance(extra, list):
                    errors.append(f"claims_batch_{k}.json: {cid}: s_ids must be a list of s_id strings")
                    continue
                bad = [x for x in extra if not isinstance(x, str) or x not in risk]
                if bad:
                    errors.append(f"claims_batch_{k}.json: {cid}: s_ids {bad!r} are not sentences or rows of batch {k}")
                    continue
            if why := questions_problem(c) or claim_problem(c, known.get(sid, "")):
                errors.append(f"claims_batch_{k}.json: {cid}: {why}")
                continue
            tags = claim_risk(c, risk)
            if scope == "risk" and tags:                # Fast: one in-scope claim per anchor sentence
                if sid in anchors:
                    errors.append(f"claims_batch_{k}.json: {cid}: a Fast scan makes at most one claim per sentence; "
                                  f"{sid} is already the anchor of {anchors[sid]}")
                    continue
                anchors[sid] = cid
            seen.add(cid)
            claims.append({**c, "risk": tags})
    figs, dropped = duplicate_figures(d, [c for c in figure_claims(d, index, errors) if c["claim_id"] not in seen], whole)
    claims.extend(figs)
    if errors or dry_run:
        return [], errors
    if dropped:
        (d / FIGURES_DROPPED_FILE).write_text(json.dumps(dropped, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    in_scope = [c for c in claims if scope == "all" or c["risk"]]   # figure claims carry risk ["figure"]: always in scope
    in_scope = [{**c, "context": ctx} if (ctx := slide_context_line(c, whole)) else c for c in in_scope]
    size = auto_size(len(in_scope)) if size == "auto" else size
    chunks = stride(in_scope, size) if spread else [in_scope[i:i + size] for i in range(0, len(in_scope), size)]
    out = []
    for j, cs in enumerate(chunks, 1):
        name = f"chunk_{j}.json"
        (d / name).write_text(json.dumps({"chunk": j, "scope": scope, "claims": cs}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out.append({"chunk": j, "file": name, "claim_ids": [c["claim_id"] for c in cs]})
    (d / "chunks.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out, []


def _chunk_size(v: str):
    return "auto" if v == "auto" else int(v)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dir", help="the directory the reader (sections.py or pptx_sections.py) wrote, holding claims_batch_<k>.json")
    ap.add_argument("--scope", choices=("all", "risk"), default="all", help="all (Deep) or risk (Fast)")
    ap.add_argument("--chunk-size", type=_chunk_size, default=15, help="claims per verify chunk (default 15), or "
                    "auto: 4 up to 80 in-scope claims, 6 beyond")
    ap.add_argument("--clock", metavar="WORK_DIR", help="record this step's start and end in <work dir>/timings.json")
    ap.add_argument("--stride", action="store_true", help="deal claims round-robin (figure claims first) instead of "
                    "consecutive chunks, so heavy claims spread across workers (deck-fact-check)")
    ap.add_argument("--check-batch", type=int, metavar="K", help="a stage-E worker's self-check: list only batch K's "
                    "problems and write nothing (exit 0 and 'batch K: OK' when there are none)")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if a.chunk_size != "auto" and a.chunk_size < 1:
        print("chunk_claims.py: --chunk-size must be at least 1", file=sys.stderr)
        return 2
    warnings: list[str] = []
    if a.check_batch is not None:
        k = a.check_batch
        _, errors = chunk(Path(a.dir), a.scope, a.chunk_size, warnings=warnings, dry_run=True)
        mine = [e for e in errors if e.startswith((f"claims_batch_{k}.json", f"coverage_batch_{k}.json",
                                                   f"batch {k}:", f"batch_{k}.json"))]
        if not mine:
            print(f"batch {k}: OK")
            return 0
        print(f"batch {k}: {len(mine)} problem(s); fix them in claims_batch_{k}.json / coverage_batch_{k}.json and "
              "run this again:")
        for e in mine:
            print(f"  - {e}")
        return 2
    if a.clock:
        run_clock.stamp(a.clock, "chunk", "start")
    index, errors = chunk(Path(a.dir), a.scope, a.chunk_size, warnings=warnings, spread=a.stride)
    if warnings:
        print(f"chunk_claims.py: {len(warnings)} quote warning(s) (not errors):", file=sys.stderr)
        for w in warnings:
            print(f"  warning: {w}", file=sys.stderr)
    if errors:
        print(f"chunk_claims.py: {len(errors)} problem(s), no chunks written:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"{sum(len(c['claim_ids']) for c in index)} claims in scope ({a.scope}) -> {len(index)} chunks")
    if a.clock:
        run_clock.stamp(a.clock, "chunk", "end")
    return 0


if __name__ == "__main__":
    sys.exit(main())
