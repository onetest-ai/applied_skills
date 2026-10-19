"""Write the approved doc-fact-check findings as anchored Word comments (step 8), then set each finding's destination.

    python annotate.py <draft.docx> <out.docx> --approved approved.json --comments major|all --findings findings.json
    python annotate.py <draft.docx> <out.docx> --select major|all --findings findings.json

--approved takes an explicit list (Deep, when the user edited the set); --select takes the comments from
findings.json itself: major = Blocker + Major defects, all = every defect.
With --approved, --comments (required: the run's comment policy) records the reviewer's decisions: a finding
the policy would comment but the reviewer removed gets destination "declined by reviewer"; a finding the reviewer
added that the policy would not comment gets "Word comment (added by reviewer)" once its comment is written.
"""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling imports work under python -I too

import annotate_cli  # noqa: E402
from annotate_cli import (COMMENT_KEYS, _bad, _comment_error, _load_list, _write_json_atomic,  # noqa: E402,F401
                          mark_destinations)
from fact_check_invariants import _last_heading, comment_text  # noqa: E402


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


def annotate(src, dst, findings, author='Fact Checker · Brain'):
    """Returns (written, skipped) id lists."""
    from docx import Document
    from docx.oxml.ns import qn
    doc = Document(src)
    if not hasattr(doc, 'comments'):
        raise SystemExit('python-docx >= 1.2 required for comments')
    have = {c.text.strip() for c in doc.comments}          # comments carry no id: a finding's comment is matched by its text
    written = []
    skipped = []
    for f in findings:
        if comment_text(f).strip() in have:
            written.append(f['id'])   # already written by an earlier run (idempotent)
            continue
        text = comment_text(f)
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


def _dependency_error():
    try:
        import docx  # noqa: F401
    except ImportError:
        from sections import NEEDS_DOCX
        return NEEDS_DOCX
    return None


def main(argv=None) -> int:
    return annotate_cli.run(argv, prog="annotate.py", description=__doc__.split("\n")[0], annotate=annotate,
                            dependency_error=_dependency_error, label="Word comment")


if __name__ == "__main__":
    sys.exit(main())
