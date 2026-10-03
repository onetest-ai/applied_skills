"""Split a draft .docx into heading sections and word-bounded batches for /kb:doc-fact-check.

    python sections.py <draft.docx> --out <dir> [--max-words 1500]
    python sections.py --check        (dependency check for step 0)

Writes into <dir>:
- sections.json  one record per heading section, in document order: section_id, section (the
                 heading path joined with " > "), heading_path, level, heading_p_id,
                 paragraphs [{p_id, text, sentences [{s_id, text, risk}]}], tables [{t_id, rows [{r_id, cells, s_id, risk}]}],
                 figures [{figure, p_id, image, media?}], words.
- stats.json    {sections_total, statements_total, statements_risk, estimate_minutes {fast, deep}, source_sha256}:
                 statements are body sentences (s_id p<n>s<k>) plus table rows (s_id = r_id); risk is the
                 list of tags (num, date, absolute, ownership) from RISK_PATTERNS.
- batches.json   the batch index: [{batch, file, words, sections [{section_id, section, part?}]}].
- media/        every image a figure references (flat); each figure gains media = "media/<file>".
- embedded/     one folder per word/embeddings/* object: an OOXML package (.xlsx/.docx/.pptx, a zip) is
                 expanded into its *.xml parts under embedded/<stem>/; anything else (an OLE .bin) is copied
                 there as-is and reported "not readable". word/diagrams/* go to embedded/diagrams/ and
                 word/charts/*.xml to embedded/charts/; a figure with no image but a chart or SmartArt part
                 gains part = "embedded/charts/<file>" (or embedded/diagrams/<file>).
- batch_<k>.json one file per batch: {batch, document, max_words, sections [...]}, each entry a
                 section record (or, for a split section, one part of it with part/parts).

Consecutive sections are grouped up to --max-words. A section is never split unless it alone
exceeds the limit; then it is split at paragraph (and table-row) boundaries. Every paragraph and
table row lands in exactly one batch. Deterministic: the same input gives byte-identical output.

Heading levels come from the "Heading N" styles ("Title" is level 0). Body paragraphs are
numbered p1, p2, ... in document order (empty ones are counted but not recorded); tables t1, t2,
...; rows t<n>r<m>. A figure is a paragraph holding a drawing, numbered as the SKILL's `annotate`
counts them (`figure`), with the paragraph id (or the table-row id) that holds it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
from pathlib import Path

PREAMBLE = "(before first heading)"
SEP = " > "
_HEADING = re.compile(r"^Heading (\d+)$")

# Risk tags (spec §13.4). The ONLY definition of these patterns; SKILL.md and the spec refer to them.
_NUM_WORDS = r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozen|hundred|thousand|million|billion"
RISK_PATTERNS = {
    "num": re.compile(r"(?:[~≈<>]\s*)?\b\d[\d,.]*\s*(?:%|k|m|bn|million|billion|thousand)?(?!\w)|\b(?:" + _NUM_WORDS + r")\b", re.I),
    "date": re.compile(r"\b(?:jan(?:uary)?|feb(?:ruary)?|march|apr(?:il)?|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
                       r"|\bQ[1-4]\b|\bFY\d{2,4}\b|\b(?:19|20)\d{2}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
                       r"|\b(?:today|currently|now|as of)\b", re.I),
    "absolute": re.compile(r"\b(?:never|always|all|every|none|no one|nothing|only|sole|solely|entire|entirely|fully|completely|consistent across)\b", re.I),
    "ownership": re.compile(r"\b(?:owns?|owned|owner|operates?|operated by|responsible|accountable|attend(?:s|ed)?|decided|approved|leads?|maintains?|managed by)\b", re.I),
}
_NUMBERING = re.compile(r"§\s*\d[\d.]*|\b(?:section|figure|fig\.|table|phase|step|version|v)\s*\d[\d.]*", re.I)
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9\"“(])|\n+")


def tag_risk(text: str) -> list[str]:
    t = _NUMBERING.sub(" ", text)
    return [tag for tag, rx in RISK_PATTERNS.items() if rx.search(t)]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s and s.strip()]


def estimate_minutes(statements_total: int, statements_risk: int) -> dict:
    """Minutes for the two-stage pipeline (spec §15): one extract wave + verify waves of 10 chunks of 8."""
    def run(claims: int) -> int:
        return 5 + 10 * math.ceil(math.ceil(claims / 8) / 10)
    return {"fast": run(statements_risk), "deep": run(statements_total)}


def _q(tag: str) -> str:
    from docx.oxml.ns import qn
    return qn(tag)


def _words(text: str) -> int:
    return len(text.split())


def _level(paragraph) -> int | None:
    name = (paragraph.style.name if paragraph.style is not None else "") or ""
    if name == "Title":
        return 0
    m = _HEADING.match(name)
    return int(m.group(1)) if m else None


def _figure_numbers(doc) -> dict:
    """Map each paragraph element holding a drawing to its 1-based figure number.

    Same rule as `annotate` in SKILL.md: every w:p in the body outside text boxes, in document
    order, counted once when one of its runs holds a w:drawing."""
    out, k = {}, 0
    for p in doc.element.body.iter(_q("w:p")):
        if any(a.tag == _q("w:txbxContent") for a in p.iterancestors()):
            continue
        if any(r.findall(".//" + _q("w:drawing")) for r in p.findall(_q("w:r"))):
            k += 1
            out[p] = k
    return out


def _drawing_part(doc, p_el) -> str | None:
    """The package part a chart (c:chart r:id) or SmartArt (dgm:relIds r:dm) drawing points to, or None."""
    refs = [el.get(_q("r:id")) for el in p_el.iter(_q("c:chart"))]
    refs += [el.get(_q("r:dm")) for el in p_el.iter(_q("dgm:relIds"))]
    for rid in refs:
        part = doc.part.related_parts.get(rid) if rid else None
        if part is not None:
            return str(part.partname).lstrip("/")
    return None


def _image_name(doc, p_el) -> str | None:
    for blip in p_el.iter(_q("a:blip")):
        rid = blip.get(_q("r:embed")) or blip.get(_q("r:link"))
        part = doc.part.related_parts.get(rid) if rid else None
        if part is not None:
            return str(part.partname).lstrip("/")
    return None


def _blocks(doc):
    """Yield body blocks in document order: ('p', Paragraph) and ('t', Table), sdt content included."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    def walk(parent):
        for child in parent.iterchildren():
            if child.tag == _q("w:p"):
                yield "p", Paragraph(child, doc)
            elif child.tag == _q("w:tbl"):
                yield "t", Table(child, doc)
            elif child.tag == _q("w:sdt"):
                content = child.find(_q("w:sdtContent"))
                if content is not None:
                    yield from walk(content)

    yield from walk(doc.element.body)


