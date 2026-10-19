"""Write the approved deck-fact-check findings as native PowerPoint comments (step 8), then set each finding's
destination.

    python pptx_annotate.py <draft.pptx> <out.pptx> --select major|all --findings findings.json
    python pptx_annotate.py <draft.pptx> <out.pptx> --approved approved.json --comments major|all --findings findings.json

Comments use the legacy format every PowerPoint version reads (ppt/comments/commentN.xml, ppt/commentAuthors.xml),
written at package level into the copy; the draft is never opened for writing. A comment pins to one point: the
place its statement renders on the slide (from the reader's sections.json, `--sections`), else the top-left
corner of the shape that holds the claim (its p_id s<slide>-sh<shape id>-…). A figure claim (anchor "drawing") pins
inside the element it reads (p_id s<slide>-sh<element id>), 1/8 in from its top-left corner, and its header names
the element (from sections.json); a figure claim naming no element pins to its slide's top-left corner. The text is
the shared comment_text (header [Verdict · Severity · id]): the quoted statement and the fix, `--fields` adds the
Brain and Source lines. Comments already in the deck (a reviewer's) are kept; an id
whose comment is already there is not written twice. The CLI, the destinations ("Deck comment") and the checks
are annotate_cli.py's, shared with doc-fact-check.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling imports work under python -I too

import annotate_cli  # noqa: E402
from fact_check_invariants import (AUTHORS_REL, COMMENTS_REL, EMU_PER_POS, HEADER, P_NS, PKG_REL_NS,  # noqa: E402
                                   MAX_COMMENT_WORDS, _rels, comment_text, comment_words, pptx_boxes,
                                   pptx_slide_parts, read_pptx_comments)

AUTHOR, INITIALS = "Fact Checker · Brain", "FC"
CT_COMMENTS = "application/vnd.openxmlformats-officedocument.presentationml.comments+xml"
CT_AUTHORS = "application/vnd.openxmlformats-officedocument.presentationml.commentAuthors+xml"
_PID = re.compile(r"^s(\d+)(?:-sh(\d+))?")
NEEDS = ("deck-fact-check needs Python with python-pptx in this environment to place comments; "
         "none is available here. Run it in Claude Code.")


def to_pos(emu) -> int:
    """Slide EMU -> legacy comment position units (576 per inch)."""
    return round(emu / EMU_PER_POS)


FAN = 72                          # pins that share a point fan out by 1/8 in (576 units per inch)
INSET = 72                        # a figure pin sits 1/8 in inside its element's top-left corner
_GENERIC = re.compile(r"^(?:(?:picture|chart|graphic|diagram|content|object|image|rectangle|shape|oval|freeform)"
                      r"(?: placeholder)?\s*\d*|google shape;.*)$", re.I)          # default names say nothing


def statement_positions(sections_path) -> dict[str, dict]:
    """p_id / r_id -> {x, y}: where pptx_sections.py found each statement on the rendered slide (slide EMU)."""
    if not sections_path:
        return {}
    out = {}
    for s in json.loads(Path(sections_path).read_text(encoding="utf-8")):
        for p in s.get("paragraphs", []):
            if p.get("at"):
                out[p["p_id"]] = p["at"]
        for t in s.get("tables", []):
            for r in t.get("rows", []):
                if r.get("at"):
                    out[r["r_id"]] = r["at"]
    return out


def element_labels(sections_path, slide_size: tuple[int, int]) -> dict[str, str]:
    """figure element p_id (s<N>-sh<id>) -> how a comment header names it: 'picture "Callout"', or, for a generic
    name ("Picture 7", "Chart 3"), its kind and place on the slide ('chart, bottom left')."""
    if not sections_path:
        return {}
    w, h = slide_size
    out = {}
    for s in json.loads(Path(sections_path).read_text(encoding="utf-8")):
        for fig in s.get("figures", []):
            for e in fig.get("elements") or []:
                name = " ".join(str(e.get("name") or "").split())
                if name and not _GENERIC.match(name):
                    label = f'{e["kind"]} "{name}"'
                else:
                    b = e["box"]
                    cx, cy = (b["x"] + b["w"] / 2) / w, (b["y"] + b["h"] / 2) / h
                    row = "top" if cy < 1 / 3 else "middle" if cy < 2 / 3 else "bottom"
                    col = "left" if cx < 1 / 3 else "centre" if cx < 2 / 3 else "right"
                    label = f"{e['kind']}, {row} {col}"
                out[f"{fig['p_id']}-sh{e['shape_id']}"] = label
    return out


def pin(f: dict, boxes: dict, at: dict | None = None) -> tuple[int, int, int, int] | None:
    """(slide, x, y, width) for a finding's comment, all in comment units, or None when its shape is not in the
    deck. A text claim pins where its statement renders (`at`, kept only inside its shape's box), else at its shape's
    top-left corner. A figure claim pins to its slide's top-left corner (width: the fan-out room of one inch)."""
    if f.get("anchor") == "drawing":
        n = f.get("figure")
        m = _PID.match(str(f.get("p_id") or ""))
        if m and m.group(2) is not None and int(m.group(1)) == n and (box := boxes.get((n, int(m.group(2))))):
            inset = min(INSET, to_pos(box["w"]) // 2, to_pos(box["h"]) // 2)
            return (n, to_pos(box["x"]) + inset, to_pos(box["y"]) + inset, max(to_pos(box["w"]) - 2 * inset, 1))
        return (n, 0, 0, 576) if isinstance(n, int) and n >= 1 else None
    m = _PID.match(str(f.get("p_id") or ""))
    if not m:
        return None
    slide = int(m.group(1))
    if m.group(2) is None:
        return (slide, 0, 0, 576)
    box = boxes.get((slide, int(m.group(2))))
    if not box:
        return None
    a = (at or {}).get(str(f.get("p_id")))
    if a and box["x"] <= a["x"] <= box["x"] + box["w"] and box["y"] <= a["y"] <= box["y"] + box["h"]:
        return (slide, to_pos(a["x"]), to_pos(a["y"]), to_pos(box["x"] + box["w"] - a["x"]))
    return (slide, to_pos(box["x"]), to_pos(box["y"]), to_pos(box["w"]))


def _add_override(types_xml: str, part: str, ctype: str) -> str:
    if f'PartName="/{part}"' in types_xml:
        return types_xml
    return types_xml.replace("</Types>", f'<Override PartName="/{part}" ContentType="{ctype}"/></Types>')


def _add_rel(rels_xml: str | None, rtype: str, target: str) -> str:
    if rels_xml is None:
        rels_xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    f'<Relationships xmlns="{PKG_REL_NS}"></Relationships>')
    used = {int(x) for x in re.findall(r'Id="rId(\d+)"', rels_xml)}
    rid = f"rId{max(used, default=0) + 1}"
    return rels_xml.replace("</Relationships>", f'<Relationship Id="{rid}" Type="{rtype}" Target="{target}"/></Relationships>')


def _authors(xml: str | None) -> tuple[str, str, int]:
    """(authors xml with our author present, our author id, our last idx)."""
    if xml is None:
        xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               f'<p:cmAuthorLst xmlns:p="{P_NS}"></p:cmAuthorLst>')
    for m in re.finditer(r"<p:cmAuthor\b[^>]*>", xml):
        if re.search(r'\bname=' + re.escape(quoteattr(AUTHOR)), m.group(0)):
            aid = re.search(r'\bid="(\d+)"', m.group(0)).group(1)
            last = int(re.search(r'\blastIdx="(\d+)"', m.group(0)).group(1))
            return xml, aid, last
    ids = [int(x) for x in re.findall(r'<p:cmAuthor\b[^>]*\bid="(\d+)"', xml)]
    aid = str(max(ids, default=-1) + 1)
    tag = (f'<p:cmAuthor id="{aid}" name={quoteattr(AUTHOR)} initials="{INITIALS}" lastIdx="0" '
           f'clrIdx="{aid}"/>')
    return xml.replace("</p:cmAuthorLst>", tag + "</p:cmAuthorLst>"), aid, 0


