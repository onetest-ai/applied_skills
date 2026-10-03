"""Write the approved doc-fact-check findings as anchored Word comments (step 8), then set each finding's destination.

    python annotate.py <draft.docx> <out.docx> --approved approved.json --findings findings.json
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from pathlib import Path


def split_run(run, off):
    from docx.oxml.ns import qn
    t = run._r.findall(qn('w:t'))
    if len(t) != 1 or off <= 0 or off >= len(t[0].text or ''):
        return
    right = copy.deepcopy(run._r)
    txt = t[0].text
    t[0].text = txt[:off]
    t[0].set(qn('xml:space'), 'preserve')
    rt = right.find(qn('w:t'))
    rt.text = txt[off:]
    rt.set(qn('xml:space'), 'preserve')
    run._r.addnext(right)


def isolate(p, quote):
    s = p.text.find(quote)
    e = s + len(quote)
    if s < 0:
        return []
    if sum(len(r.text) for r in p.runs) != len(p.text):
        # hyperlink/field runs are not in p.runs; offsets would drift -> anchor the whole paragraph, log "paragraph-anchored"
        return [r for r in p.runs if r.text]
    for b in (e, s):
        pos = 0
        for r in list(p.runs):
            n = len(r.text)
            if pos < b < pos + n:
                split_run(r, b - pos)
                break
            pos += n
    out = []
    pos = 0
    for r in p.runs:
        n = len(r.text)
        if pos >= s and pos + n <= e and n:
            out.append(r)
        pos += n
    return out


def paragraphs(doc, textbox=False):
    """Every paragraph in true document order, table cells included, each once.

    Body paragraphs only by default; textbox=True yields only text-box
    paragraphs (w:txbxContent), the fallback anchor.
    """
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph
    for p in doc.element.body.iter(qn('w:p')):
        in_box = any(a.tag == qn('w:txbxContent') for a in p.iterancestors())
        if in_box == textbox:
            yield Paragraph(p, doc)


def mark_destinations(findings, written_ids):
    """Call after annotate; pure, returns a new list."""
    out = []
    for f in findings:
        dest = 'Word comment' if f['id'] in written_ids else ('count only' if f.get('verdict') == 'Verified' else 'log only')
        out.append({**f, 'destination': dest})
    return out


def annotate(src, dst, findings, author='Fact Checker · Brain'):
    """Returns (written, skipped) id lists."""
    from docx import Document
    from docx.oxml.ns import qn
    doc = Document(src)
    if not hasattr(doc, 'comments'):
        raise SystemExit('python-docx >= 1.2 required for comments')
    hdr = re.compile(r'^\[[^·\]]+ · [^·\]]+ · ([^\]\s]+)\]')   # only a comment's own header line names its id
    have = {m[1] for c in doc.comments if (m := hdr.match((c.text.strip().splitlines() or [''])[0]))}
    written = []
    skipped = []
    for f in findings:
        if f['id'] in have:
            written.append(f['id'])   # exact id: C1 is not C10, a mention in another comment's body is not a header
            continue
        text = (f"[{f['verdict']} · {f['severity']} · {f['id']}] §{f['section']}\n"
                f"Brain: {f['evidence']}\nFix: {f['fix']}\nSource: {f['source']}")
        before = len(written)
        if f.get('anchor') == 'drawing':
            k = 0
            for p in paragraphs(doc):
                runs = [r for r in p.runs if r._r.findall('.//' + qn('w:drawing'))]
                if runs and (k := k + 1) == f['figure']:
                    doc.add_comment(runs, text=text, author=author, initials='FC')
                    written.append(f['id'])
                    break
        else:
            for box in (False, True):   # body first; text boxes only when the body lacks the quote
                for p in paragraphs(doc, textbox=box):
                    if f['quote'] in p.text:
                        runs = isolate(p, f['quote'])
                        if runs:
                            doc.add_comment(runs, text=text, author=author, initials='FC')
                            written.append(f['id'])
                            if box:
                                print('anchored: textbox', f['id'], file=sys.stderr)
                            break
                if len(written) > before:
                    break
        if len(written) == before:
            skipped.append(f['id'])   # quote/figure not found: logged, no comment
    doc.save(dst)
    return written, skipped


def _load_list(path, what, need_anchor=False):
    """Load a JSON list of finding objects; exit 2 with 'annotate.py: <file>: <reason>' when it is not usable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.exit(_bad(path, f"not valid JSON ({exc})"))
    if not isinstance(data, list):
        sys.exit(_bad(path, f"{what} must be a JSON list of finding objects"))
    for i, f in enumerate(data):
        if not isinstance(f, dict) or not f.get("id") or not isinstance(f["id"], str):
            sys.exit(_bad(path, f"item {i} is not an object with a non-empty id"))
        reason = _comment_error(f) if need_anchor else None
        if reason:
            sys.exit(_bad(path, f"{f['id']}: {reason}"))
    return data


COMMENT_KEYS = ("verdict", "severity", "section", "evidence", "fix", "source")


def _comment_error(f) -> str | None:
    """Why an approved finding cannot become a comment, or None: the comment's keys and its anchor."""
    for key in COMMENT_KEYS:
        v = f.get(key)
        if key == "section":                     # a heading path or a section number: any non-empty value
            if v is None or not str(v).strip() or v in ([], {}):
                return "`section` must be non-empty"
        elif not (isinstance(v, str) and v.strip()):
            return f"`{key}` must be a non-empty string"
    if f.get("anchor") == "drawing":
        fig = f.get("figure")
        if not (type(fig) is int and fig >= 1):
            return 'anchor "drawing" needs an integer figure >= 1'
    elif not f.get("quote"):
        return 'needs a quote or anchor: "drawing"'
    return None


def _bad(path, reason):
    print(f"annotate.py: {path}: {reason}", file=sys.stderr)
    return 2


def _write_json_atomic(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("draft")
    ap.add_argument("out")
    ap.add_argument("--approved", required=True, help="JSON list of the findings the user approved")
    ap.add_argument("--findings", required=True, help="findings.json; rewritten with destination set")
    a = ap.parse_args(argv)
    for p in (a.draft, a.approved, a.findings):
        if not Path(p).is_file():
            print(f"annotate.py: no such file: {p}", file=sys.stderr)
            return 2
    if Path(a.out).resolve() == Path(a.draft).resolve():
        print("annotate.py: refusing to overwrite the original draft", file=sys.stderr)
        return 2
    approved = _load_list(a.approved, "approved", need_anchor=True)
    findings = _load_list(a.findings, "findings")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from sections import NEEDS_DOCX
    try:
        import docx  # noqa: F401
    except ImportError:
        print(NEEDS_DOCX, file=sys.stderr)
        return 2
    written, skipped = annotate(a.draft, a.out, approved)
    _write_json_atomic(a.findings, mark_destinations(findings, written))
    print(json.dumps({"written": written, "skipped": skipped}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