def _row_cells(table, tr) -> list[str]:
    from docx.table import _Cell
    return [_Cell(tc, table).text.strip() for tc in tr.findall(_q("w:tc"))]


def extract_sections(docx_path) -> list[dict]:
    from docx import Document
    doc = Document(str(docx_path))
    figs = _figure_numbers(doc)
    sections: list[dict] = []
    stack: list[tuple[int, str]] = []
    cur: dict | None = None
    p_n = t_n = 0

    def open_section(path, level, p_id):
        rec = {"section_id": f"s{len(sections) + 1:02d}", "section": SEP.join(path),
               "heading_path": list(path), "level": level, "heading_p_id": p_id,
               "blocks": [], "words": sum(_words(h) for h in path[-1:]) if p_id else 0}
        sections.append(rec)
        return rec

    def current():
        nonlocal cur
        if cur is None:
            cur = open_section([PREAMBLE], None, None)
        return cur

    def figure(p_el, holder_id):
        if p_el in figs:
            rec = {"figure": figs[p_el], "p_id": holder_id, "image": _image_name(doc, p_el)}
            if rec["image"] is None and (part := _drawing_part(doc, p_el)):
                rec["drawing_part"] = part          # mapped to its extracted path by extract_embedded
            current()["blocks"].append(("f", rec, 0))

    for kind, obj in _blocks(doc):
        if kind == "p":
            p_n += 1
            p_id, text = f"p{p_n}", obj.text.strip()
            level = _level(obj)
            if level is not None and text:
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, text))
                cur = open_section([h for _, h in stack], level, p_id)
                figure(obj._p, p_id)
                continue
            if text:
                sents = [{"s_id": f"{p_id}s{n}", "text": s, "risk": tag_risk(s)}
                         for n, s in enumerate(split_sentences(text), 1)]
                current()["blocks"].append(("p", {"p_id": p_id, "text": text, "sentences": sents}, _words(text)))
            figure(obj._p, p_id)
        else:
            t_n += 1
            t_id = f"t{t_n}"
            for r_n, tr in enumerate(obj._tbl.tr_lst, 1):
                r_id = f"{t_id}r{r_n}"
                cells = _row_cells(obj, tr)
                current()["blocks"].append(("r", {"t_id": t_id, "r_id": r_id, "cells": cells, "s_id": r_id,
                                                  "risk": tag_risk(" | ".join(cells))},
                                            sum(_words(c) for c in cells)))
                for p_el in tr.iter(_q("w:p")):
                    figure(p_el, r_id)
    for s in sections:
        s["words"] += sum(w for _, _, w in s["blocks"])
    return sections


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
            tables[-1]["rows"].append({k: rec[k] for k in ("r_id", "cells", "s_id", "risk")})
    out = {k: sec[k] for k in ("section_id", "section", "heading_path", "level", "heading_p_id")}
    if part:
        out["part"], out["parts"] = part
    out.update(paragraphs=paragraphs, tables=tables, figures=figures, words=words)
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


