"""A synced document folder (SharePoint/OneDrive library): a new or changed file is evidence that a deliverable
appeared or moved. One evidence item per file version, carrying a short text excerpt the agent can quote.

Text comes from the standard library only: .docx/.pptx/.xlsx are zip+XML, .md/.txt/.csv are read as text, .pdf
uses `pdftotext` when it is on PATH (otherwise the item carries the file's name and dates only).
"""
import datetime as dt
import fnmatch
import os
import re
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .. import store as S

DEFAULT_INCLUDE = ["*.docx", "*.pptx", "*.xlsx", "*.pdf", "*.md", "*.txt", "*.csv"]
EXCERPT_CHARS = 1500
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _xml(z, name):
    return ET.fromstring(z.read(name))


def excerpt(path, limit=EXCERPT_CHARS):
    """First `limit` characters of a document's text, whitespace-collapsed; '' when unreadable."""
    p = str(path).lower()
    try:
        if p.endswith((".md", ".txt", ".csv")):
            text = Path(path).read_text(encoding="utf-8", errors="replace")[: limit * 2]
        elif p.endswith(".docx"):
            with zipfile.ZipFile(path) as z:
                root = _xml(z, "word/document.xml")
            text = "\n".join("".join(t.text or "" for t in para.iter(_W + "t")) for para in root.iter(_W + "p"))
        elif p.endswith(".pptx"):
            with zipfile.ZipFile(path) as z:
                slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                                key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
                parts = []
                for n in slides:
                    words = " ".join(t.text or "" for t in _xml(z, n).iter(_A + "t")).strip()
                    if words:
                        parts.append(f"[slide {slides.index(n) + 1}] {words}")
                    if sum(map(len, parts)) > limit:
                        break
            text = "\n".join(parts)
        elif p.endswith(".xlsx"):
            with zipfile.ZipFile(path) as z:
                wb = _xml(z, "xl/workbook.xml")
                sheets = [s.get("name") for s in wb.iter(_X + "sheet")]
                strings = []
                if "xl/sharedStrings.xml" in z.namelist():
                    for si in _xml(z, "xl/sharedStrings.xml").iter(_X + "si"):
                        strings.append("".join(t.text or "" for t in si.iter(_X + "t")))
                        if sum(map(len, strings)) > limit:
                            break
            text = f"Sheets: {', '.join(sheets)}\n" + " | ".join(s for s in strings if s.strip())
        elif p.endswith(".pdf") and shutil.which("pdftotext"):
            r = subprocess.run(["pdftotext", "-l", "3", "-layout", str(path), "-"], capture_output=True, text=True,
                               timeout=60)
            text = r.stdout if r.returncode == 0 else ""
        else:
            text = ""
    except (KeyError, zipfile.BadZipFile, ET.ParseError, OSError, ValueError, subprocess.SubprocessError):
        return ""
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()[:limit]


def _walk(root, include, exclude):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not d.startswith(".")]
        for f in fns:
            if f.startswith((".", "~$")):
                continue
            rel = os.path.relpath(os.path.join(dp, f), root)
            if any(fnmatch.fnmatch(f.lower(), x.lower()) for x in include) and \
                    not any(fnmatch.fnmatch(rel, x) or fnmatch.fnmatch(f, x) for x in exclude):
                yield rel


def _local_mtime(path, tzname):
    try:
        from zoneinfo import ZoneInfo
        z = ZoneInfo(tzname or "UTC")
    except Exception:
        z = None
    return dt.datetime.fromtimestamp(os.path.getmtime(path), z).replace(tzinfo=None).isoformat(timespec="seconds")


def _digest(cfg, key, scfg, store, root, rels_sigs, cur):
    """`digest = true`: one evidence item per top-level folder per day, listing what arrived (for bulk drops such
    as a client's inputs folder, where each file on its own is noise but the delivery is the signal)."""
    groups = {}
    for rel, sig, at, what in rels_sigs:
        top = rel.split(os.sep, 1)[0] if os.sep in rel else "."
        groups.setdefault((at[:10], top), []).append((rel, sig, at, what))
    new = []
    for (day, top), files in sorted(groups.items()):
        files.sort()
        lines = [f"- {what}: {rel}" for rel, _, _, what in files]
        title = f"{len(files)} files in {scfg.get('label') or Path(root).name}/{'' if top == '.' else top}"
        text = (f"{len(files)} files added or updated on {day} in {Path(root).name}"
                f"{'' if top == '.' else '/' + top}:\n" + "\n".join(lines[:80])
                + (f"\n- and {len(lines) - 80} more" if len(lines) > 80 else ""))
        at = max(f[2] for f in files)
        item = {"id": S.make_id(at, key, f"{day}|{top}|" + "|".join(f[1] for f in files)), "source": key,
                "kind": "file", "at": at, "date_source": "file_mtime", "title": title.rstrip("/"), "text": text,
                "meta": {"digest": True, "folder": top, "files": len(files)}}
        if S.write_evidence(store, item):
            new.append(item["id"])
        for rel, sig, _, _ in files:
            cur[rel] = sig
    return new