def annotate(src, dst, findings, sections=None, fields=None) -> tuple[list[str], list[str]]:
    """Write one comment per finding into a copy of src at dst. Returns (written, skipped) id lists. `sections` is
    the reader's sections.json: with it, each comment is pinned where its statement renders and a figure comment
    names its element. `fields` are the comment lines after the header (default: the quote and the fix)."""
    src, dst = Path(src), Path(dst)
    have = {c["text"].strip() for c in read_pptx_comments(src)}   # comments carry no id: matched by their text
    boxes = pptx_boxes(src)
    at = statement_positions(sections)
    parts = pptx_slide_parts(src)
    from pptx import Presentation
    prs = Presentation(str(src))
    labels = element_labels(sections, (prs.slide_width or 9144000, prs.slide_height or 6858000))
    written, skipped, new, fan = [], [], {}, {}   # new: slide number -> [(x, y, text)]
    for f in findings:
        if comment_text(f, fields).strip() in have or comment_text(f).strip() in have:
            written.append(f["id"])
            continue
        p = pin(f, boxes, at)
        if p is None or not 1 <= p[0] <= len(parts):
            skipped.append(f["id"])             # its shape is not in the deck: logged, no comment
            continue
        slide, x, y, w = p
        k = fan.get((slide, x, y), 0)            # several claims in one shape: fan out, staying inside its box
        fan[(slide, x, y)] = k + 1
        if f.get("anchor") == "drawing" and str(f.get("p_id")) in labels:
            f = {**f, "element": labels[str(f["p_id"])]}
        text = comment_text(f, fields)
        if fields and comment_words(text, True) > MAX_COMMENT_WORDS:   # the extra lines do not fit: statement and fix
            text = comment_text(f)
            print(f"{f['id']}: Brain/Source lines left out of the comment (over {MAX_COMMENT_WORDS} words); "
                  "they are in the findings page", file=sys.stderr)
        new.setdefault(slide, []).append((x + min(FAN * k, max(w - 1, 0)), y, text))
        written.append(f["id"])
    tmp = dst.with_name(dst.name + ".tmp")
    if not new:
        shutil.copyfile(src, tmp)
        tmp.replace(dst)
        return written, skipped
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000")
    with zipfile.ZipFile(src) as zin:
        files = {i.filename: zin.read(i.filename) for i in zin.infolist()}
        infos = {i.filename: i for i in zin.infolist()}
        authors_part = next((t for typ, t in _rels(zin, "ppt/presentation.xml").values() if typ == AUTHORS_REL), None)
        slide_comments = {n: next((t for typ, t in _rels(zin, part).values() if typ == COMMENTS_REL), None)
                          for n, part in enumerate(parts, 1)}
    text = lambda name: files[name].decode("utf-8") if name in files else None
    types = text("[Content_Types].xml")
    if authors_part is None:
        authors_part = "ppt/commentAuthors.xml"
        files["ppt/_rels/presentation.xml.rels"] = _add_rel(text("ppt/_rels/presentation.xml.rels"), AUTHORS_REL,
                                                             "commentAuthors.xml").encode("utf-8")
        types = _add_override(types, authors_part, CT_AUTHORS)
    authors, aid, idx = _authors(text(authors_part))
    taken = {int(m) for name in files for m in re.findall(r"^ppt/comments/comment(\d+)\.xml$", name)}
    for n in sorted(new):
        part = slide_comments[n]
        if part is None:
            k = max(taken, default=0) + 1
            taken.add(k)
            part = f"ppt/comments/comment{k}.xml"
            slide = parts[n - 1]
            rels_name = f"{slide.rsplit('/', 1)[0]}/_rels/{slide.rsplit('/', 1)[1]}.rels"
            files[rels_name] = _add_rel(text(rels_name), COMMENTS_REL, f"../comments/comment{k}.xml").encode("utf-8")
            types = _add_override(types, part, CT_COMMENTS)
            body = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    f'<p:cmLst xmlns:p="{P_NS}"></p:cmLst>')
        else:
            body = text(part)
        cms = ""
        for x, y, t in new[n]:
            idx += 1
            cms += (f'<p:cm authorId="{aid}" dt="{stamp}" idx="{idx}"><p:pos x="{x}" y="{y}"/>'
                    f'<p:text>{escape(t)}</p:text></p:cm>')
        files[part] = body.replace("</p:cmLst>", cms + "</p:cmLst>").encode("utf-8")
    authors = re.sub(r'(<p:cmAuthor\b[^>]*\bid="' + aid + r'"[^>]*\blastIdx=")\d+(")', rf"\g<1>{idx}\g<2>", authors)
    files[authors_part] = authors.encode("utf-8")
    files["[Content_Types].xml"] = types.encode("utf-8")
    order = ["[Content_Types].xml"] + [n for n in files if n != "[Content_Types].xml"]
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in order:
            info = infos.get(name)
            if info is not None:
                zout.writestr(info, files[name], compress_type=zipfile.ZIP_DEFLATED)
            else:
                zout.writestr(name, files[name])
    tmp.replace(dst)
    return written, skipped


def _dependency_error():
    try:
        import pptx  # noqa: F401
    except ImportError:
        return NEEDS
    return None


def main(argv=None) -> int:
    return annotate_cli.run(argv, prog="pptx_annotate.py", description=__doc__.split("\n")[0], annotate=annotate,
                            dependency_error=_dependency_error, label="Deck comment")


if __name__ == "__main__":
    sys.exit(main())