def extract_media(docx_path, out_dir: Path, sections: list[dict]) -> list[str]:
    """Write every image a figure references to <out>/media/; orphaned package media is never read."""
    import zipfile
    names = sorted({rec["image"] for s in sections for k, rec, _ in s["blocks"] if k == "f" and rec.get("image")})
    if not names:
        return []
    media = Path(out_dir) / "media"
    written = []
    with zipfile.ZipFile(docx_path) as z:
        have = set(z.namelist())
        for name in names:                       # r:link (external) images have no part: skipped
            if name in have:
                media.mkdir(parents=True, exist_ok=True)
                (media / Path(name).name).write_bytes(z.read(name))
                written.append(name)
    for s in sections:                           # link each figure to its extracted file
        for k, rec, _ in s["blocks"]:
            if k == "f" and rec.get("image") in written:
                rec["media"] = f"media/{Path(rec['image']).name}"
    return written


class Refusal(Exception):
    """sections.py will not touch the out dir (exit 2, nothing written)."""


EMBED_DIRS = {"word/diagrams/": "diagrams", "word/charts/": "charts"}


def embedded_members(docx_path) -> tuple[list[str], list[str], list[str]]:
    """Package parts: word/embeddings/* and word/diagrams/* (files), word/charts/*.xml (no _rels), sorted."""
    import zipfile
    with zipfile.ZipFile(docx_path) as z:
        n = [x for x in z.namelist() if not x.endswith("/")]
    return (sorted(x for x in n if x.startswith("word/embeddings/")),
            sorted(x for x in n if x.startswith("word/diagrams/")),
            sorted(x for x in n if x.startswith("word/charts/") and "/" not in x[len("word/charts/"):]
                   and x.endswith(".xml")))