_MONTHS = {m[:3].lower(): i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"])}


def report_date(first_line, date_format=None, today=None):
    """A status report's own date, read from its header (first) line, or None. ISO (2026-06-03) and
    month-name (3 Jun 2026 / Jun 3, 2026) dates are always read; a numeric DD/MM/YY-style date is read
    only when the source declares `date_format` ("dmy"|"mdy"|"ymd") — taska never guesses an ambiguous
    date. A date after `today` (a sync-clock error) returns None."""
    today = today or dt.date.today().isoformat()
    s = (first_line or "").strip()
    y = mo = d = None
    if (m := re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", s)):                                   # ISO
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    elif (m := re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b", s)):                  # 3 Jun 2026
        d, mo, y = int(m[1]), _MONTHS.get(m[2][:3].lower()), int(m[3])
    elif (m := re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b", s)):                  # Jun 3, 2026
        mo, d, y = _MONTHS.get(m[1][:3].lower()), int(m[2]), int(m[3])
    elif date_format and (m := re.search(r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b", s)):          # numeric + declared format
        a, b, c = int(m[1]), int(m[2]), int(m[3])
        y, mo, d = {"dmy": (c, b, a), "mdy": (c, a, b), "ymd": (a, b, c)}[date_format]
    if not (y and mo and d):
        return None
    if y < 100:
        y += 2000
    try:
        iso = dt.date(y, mo, d).isoformat()
    except ValueError:
        return None
    return iso if iso <= today else None


def sync(cfg, key, scfg, store, cursor):
    root = scfg.get("path")
    if not root or not os.path.isdir(root):
        return cursor, {"source": key, "status": "unavailable", "reason": f"folder not found: {root}"}
    cur = dict(cursor or {})
    since = scfg.get("since")
    new, changed, skipped, bulk = [], [], [], []
    for rel in sorted(_walk(root, scfg.get("include") or DEFAULT_INCLUDE, scfg.get("exclude") or [])):
        ap = os.path.join(root, rel)
        st = os.stat(ap)
        sig = f"{st.st_size}:{int(st.st_mtime)}"
        if cur.get(rel) == sig:
            continue
        at = _local_mtime(ap, cfg.timezone)
        if since and at[:10] < since:
            cur[rel] = sig
            continue
        what = "updated" if rel in cur else "added"
        if scfg.get("digest"):
            bulk.append((rel, sig, at, what))
            continue
        text = excerpt(ap)
        if not text and st.st_size and rel.lower().endswith((".docx", ".pptx", ".xlsx")):
            skipped.append({"file": rel, "reason": "unreadable (corrupt, or the sync client has not finished it)"})
            continue  # retried next sync
        rd = report_date(text.splitlines()[0] if text else "", scfg.get("date_format"))
        ev_at, src = (f"{rd}T00:00:00", "file_content") if rd else (at, "file_mtime")
        head = (f"File {what}: {rel} (modified {at.replace('T', ' ')[:16]}, {max(1, st.st_size // 1024)} KB)."
                + ("" if text else " No text could be read from it."))
        item = {"id": S.make_id(ev_at, key, f"{rel}|{sig}"), "source": key, "kind": "file", "at": ev_at, "date_source": src,
                "date": rd or ev_at[:10], "title": Path(rel).stem, "text": head + ("\n\n" + text if text else ""),
                "meta": {"path": rel, "what": what, "folder": os.path.dirname(rel) or "."}}
        if S.write_evidence(store, item):
            (changed if what == "updated" else new).append(item["id"])
        cur[rel] = sig
    if bulk:
        new += _digest(cfg, key, scfg, store, root, bulk, cur)
    return cur, {"source": key, "status": "ok", "new": new, "changed": changed, "skipped": skipped}
