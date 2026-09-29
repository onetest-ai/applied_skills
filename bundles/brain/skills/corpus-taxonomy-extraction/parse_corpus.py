#!/usr/bin/env python3
"""Deterministic corpus parser for corpus-taxonomy-extraction.

Converts a heterogeneous document corpus into uniform Markdown that a
Sonnet model can read. No LLM, no torch (docling is retired):
  - .pdf         -> PyMuPDF text layer
  - .pptx/.docx  -> LibreOffice (soffice) -> PDF -> PyMuPDF text layer
  - .xlsx/.xlsm  -> openpyxl read_only structure-dump
  - .html/.htm   -> PyMuPDF HTML renderer (degraded fidelity, no JavaScript)
  - .md/.txt     -> passthrough (already the parsed-store format)
This is the TEXT-layer path. Visual/diagram pages (flows, timelines, complex
tables) collapse under any text extractor — those go through the `visual-parse`
skill (render page -> VLM transcription + deterministic table extraction).
Writes one .md per source file plus a manifest.json.

Usage:
  parse_corpus.py --corpus <dir> --out <dir> [--xlsx-max-mb 20] [--sample-rows 8]
A file that cannot be parsed is reported in one `[ERR] <file>: <reason>` line and an
`error` manifest entry; pass --verbose for the full traceback.
"""
import argparse, json, os, shutil, sqlite3, subprocess, sys, tempfile, warnings, traceback
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
        r = subprocess.run([so, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--convert-to", "pdf", "--outdir", tmp, path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
        pdf = os.path.join(tmp, os.path.splitext(os.path.basename(path))[0] + ".pdf")
        # soffice can also exit 0 without writing a PDF; either way the file is the
        # problem, and the command line is noise the user can't act on.
        if r.returncode != 0 or not os.path.exists(pdf):
            lines = [ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()]
            # soffice prefixes unrelated warnings (Fontconfig, Java); its verdict is the "Error" line
            tail = " ".join([ln for ln in lines if "error" in ln.lower()][-2:] or lines[-1:])
            raise RuntimeError("soffice could not convert it — not a readable Office file (truncated download?)"
                               + (f"; soffice said: {tail}" if tail else ""))
        return parse_pdf_pymupdf(pdf)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)

def parse_xlsx_structure(path, sample_rows=None):
    """Workbook text dump with one Markdown section per non-empty data row.

    ``sample_rows`` limits each sheet when it is a positive integer. ``None`` or
    zero reads the full sheet.  This remains a narrative map of workbook content;
    governed numeric answers still come from the tabular semantic layer.
    """
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        try:
            dims = ws.calculate_dimension(force=True)
        except (AttributeError, TypeError, ValueError):
            dims = "unknown"
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if sample_rows and i >= sample_rows:
                break
            cells = ["" if value is None else " ".join(str(value).split()) for value in row]
            rows.append((i + 1, cells))

        nonempty = [(number, cells) for number, cells in rows if any(cells)]
        if not nonempty:
            continue

        # Reporting sheets often have one or two decorative rows above the real
        # header. The densest text row in the first ten non-empty rows is a stable
        # deterministic approximation and works for ordinary single-table sheets.
        candidates = nonempty[:10]
        header_number, headers = max(
            candidates,
            key=lambda item: (
                sum(bool(cell) for cell in item[1]),
                sum(bool(cell) and not cell.replace(".", "", 1).isdigit() for cell in item[1]),
            ),
        )
        labels = [header or f"Column {i + 1}" for i, header in enumerate(headers)]
        out.append(
            f"\n\n## sheet: {ws.title} · schema  (dims={dims})\n\n"
            + "Columns: " + "; ".join(label for label in labels if label) + "\n"
        )

        for row_number, cells in nonempty:
            if row_number <= header_number:
                continue
            fields = []
            for i, value in enumerate(cells):
                if not value:
                    continue
                label = labels[i] if i < len(labels) else f"Column {i + 1}"
                fields.append(f"- {label}: {value}")
            if fields:
                out.append(
                    f"\n\n## sheet: {ws.title} · row {row_number}  (dims={dims})\n\n"
                    + "\n".join(fields) + "\n"
                )
    wb.close()
    return "".join(out).rstrip() + "\n"

def _merge_speaker_turns(groups, merge_cues):
    """Merge consecutive same-speaker cue groups into turns.

    Each group is {"ts": str, "speaker": str, "text": str}.
    Returns list of same shape where consecutive same-speaker groups within
    the merge_cues budget are joined (text concatenated with a space).
    merge_cues=1 returns a copy of groups unchanged.
    """
    if merge_cues <= 1 or not groups:
        return list(groups)
    turns = []
    buf_ts = groups[0]["ts"]
    buf_speaker = groups[0]["speaker"]
    buf_texts = [groups[0]["text"]]
    for g in groups[1:]:
        same_speaker = (g["speaker"] == buf_speaker) or (not g["speaker"] and not buf_speaker)
        within_budget = len(buf_texts) < merge_cues
        if same_speaker and within_budget:
            buf_texts.append(g["text"])
        else:
            turns.append({"ts": buf_ts, "speaker": buf_speaker, "text": " ".join(buf_texts)})
            buf_ts = g["ts"]
            buf_speaker = g["speaker"]
            buf_texts = [g["text"]]
    turns.append({"ts": buf_ts, "speaker": buf_speaker, "text": " ".join(buf_texts)})
    return turns


def _fold_interjections(turns, min_chars):
    """Fold a turn shorter than min_chars into the previous turn, inline as "[Speaker: text]".

    A listener's "Mhm." or a short answer like "Three." then stays next to the turn it
    responds to instead of becoming its own section (and so its own chunk). Every word is
    kept. Square brackets, because Teams speaker names carry parentheses ("Name (Partner)")
    and transcript text carries none. The first turn has no predecessor and is kept as is.
    min_chars <= 0 returns a copy unchanged.
    """
    if min_chars <= 0:
        return [dict(t) for t in turns]
    out = []
    for t in turns:
        if out and len(t["text"]) < min_chars:
            said = "%s: %s" % (t["speaker"], t["text"]) if t["speaker"] else t["text"]
            out[-1] = {**out[-1], "text": "%s [%s]" % (out[-1]["text"], said)}
        else:
            out.append(dict(t))
    return out


def _pack_turns(turns, max_chars, label=lambda ts: ts):
    """Group consecutive turns (any speaker) into packs whose rendered size stays within
    max_chars, so a short answer stays next to its question in one section (one chunk).

    Rendered size of one turn = len(label(ts)) + 1 + (len(speaker) + 2 if speaker else 0) +
    len(text) + 2, where label(ts) is the turn's actual display timestamp — this mirrors
    what _render_turn_sections actually emits per turn. label defaults to identity (the
    turn's ts is already display-formatted, as SRT's is); pass the format function (e.g.
    _vtt_label) when ts is still raw, so an H:MM:SS label (past the first hour) is counted
    at its real length instead of the MM:SS default. A turn whose own rendered size exceeds
    max_chars is still emitted, alone, as its own pack. max_chars <= 0 returns one pack per
    turn (packing off)."""
    if max_chars <= 0:
        return [[t] for t in turns]
    packs, cur, size = [], [], 0
    for t in turns:
        speaker_part = (len(t["speaker"]) + 2) if t["speaker"] else 0
        n = len(label(t["ts"])) + 1 + speaker_part + len(t["text"]) + 2
        if cur and size + n > max_chars:
            packs.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += n
    if cur:
        packs.append(cur)
    return packs


def _render_turn_sections(packs, label):
    """One ## section per pack. A single-turn pack keeps today's per-turn format exactly
    (speaker in the heading plus a `<!-- speaker -->` marker). A multi-turn pack gets a
    time-range heading and one "MM:SS Speaker: text" (or "MM:SS text") paragraph per turn,
    with no marker — the indexer would otherwise lift only the first speaker for the whole
    section. `seq`/cue numbers count turns, not packs, across the whole document."""
    out, seq = [], 0
    for pack in packs:
        first = seq + 1
        seq += len(pack)
        if len(pack) == 1:
            t = pack[0]
            who = (" — %s" % t["speaker"]) if t["speaker"] else ""
            marker = ("<!-- speaker: %s -->\n\n" % t["speaker"]) if t["speaker"] else ""
            out.append("\n## %s%s (cue %d)\n\n%s%s\n" % (label(t["ts"]), who, seq, marker, t["text"]))
        else:
            body = "\n\n".join(
                (("%s %s: %s" % (label(t["ts"]), t["speaker"], t["text"])) if t["speaker"]
                 else ("%s %s" % (label(t["ts"]), t["text"])))
                for t in pack
            )
            out.append("\n## %s–%s (cues %d–%d)\n\n%s\n" % (label(pack[0]["ts"]), label(pack[-1]["ts"]), first, seq, body))
    return "\n".join(out)


def _hms_label(hour, minute, second):
    """MM:SS, or H:MM:SS when hour is non-zero (a cue past the first hour renders
    unambiguously instead of wrapping back to 00:SS). Meetings under an hour are
    byte-identical to before this fix — the hour prefix only appears when needed."""
    if int(hour) > 0:
        return "%d:%s:%s" % (int(hour), minute, second)
    return "%s:%s" % (minute, second)


def _vtt_label(ts):
    """WebVTT timestamp -> MM:SS, or H:MM:SS once the hour part is non-zero. HH:MM:SS[.mmm]
    -- strip fractional, split on colon; falls back to the first 5 chars if ts is too short."""
    parts = ts.split(".")[0].split(":")
    if len(parts) >= 3:
        return _hms_label(parts[-3], parts[-2].zfill(2), parts[-1].zfill(2))
    if len(parts) == 2:
        return _hms_label(0, parts[-2].zfill(2), parts[-1].zfill(2))
    return ts[:5]


def _parse_srt(path, merge_cues=1, fold=0, pack=0):
    """SRT -> headed Markdown. Each numbered cue block -> one ## heading.
    merge_cues>1 joins consecutive same-speaker cues into speaker turns; fold>0 folds
    turns shorter than fold chars into the previous turn; pack>0 groups consecutive
    turns into one section up to pack chars."""
    import re
    # utf-8-sig strips a leading BOM if present (no-op otherwise) — a BOM'd sequence-number
    # line fails .isdigit(), silently dropping the first cue.
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    # Split on blank lines between cue blocks
    blocks = re.split(r"\n\s*\n", text.strip())
    groups = []
    for block in blocks:
        rows = [r.strip() for r in block.strip().splitlines() if r.strip()]
        if not rows:
            continue
        # First line is sequence number, second is timestamp, rest is text
        i = 0
        if rows[i].isdigit():
            i += 1
        if i < len(rows) and re.match(r"\d{1,2}:\d{2}:\d{2},\d+ --> ", rows[i]):
            ts = rows[i].split("-->")[0].strip()  # start timestamp
            cue_text = " ".join(rows[i+1:])
            if cue_text.strip():
                speaker, cue_text = _speaker_and_text(cue_text)
                speaker, cue_text = _clean_cue_text(speaker), _clean_cue_text(cue_text)
                # Emptiness is checked AFTER cleaning (matching VTT's filter at
                # `if full_text:` below) — a cue that is markup-only after stripping tags
                # (e.g. `Ann: <00:00:01.500>`) must produce no turn, not a hollow one.
                if cue_text:
                    # Format: MM:SS from HH:MM:SS,mmm, or H:MM:SS once the hour part is non-zero.
                    parts = ts.split(":")
                    label = _hms_label(parts[0], parts[1], parts[2].split(",")[0])
                    groups.append({"ts": label, "speaker": speaker, "text": cue_text.strip()})

    turns = _fold_interjections(_merge_speaker_turns(groups, merge_cues), fold)
    return _render_turn_sections(_pack_turns(turns, pack), lambda ts: ts)


def _parse_vtt(path, merge_cues=1, fold=0, pack=0):
    """WebVTT -> headed Markdown. Multi-line cues (same UUID prefix) merged.
    merge_cues>1 joins consecutive same-speaker UUID groups into speaker turns; fold>0
    folds turns shorter than fold chars into the previous turn; pack>0 groups consecutive
    turns into one section up to pack chars."""
    import re
    # utf-8-sig strips a leading BOM if present (no-op otherwise) — without it a BOM'd
    # "WEBVTT" line fails the ^WEBVTT header-strip regex below.
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    # Remove WEBVTT header and NOTE blocks
    body = re.sub(r"^WEBVTT.*?\n", "", text, flags=re.MULTILINE)
    blocks = re.split(r"\n\s*\n", body.strip())
    merged = {}  # base_id -> {"ts": str, "text": [str], "speaker": str}
    order = []
    for block_idx, block in enumerate(blocks):
        rows = [r.strip() for r in block.strip().splitlines() if r.strip()]
        if not rows:
            continue
        # Detect cue block: first line is ID or timestamp
        i = 0
        cue_id = None
        if i < len(rows) and not re.match(r"\d{1,2}:\d{2}[\d:\.]+\s+-->", rows[i]):
            cue_id = rows[i]
            i += 1
        if i < len(rows) and re.match(r"[\d:\.]+\s+-->", rows[i]):
            ts = rows[i].split("-->")[0].strip()
            cue_text = " ".join(rows[i+1:]).strip()
            if not cue_text:
                continue
            speaker, cue_text = _speaker_and_text(cue_text)
            speaker, cue_text = _clean_cue_text(speaker), _clean_cue_text(cue_text)
            # Multi-line cues sharing a UUID base (ids "uuid-0", "uuid-1", …) merge
            # by stripping the trailing -N fragment counter. Id-less cues get a
            # unique base per block so distinct cues that happen to share a start
            # timestamp are never collapsed together.
            base = re.sub(r"-\d+$", "", cue_id) if cue_id else "__cue_%d__" % block_idx
            if base not in merged:
                merged[base] = {"ts": ts, "text": [], "speaker": speaker}
                order.append(base)
            elif speaker and not merged[base]["speaker"]:
                merged[base]["speaker"] = speaker
            merged[base]["text"].append(cue_text)

    # Build UUID-group list
    groups = []
    for base in order:
        entry = merged[base]
        full_text = " ".join(entry["text"]).strip()
        if full_text:
            groups.append({"ts": entry["ts"], "speaker": entry["speaker"], "text": full_text})

    # Merge consecutive same-speaker groups into turns, then fold interjections
    turns = _fold_interjections(_merge_speaker_turns(groups, merge_cues), fold)
    return _render_turn_sections(_pack_turns(turns, pack, _vtt_label), _vtt_label)


def _parse_ai_dial_json(path):
    """AI DIAL conversation JSON → Markdown of assistant messages.

    Format: {history: [{name: str, messages: [{role, content}]}]}
    Only assistant messages with ≥50 chars are included.
    Returns a Markdown string, or None if the file is not AI DIAL format or has no usable content.
    """
    import json as _json
    with open(path, encoding="utf-8", errors="replace") as f:
        d = _json.loads(f.read())
    if not isinstance(d, dict) or "history" not in d:
        return None
    parts = []
    for conv in d.get("history", []):
        name = conv.get("name", "conversation")
        conv_parts = []
        for msg in conv.get("messages", []):
            if msg.get("role") == "assistant":
                raw = msg.get("content") or ""
                if isinstance(raw, list):
                    # Handle both plain strings and structured content blocks
                    # {"type": "text", "text": "..."} as used by Anthropic API exports
                    parts_raw = []
                    for p in raw:
                        if isinstance(p, str):
                            parts_raw.append(p)
                        elif isinstance(p, dict) and p.get("text"):
                            parts_raw.append(str(p["text"]))
                    raw = " ".join(parts_raw)
                content = raw.strip()
                if len(content) >= 50:
                    conv_parts.append(content)
        if conv_parts:
            parts.append(f"# {name}\n\n" + "\n\n---\n\n".join(conv_parts))
    if not parts:
        return None
    return "\n\n".join(parts)


# Capitalised words that commonly precede a colon at the start of a caption but
# are NOT speaker names — kept lowercase for case-insensitive matching. Without
# this guard "Note: ...", "Today: ...", "Warning: ..." get misread as speakers,
# polluting the speaker markers that drive speaker-scoped search.
_NON_SPEAKER_PREFIXES = frozenset({
    "note", "notes", "today", "tomorrow", "yesterday", "ok", "okay", "yes", "no",
    "so", "well", "actually", "right", "first", "second", "third", "next", "then",
    "step", "warning", "error", "caution", "important", "update", "summary",
    "question", "answer", "action", "agenda", "topic", "example", "tip", "re",
    "subject", "from", "to", "date", "time", "edit", "ps", "aside", "recap",
})


def _speaker_and_text(text: str) -> tuple[str, str]:
    """Extract WebVTT voice tags and conservative ``Name: text`` prefixes.

    The ``Name:`` fallback (used by SRT and by VTT cues without <v> tags) only
    fires for name-shaped prefixes: one to three capitalised words, letters and
    name punctuation only, and whose lead word is not a common sentence-opening
    word (see ``_NON_SPEAKER_PREFIXES``).
    """
    import re
    voice = re.match(r"\s*<v(?:\.[^ >]+)*\s+([^>]+)>\s*(.*)", text, flags=re.I | re.S)
    if voice:
        return voice.group(1).strip(), re.sub(r"</?v[^>]*>", "", voice.group(2)).strip()
    labelled = re.match(
        r"\s*([A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,2}):\s+(.+)", text, flags=re.S)
    if labelled:
        name = labelled.group(1).strip()
        lead = name.split()[0].lower().strip(".'’-")
        if lead not in _NON_SPEAKER_PREFIXES:
            return name, labelled.group(2).strip()
    return "", re.sub(r"</?v[^>]*>", "", text).strip()


def _clean_cue_text(text):
    """Drop WebVTT inline markup (<b>, <i>, <u>, <c.class>, <lang ..>, <ruby>, <rt>, cue timestamps
    <00:01.000>) and decode HTML entities (&amp; -> &). Speaker <v> tags are handled by _speaker_and_text.

    Per the WebVTT spec, only <v …> and <lang …> carry a space-separated annotation; <b>, <i>,
    <u>, <c>, <ruby>, <rt> take only an optional .class suffix — a broader pattern would swallow
    literal spoken text shaped like a tag (e.g. "<i can't believe it>") as silent word loss.

    html.unescape can turn `&#10;`/`&NewLine;` (or `&#13;`) into a real newline/carriage
    return, which would otherwise let a decoded entity open a live `##`/`#` heading inside a
    transcript section (seeding parent_heading/breadcrumb_path — the CLAUDE.md preamble/H1
    invariant) or split a multi-turn pack's one-paragraph-per-turn format. Collapsing
    whitespace after unescaping is golden-safe: cue rows are already splitlines()-joined
    before this runs, so no newline survives in today's corpus.
    """
    import html, re
    cleaned = html.unescape(re.sub(
        r"</?(?:b|i|u|c|ruby|rt)(?:\.[\w.-]+)?>|</?lang(?:\.[\w.-]+)?(?:\s[^<>]*)?>|<\d{2}:[\d:.]+>",
        "", text))
    return " ".join(cleaned.split())


def _parse_text(path):
    """Markdown/plain text -> itself. Markdown IS the parsed-store format, so a
    pre-processed corpus needs no conversion — only the standard `# SOURCE:`
    header the writer prepends. Decoding is lenient for the same reason the
    transcript parsers are: a corpus is not guaranteed to be clean UTF-8."""
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().strip()


def _video_lane_consumed(out):
    """``{input_rel: video_rel}`` (``/``-separated) for the transcripts the video lane has
    consumed into THIS out dir.

    Read from the out dir's manifest as it stands BEFORE this run's merge: every entry
    with ``method == "video-lane"`` whose ``md`` exists contributes its ``inputs`` other
    than the video itself. Consumption is keyed on what the video lane actually used, not
    on file names — a Teams ``.docx`` transcript is named after the meeting, not the
    recording — so a video assembled with ``--transcript asr`` (no sidecar in ``inputs``)
    consumes nothing, a corpus that never ran the video lane parses exactly as before,
    and a video doc that has since been removed gives its transcript back.
    """
    path = os.path.join(out, "manifest.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    consumed = {}
    for e in data if isinstance(data, list) else []:
        if not (isinstance(e, dict) and e.get("method") == "video-lane" and e.get("source")
                and e.get("md") and os.path.isfile(os.path.join(out, e["md"]))):
            continue
        video = e["source"].replace(os.sep, "/")
        for inp in e.get("inputs") or []:
            inp = str(inp).replace(os.sep, "/")
            if inp != video:
                consumed[inp] = video
    return consumed


def _merge_manifest(path, fresh, allow):
    """Keep entries this run did not re-derive (other formats, the video lane); replace the rest."""
    old = []
    if os.path.exists(path):
        with open(path) as f:
            old = json.load(f)
    kept = [e for e in old if os.path.splitext(e.get("source", ""))[1].lower() not in allow]
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kept + fresh, f, indent=2)
    os.replace(tmp, path)


HTML_MIN_TEXT = 220   # mirrors render_pages.py's --min-text 220 rather than inventing a
                      # second notion of "too little text"; tunable per corpus via min_text


def _parse_html(path, min_text=HTML_MIN_TEXT):
    """HTML -> text via PyMuPDF's own renderer. NO JavaScript runs here.

    Returns ("", "skipped-js-rendered") when extraction yields almost nothing: the
    document was built by JS and we captured none of it. Storing those few stray
    words would put them in the index to be cited as if they were the deck.

    HTML has no pages — PyMuPDF's print-pagination of it is an artifact of print
    CSS, not of the document (the same fact the full-fidelity capture path segments
    by DOM instead of pagination for). Emitting `## [part N]` headings here would
    promote that pagination to citable chunk boundaries and make the same deck
    ingested both ways cite incompatible targets. So this joins the per-page text
    into ONE un-paginated body instead.
    """
    import pymupdf
    doc = pymupdf.open(path, filetype="html")
    raw_texts = [(page.get_text() or "").strip() for page in doc]
    doc.close()
    raw_texts = [t for t in raw_texts if t]
    # Check against raw text length to detect JS-rendered docs with no content
    raw_len = len("".join(raw_texts))
    if raw_len < min_text:
        return "", "skipped-js-rendered"
    md = "\n\n".join(raw_texts).strip()
    return md, "pymupdf-html"


def parse_one(path, xlsx_max_mb, sample_rows, merge_cues=1, fold=0, pack=0):
    ext = os.path.splitext(path)[1].lower()
    size_mb = os.path.getsize(path) / 1e6
    if ext in (".pptx", ".docx", ".ppt", ".doc"):
        return parse_office_pymupdf(path), "soffice+pymupdf"
    if ext == ".pdf":
        return parse_pdf_pymupdf(path), "pymupdf"
    if ext in (".xlsx", ".xlsm"):
        # Small workbooks are useful narrative/entity sources and are cheap to
        # read in full. Large reporting books stay bounded to a structural sample;
        # their complete numeric data belongs in the deterministic facts lane.
        row_limit = None if size_mb <= xlsx_max_mb else sample_rows
        return parse_xlsx_structure(path, row_limit), "openpyxl-structure"
    if ext == ".srt":
        return _parse_srt(path, merge_cues=merge_cues, fold=fold, pack=pack), "transcript-etl"
    if ext == ".vtt":
        return _parse_vtt(path, merge_cues=merge_cues, fold=fold, pack=pack), "transcript-etl"
    if ext == ".json":
        return _parse_ai_dial_json(path), "ai-dial-json"
    if ext in (".md", ".markdown", ".txt"):
        return _parse_text(path), "passthrough"
    if ext in (".html", ".htm"):
        md, how = _parse_html(path)
        return (md or None), how
    return None, "skipped"

def registered_paths(db: str, root_key: str) -> set[str]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT relative_path FROM sources WHERE root_key=? AND state='active'", (root_key,)).fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--xlsx-max-mb", type=float, default=20.0)
    ap.add_argument("--sample-rows", type=int, default=8)
    ap.add_argument("--formats", default="pptx,docx,pdf,xlsx,xlsm,vtt,srt,json,md,markdown,txt,html,htm",
                    help="comma-separated extensions (no dot) to include")
    ap.add_argument("--merge-cues", type=int, default=1,
                    help="join N consecutive same-speaker VTT/SRT cues into one chunk (default: 1 = per-cue)")
    ap.add_argument("--registry-db", help="parse only files registered as active sources in this store's source registry")
    ap.add_argument("--root-key", help="source registry root key that --corpus points at (with --registry-db)")
    ap.add_argument("--fold-interjections", type=int, default=0,
                    help="VTT/SRT: fold a turn shorter than N chars (\"Mhm.\", \"Three.\") into the previous "
                         "turn as \"[Speaker: text]\" instead of its own chunk (default: 0 = off)")
    ap.add_argument("--pack-turns", type=int, default=0,
                    help="VTT/SRT: group consecutive turns into one section up to N chars (default: 0 = off)")
    ap.add_argument("--verbose", action="store_true",
                    help="print the full traceback for a file that fails to parse (default: one line per file)")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="corpus-relative glob to skip (repeatable; pass the source root's brain.toml "
                         "`exclude` globs). Scribe-marked files are always skipped.")
    a = ap.parse_args(argv)
    # The loop guard's shared predicate (byte-identical copy in knowledge-pipeline/),
    # loaded here rather than at import time: scribe loads this module by file path.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import scribe_marker
    if bool(a.registry_db) != bool(a.root_key):
        ap.error("--registry-db and --root-key go together")
    registered = registered_paths(a.registry_db, a.root_key) if a.registry_db else None
    allow = {"." + e.strip().lower().lstrip(".") for e in a.formats.split(",") if e.strip()}
    os.makedirs(a.out, exist_ok=True)
    manifest = []
    consumed = _video_lane_consumed(a.out)
    for root, _, files in os.walk(a.corpus):
        for fn in sorted(files):
            src = os.path.join(root, fn)
            rel = os.path.relpath(src, a.corpus)
            posix_rel = rel.replace(os.sep, "/")
            # Checked ahead of the format filter (unlike exclude/scribe-marker below):
            # a hidden file must never surface in the manifest as a silently-dropped
            # extension, and a hidden DIRECTORY (.cache/x.pdf) must be skipped even
            # when the file's own extension is otherwise parseable.
            if scribe_marker.is_hidden(src, posix_rel):
                manifest.append({"source": rel, "skipped": True, "method": "hidden", "reason": "hidden"})
                stale = os.path.join(a.out, rel.replace(os.sep, "__") + ".md")
                if os.path.exists(stale):
                    os.remove(stale)
                print(f"[skip] {'hidden':20} {rel}", file=sys.stderr)
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in allow:
                continue
            reason = scribe_marker.skip_reason(src, posix_rel, a.exclude)
            if reason:
                # Same predicate source_registry uses, so a file the registry never registers
                # is never parsed into an unmanaged doc (which strict brain_sync refuses).
                manifest.append({"source": rel, "skipped": True, "method": reason, "reason": reason})
                stale = os.path.join(a.out, rel.replace(os.sep, "__") + ".md")
                if os.path.exists(stale):
                    os.remove(stale)
                print(f"[skip] {reason:20} {rel}", file=sys.stderr)
                continue
            if rel.replace(os.sep, "/") in consumed:
                # The video lane already owns this transcript (see _video_lane_consumed).
                manifest.append({"source": rel, "skipped": True, "method": "consumed-by-video",
                                 "consumed_by": consumed[rel.replace(os.sep, "/")]})
                # Remove stale parsed doc from a previous parse run
                stale = os.path.join(a.out, rel.replace(os.sep, "__") + ".md")
                if os.path.exists(stale):
                    os.remove(stale)
                continue
            if registered is not None and rel.replace(os.sep, "/") not in registered:
                manifest.append({"source": rel, "skipped": True, "method": "unregistered"})
                stale = os.path.join(a.out, rel.replace(os.sep, "__") + ".md")
                if os.path.exists(stale):
                    os.remove(stale)
                continue
            try:
                md, method = parse_one(src, a.xlsx_max_mb, a.sample_rows, merge_cues=a.merge_cues,
                                       fold=a.fold_interjections, pack=a.pack_turns)
                if not md:
                    manifest.append({"source": rel, "skipped": True, "method": method})
                    continue
                safe = rel.replace(os.sep, "__") + ".md"
                outp = os.path.join(a.out, safe)
                with open(outp, "w") as f:
                    f.write(f"# SOURCE: {rel}\n# method: {method}\n# fidelity: "
                            f"{'degraded' if method == 'pymupdf-html' else 'full'}\n\n{md}")
                manifest.append({"source": rel, "md": safe, "method": method,
                                 "chars": len(md), "size_mb": round(os.path.getsize(src)/1e6, 2)})
                print(f"[ok] {method:20} {len(md):>8} chars  {rel}", file=sys.stderr)
            except Exception as e:
                print(f"[ERR] {rel}: {e}", file=sys.stderr)
                if a.verbose:
                    traceback.print_exc(file=sys.stderr)
                manifest.append({"source": rel, "error": str(e)})
    _merge_manifest(os.path.join(a.out, "manifest.json"), manifest, allow)
    ok = [m for m in manifest if "error" not in m]
    print(f"\nparsed {len(ok)}/{len(manifest)} files -> {a.out}", file=sys.stderr)

if __name__ == "__main__":
    main()