def _safe_rel(name: str) -> Path | None:
    """A zip part name as a relative path that cannot leave its folder, or None."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        return None
    return Path(*parts)


def _object_dirs(names: list[str]) -> dict[str, str]:
    """embedded/<stem>/ per object; a stem already taken (or "charts"/"diagrams") gets <stem>-<ext>."""
    taken, out = set(EMBED_DIRS.values()), {}
    for name in names:
        p = Path(name)
        folder = p.stem if p.stem not in taken else f"{p.stem}-{p.suffix.lstrip('.') or 'bin'}"
        while folder in taken:
            folder += "_"
        taken.add(folder)
        out[name] = folder
    return out


def extract_embedded(docx_path, out_dir: Path, sections: list[dict] | None = None) -> list[dict]:
    """Write embedded objects, diagram and chart parts under <out>/embedded/ and return one record per object
    ({member, path, xml_parts} for an expanded OOXML package, {member, path, readable: False} otherwise).
    Figures pointing at a chart/diagram part gain part = its extracted path."""
    import io
    import zipfile
    emb, dia, charts = embedded_members(docx_path)
    objects: list[dict] = []
    extracted: dict[str, str] = {}
    if not (emb or dia or charts):
        return objects
    dest = Path(out_dir) / "embedded"
    with zipfile.ZipFile(docx_path) as z:
        for name, folder in _object_dirs(emb).items():
            data, obj = z.read(name), dest / folder
            obj.mkdir(parents=True, exist_ok=True)
            if zipfile.is_zipfile(io.BytesIO(data)):
                count = 0
                with zipfile.ZipFile(io.BytesIO(data)) as inner:
                    for part in sorted(inner.namelist()):
                        rel = _safe_rel(part)
                        if rel is None or rel.suffix.lower() != ".xml":
                            continue
                        target = obj / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(inner.read(part))
                        count += 1
                objects.append({"member": name, "path": f"embedded/{folder}/", "xml_parts": count})
            else:
                (obj / Path(name).name).write_bytes(data)
                objects.append({"member": name, "path": f"embedded/{folder}/{Path(name).name}", "readable": False})
        for prefix, folder in EMBED_DIRS.items():
            for name in (dia if folder == "diagrams" else charts):
                rel = _safe_rel(name[len(prefix):])
                if rel is None:
                    continue
                target = dest / folder / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(name))
                extracted[name] = f"embedded/{folder}/{rel.as_posix()}"
    for s in sections or []:
        for k, rec, _ in s["blocks"]:
            if k == "f" and rec.get("drawing_part") in extracted:
                rec["part"] = extracted[rec["drawing_part"]]
    return objects


def _clear_stale(out_dir: Path) -> None:
    """Remove media/ and embedded/ from an earlier run of this script; refuse anything else."""
    for sub in ("media", "embedded"):
        path = out_dir / sub
        if path.is_symlink():
            raise Refusal(f"refusing to clear {sub}/: it is a symlink")
        if path.exists() and not (out_dir / "sections.json").is_file():
            raise Refusal(f"refusing to clear {sub}/ in a folder sections.py did not create")
    for sub in ("media", "embedded"):
        shutil.rmtree(out_dir / sub, ignore_errors=True)


def write_outputs(docx_path, out_dir, max_words: int = 1500) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (sections, batch index, embedded-object records from extract_embedded)."""
    out_dir = Path(out_dir)
    _clear_stale(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sections = extract_sections(docx_path)
    batches = build_batches(sections, max_words)
    for stale in out_dir.glob("batch_*.json"):
        stale.unlink()
    extract_media(docx_path, out_dir, sections)
    objects = extract_embedded(docx_path, out_dir, sections)
    for s in sections:                           # the package part name is internal; `part` is the extracted path
        for k, rec, _ in s["blocks"]:
            if k == "f":
                rec.pop("drawing_part", None)
    _dump(out_dir / "sections.json", [_render(s, s["blocks"], s["words"]) for s in sections])
    statements = [s for sec in sections for kind, rec, _ in sec["blocks"]
                  for s in (rec["sentences"] if kind == "p" else [rec] if kind == "r" else [])]
    total, risky = len(statements), sum(1 for s in statements if s["risk"])
    _dump(out_dir / "stats.json", {"sections_total": len(sections), "statements_total": total,
                                   "statements_risk": risky, "estimate_minutes": estimate_minutes(total, risky),
                                   "source_sha256": hashlib.sha256(Path(docx_path).read_bytes()).hexdigest()})
    index = []
    for k, units in enumerate(batches, 1):
        name = f"batch_{k}.json"
        _dump(out_dir / name, {"batch": k, "document": Path(docx_path).name,
                               "max_words": max_words, "sections": units})
        index.append({"batch": k, "file": name, "words": sum(u["words"] for u in units),
                      "sections": [{"section_id": u["section_id"], "section": u["section"],
                                    **({"part": u["part"]} if "part" in u else {})} for u in units]})
    _dump(out_dir / "batches.json", index)
    return sections, index, objects


NEEDS_DOCX = ("doc-fact-check needs Python with python-docx ≥ 1.2 in this environment; "
              "none is available here. Run it in Claude Code.")


def check_dependencies() -> int:
    """Step 0: print the versions the skill needs, or the stop message (exit 2)."""
    try:
        import docx
        ok = hasattr(docx.Document(), "comments")          # the comments API exists only from 1.2
        ver = getattr(docx, "__version__", "?")
    except Exception:
        ok, ver = False, None
    if not ok:
        print(NEEDS_DOCX, file=sys.stderr)
        return 2
    print(f"python {sys.version.split()[0]} python-docx {ver}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("docx", nargs="?", help="the draft .docx")
    ap.add_argument("--out", help="directory for sections.json, batches.json, batch_<k>.json, media/ and embedded/")
    ap.add_argument("--check", action="store_true", help="print the Python and python-docx versions, then exit")
    ap.add_argument("--max-words", type=int, default=1500, help="word budget per batch (default 1500)")
    a = ap.parse_args(argv)
    if a.check:
        return check_dependencies()
    if not a.docx or not a.out:
        ap.error("the draft .docx and --out are required (or use --check)")
    src = Path(a.docx)
    if not src.is_file():
        print(f"sections.py: no such file: {src}", file=sys.stderr)
        return 2
    if a.max_words < 1:
        print("sections.py: --max-words must be at least 1", file=sys.stderr)
        return 2
    try:
        sections, index, objects = write_outputs(src, a.out, a.max_words)
    except Refusal as e:
        print(f"sections.py: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # not a .docx, corrupt package
        print(f"sections.py: cannot read {src}: {e}", file=sys.stderr)
        return 2
    out = Path(a.out)
    st = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    figs = [f for s in json.loads((out / "sections.json").read_text(encoding="utf-8")) for f in s["figures"]]
    _, dia, charts = embedded_members(src)
    part_dirs = {"diagrams": dia, "charts": charts}
    listed = lambda folder: f"{len(part_dirs[folder])}" + (" (" + ", ".join(
        f"embedded/{folder}/{x.split('/', 2)[2]}" for x in part_dirs[folder]) + ")" if part_dirs[folder] else "")
    obj_line = lambda o: (f"{o['path']}: {o['xml_parts']} XML parts" if "xml_parts" in o
                          else f"{o['path']}: not readable")
    print(f"{len(sections)} sections, {len(index)} batches -> {out}")
    print(f"statements {st['statements_total']}, high-risk {st['statements_risk']}, "
          f"estimate fast ~{st['estimate_minutes']['fast']} min, deep ~{st['estimate_minutes']['deep']} min")
    print(f"figures: {len(figs)}" + (" (" + "; ".join(
        f"figure {f['figure']} in {f['p_id']} -> {f.get('media') or f.get('part') or 'not extracted'}" for f in figs) + ")"
        if figs else ""))
    print(f"embedded objects: {len(objects)}" + (" (" + "; ".join(map(obj_line, objects)) + ")" if objects else "")
          + f", diagrams: {listed('diagrams')}, charts: {listed('charts')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
