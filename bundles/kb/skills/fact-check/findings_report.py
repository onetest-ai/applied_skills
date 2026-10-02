#!/usr/bin/env python3
"""Render the fact-check findings table as ONE self-contained local HTML file.

    python findings_report.py findings.json --out findings.html --document draft.docx --brain-version v1

Standard library only, no network resources (no CDN fonts/scripts): the page must render
identically offline and must never be published as an Artifact or to any hosted service —
it is a file next to the annotated document. Every claim is a ledger row, whatever its verdict.
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import sys
from pathlib import Path

VERDICTS = ["Incorrect", "Misleading", "Outdated", "Controversial", "No Evidence", "Verified"]
MAJOR_SEVERITIES = ("Blocker", "Major")


def _e(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def _slug(verdict: str) -> str:
    return str(verdict).lower().replace(" ", "-")


def _has_comment(f: dict) -> bool:
    return "comment" in str(f.get("destination", "")).lower()


def _is_major(f: dict) -> bool:
    return f.get("severity") in MAJOR_SEVERITIES


def _is_figure(f: dict) -> bool:
    return f.get("type") == "figure" or str(f.get("id", "")).startswith("I")


def _tags(f: dict) -> str:
    tags = [_slug(f.get("verdict"))]
    if f.get("verdict") != "Verified":
        tags.append("finding")
    if _is_major(f):
        tags.append("major")
    if _has_comment(f):
        tags.append("in-document")
    if _is_figure(f):
        tags.append("figure")
    return " ".join(tags)


def _safe_link(url) -> str | None:
    url = str(url or "").strip()
    return url if url.lower().startswith(("http://", "https://")) else None


def _sources(f: dict) -> str:
    out = []
    for s in f.get("sources") or []:
        if isinstance(s, str):
            out.append(f'<span class="src">{_e(s)}</span>')
            continue
        name, link = _e(s.get("name") or s.get("file") or s.get("link")), _safe_link(s.get("link"))
        cap = f'<span class="cap">{_e(s.get("folder"))}</span>' if s.get("folder") else ""
        out.append(f'<span class="src"><a href="{_e(link)}">{name}</a>{cap}</span>' if link
                   else f'<span class="src">{name}{cap}</span>')
    return "".join(out)


CSS = """
:root{--bg:#eef1f3;--surface:#fff;--surface-2:#f6f8f9;--ink:#16212b;--ink-2:#44525e;--muted:#687784;--line:#d7dde2;
--term:#1d4fa0;--term-soft:#e3ebf7;--s-verified:#1d7a4d;--s-incorrect:#b3261e;--s-misleading:#9a5a00;
--s-controversial:#6a3fb2;--s-outdated:#56657a;--s-no-evidence:#6f7c87;
--display:system-ui,-apple-system,sans-serif;--body:system-ui,-apple-system,sans-serif;--mono:ui-monospace,Menlo,Consolas,monospace}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f151b;--surface:#161f27;--surface-2:#1b262f;
--ink:#e6ecf0;--ink-2:#b3c0ca;--muted:#8a99a5;--line:#27343e;--term:#7fa8ec;--term-soft:#1a2a44;
--s-verified:#4cc38a;--s-incorrect:#f0766c;--s-misleading:#e0a64b;--s-controversial:#b095f0;--s-outdated:#9aa8ba;--s-no-evidence:#95a2ad}}
:root[data-theme=dark]{--bg:#0f151b;--surface:#161f27;--surface-2:#1b262f;--ink:#e6ecf0;--ink-2:#b3c0ca;--muted:#8a99a5;
--line:#27343e;--term:#7fa8ec;--term-soft:#1a2a44;--s-verified:#4cc38a;--s-incorrect:#f0766c;--s-misleading:#e0a64b;
--s-controversial:#b095f0;--s-outdated:#9aa8ba;--s-no-evidence:#95a2ad}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--body);font-size:15px;line-height:1.5}
.wrap{max-width:1120px;margin:0 auto;padding:36px 20px 56px;display:flex;flex-direction:column;gap:36px}
.eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.08em;color:var(--term);background:var(--term-soft);display:inline-block;padding:3px 8px;border-radius:2px}
h1{font-family:var(--display);font-weight:700;font-size:clamp(26px,4vw,38px);line-height:1.1;letter-spacing:-.01em;margin:12px 0 10px}
h2{font-family:var(--display);font-weight:600;font-size:20px;margin:0 0 14px}
.lede{color:var(--ink-2);max-width:68ch;margin:0}
.meta{display:flex;flex-wrap:wrap;gap:6px 18px;margin-top:14px;font-family:var(--mono);font-size:12.5px;color:var(--muted)}
.meta b{color:var(--ink-2);font-weight:500}
.tiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:4px;padding:16px 18px}
.tile .k{font-family:var(--mono);font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.tile .v{font-family:var(--display);font-size:34px;font-weight:700;line-height:1.1;font-variant-numeric:tabular-nums;margin-top:4px}
.tile .n{font-size:13px;color:var(--ink-2)}.tile.alert .v{color:var(--s-incorrect)}
.band{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:28px;align-items:start}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:4px;padding:20px 22px;min-width:0}
.vbar{display:grid;grid-template-columns:110px 1fr 34px;gap:12px;align-items:center;padding:6px 0}
.vlabel{font-size:13.5px;color:var(--ink-2)}
.vtrack{height:14px;background:var(--surface-2);border:1px solid var(--line);border-radius:2px;overflow:hidden}
.vfill{height:100%;border-radius:0 3px 3px 0}
.vnum{font-family:var(--mono);font-size:13.5px;text-align:right;font-variant-numeric:tabular-nums}
.note{font-size:13px;color:var(--muted);margin:14px 0 0}
.majors{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:14px}
.majors li{display:grid;grid-template-columns:44px 112px 1fr;gap:10px;align-items:baseline}
.mid{font-family:var(--mono);font-weight:600;color:var(--term)}.majors .pill{justify-self:start}
.mtext{font-size:14px;color:var(--ink-2)}
.pill{display:inline-block;font-family:var(--mono);font-size:11px;font-weight:600;letter-spacing:.03em;padding:2px 7px;border-radius:2px;border:1px solid currentColor;white-space:nowrap}
.s-verified{color:var(--s-verified)}.s-incorrect{color:var(--s-incorrect)}.s-misleading{color:var(--s-misleading)}
.s-controversial{color:var(--s-controversial)}.s-outdated{color:var(--s-outdated)}.s-no-evidence{color:var(--s-no-evidence)}
.vfill.s-verified{background:var(--s-verified)}.vfill.s-incorrect{background:var(--s-incorrect)}.vfill.s-misleading{background:var(--s-misleading)}
.vfill.s-controversial{background:var(--s-controversial)}.vfill.s-outdated{background:var(--s-outdated)}.vfill.s-no-evidence{background:var(--s-no-evidence)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;align-items:center}
.chip{font-family:var(--body);font-size:13px;padding:6px 12px;border:1px solid var(--line);background:var(--surface);color:var(--ink-2);border-radius:999px;cursor:pointer}
.chip[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.chip .ct{font-family:var(--mono);font-size:11.5px;margin-left:6px;opacity:.75}
.empty td{padding:22px 12px;color:var(--muted);font-style:italic}
.shown{margin-left:auto;font-family:var(--mono);font-size:12px;color:var(--muted)}
.scroll{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:4px}
table{width:100%;border-collapse:collapse;min-width:980px;font-size:13.5px}
th{text-align:left;font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:500;padding:10px 12px;border-bottom:1px solid var(--line);background:var(--surface-2);position:sticky;top:0}
td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
.mono{font-family:var(--mono);font-weight:600;color:var(--term);white-space:nowrap}
.q{font-style:italic;color:var(--ink-2);max-width:32ch}.src{display:block;color:var(--muted);font-size:12.5px}.cap{color:var(--muted);font-size:12px}
.sev-major{color:var(--s-incorrect);font-weight:600}.sev-blocker{color:var(--s-incorrect);font-weight:700;text-transform:uppercase;letter-spacing:.04em}
.dest{font-family:var(--mono);font-size:12px;white-space:nowrap}
footer{font-size:12.5px;color:var(--muted);border-top:1px solid var(--line);padding-top:14px}footer p{margin:0 0 6px}
@media(max-width:820px){.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}.band{grid-template-columns:1fr}}
@media(max-width:420px){.vbar{grid-template-columns:92px 1fr 28px}.majors li{grid-template-columns:40px 1fr}.majors .mtext{grid-column:1/-1}}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
"""

JS = """
(function(){
  var chips=[].slice.call(document.querySelectorAll('.chip'));
  var rows=[].slice.call(document.querySelectorAll('#ledger tr'));
  var shown=document.getElementById('shown');
  var tbody=document.getElementById('ledger');
  var cols=document.querySelectorAll('thead th').length;
  var empty=document.createElement('tr');empty.className='empty';empty.hidden=true;
  empty.innerHTML='<td colspan="'+cols+'"></td>';tbody.appendChild(empty);
  function match(r,f){return f==='all'||(' '+r.getAttribute('data-tags')+' ').indexOf(' '+f+' ')>-1;}
  chips.forEach(function(c){
    var f=c.getAttribute('data-f'),n=rows.filter(function(r){return match(r,f);}).length;
    c.innerHTML=c.textContent+'<span class="ct">'+n+'</span>';
  });
  function apply(f){
    var n=0;
    rows.forEach(function(r){var ok=match(r,f);r.hidden=!ok;if(ok)n++;});
    empty.hidden=n>0;if(!n)empty.firstChild.textContent='No claims match this filter.';
    chips.forEach(function(c){c.setAttribute('aria-pressed',String(c.getAttribute('data-f')===f));});
    shown.textContent=n+' of '+rows.length+' claims';
  }
  chips.forEach(function(c){c.addEventListener('click',function(){apply(c.getAttribute('data-f'));});});
  apply('all');
})();
"""


def render_html(findings, *, document="", brain_version="", run_date=None,
                 brain_name="", title="", eyebrow="", lede="", output_name="") -> str:
    run_date = run_date or datetime.date.today().isoformat()
    n = len(findings)
    counts = {v: sum(1 for f in findings if f.get("verdict") == v) for v in VERDICTS}
    comments = sum(1 for f in findings if _has_comment(f))
    majors = [f for f in findings if _is_major(f)]
    blockers = sum(1 for f in majors if f.get("severity") == "Blocker")
    major_only = len(majors) - blockers
    figures = sum(1 for f in findings if _is_figure(f))
    verified_pct = round(100 * counts["Verified"] / n) if n else 0

    title = title or "Fact-check findings"
    meta_items = [("Document", document), ("Brain", brain_name), ("Knowledge", brain_version),
                  ("Run", run_date), ("Output", output_name)]
    meta_html = "".join(f'<span>{_e(k)} <b>{_e(v)}</b></span>' for k, v in meta_items if v)

    major_label = ", ".join(s for s in (f"{blockers} Blocker" if blockers else "",
                                         f"{major_only} Major" if major_only else "") if s) or "none"
    tiles = [("claims", n, "claims checked", "across the document"),
             ("verified", counts["Verified"], "verified", f"{verified_pct}% of all claims"),
             ("major", len(majors), "blocker + major", major_label),
             ("comments", comments, "comments in the file", "every finding, for review")]
    tiles_html = "".join(
        f'<div class="tile{" alert" if k == "major" and v else ""}"><div class="k">{_e(l)}</div>'
        f'<div class="v" data-stat="{k}"{f" style=\"color:var(--s-verified)\"" if k == "verified" else ""}>{v}</div>'
        f'<div class="n">{_e(sub)}</div></div>' for k, v, l, sub in tiles)

    bars_html = "".join(
        f'<div class="vbar"><span class="vlabel">{_e(v)}</span>'
        f'<div class="vtrack" role="img" aria-label="{_e(v)}: {counts[v]} of {n}">'
        f'<div class="vfill s-{_slug(v)}" style="width:{(100 * counts[v] / n) if n else 0:.1f}%"></div></div>'
        f'<span class="vnum">{counts[v]}</span></div>' for v in VERDICTS)

    if majors:
        majors_html = (f'<h2>Blocker and Major findings</h2><ul class="majors">' + "".join(
            f'<li><span class="mid">{_e(f.get("id"))}</span>'
            f'<span class="pill s-{_slug(f.get("verdict"))}">{_e(f.get("verdict"))}</span>'
            f'<span class="mtext">{_e(f.get("evidence"))}</span></li>' for f in majors) + "</ul>")
    else:
        majors_html = '<h2>Blocker and Major findings</h2><p class="note">None.</p>'

    chip_defs = [("all", "All", True), ("finding", "All findings", n - counts["Verified"]),
                 ("major", "Blocker + Major", len(majors)), ("in-document", "In document", comments)]
    chip_defs += [(_slug(v), v, counts[v]) for v in VERDICTS]
    chip_defs.append(("figure", "Figure only", figures))
    chips_html = "".join(f'<button class="chip" data-f="{k}" aria-pressed="{"true" if k == "all" else "false"}">{_e(l)}</button>'
                         for k, l, present in chip_defs if present)

    def _sev_cell(f):
        sev = f.get("severity")
        cls = f" sev-{_slug(sev)}" if sev in MAJOR_SEVERITIES else ""
        return f'<td class="sev{cls}">{_e(sev)}</td>'

    rows = "".join(
        f'<tr data-tags="{_tags(f)}">'
        f'<td class="mono">{_e(f.get("id"))}</td><td>{_e(f.get("where") or f.get("section"))}</td>'
        f'<td class="q">{_e(f.get("quote"))}</td>'
        f'<td><span class="pill s-{_slug(f.get("verdict"))}">{_e(f.get("verdict"))}</span></td>'
        f'{_sev_cell(f)}<td>{_e(f.get("confidence"))}</td>'
        f'<td>{_e(f.get("evidence"))}</td><td>{_sources(f)}</td>'
        f'<td class="dest">{"Comment" if _has_comment(f) else "Log"}</td></tr>' for f in findings)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title><style>{CSS}</style></head>
<body><div class="wrap">
<header>
{f'<span class="eyebrow">{_e(eyebrow)}</span>' if eyebrow else ""}
<h1>{_e(title)}</h1>
{f'<p class="lede">{_e(lede)}</p>' if lede else ""}
<div class="meta">{meta_html}</div>
</header>
<section class="tiles" aria-label="Summary">{tiles_html}</section>
<section class="band">
<div class="panel"><h2>Verdicts</h2><div class="bars">{bars_html}</div><p class="note">Bars are drawn to scale against all {n} claims.</p></div>
<div class="panel">{majors_html}</div>
</section>
<section>
<h2>Claim ledger</h2>
<div class="chips" role="group" aria-label="Filter claims">{chips_html}<span class="shown" id="shown"></span></div>
<div class="scroll"><table><thead><tr><th>ID</th><th>Where</th><th>Claim</th><th>Verdict</th><th>Severity</th><th>Conf.</th><th>Brain evidence</th><th>Source files</th><th>Action</th></tr></thead>
<tbody id="ledger">
{rows}
</tbody></table></div>
</section>
<footer>
<p>Method: numbers checked against governed Brain metrics where one exists; narrative and topology checked via Brain search with cited evidence. Newer sources win disagreements; the older value is shown as context.</p>
</footer>
</div><script>{JS}</script></body></html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("findings")
    ap.add_argument("--out", required=True)
    ap.add_argument("--document", default="")
    ap.add_argument("--brain-version", default="")
    ap.add_argument("--brain-name", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--eyebrow", default="")
    ap.add_argument("--lede", default="")
    ap.add_argument("--output-name", default="")
    a = ap.parse_args(argv)
    data = json.load(open(a.findings))
    if isinstance(data, dict):
        data = data.get("findings") or data.get("claims") or next(iter(data.values()))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(data, document=a.document, brain_version=a.brain_version, brain_name=a.brain_name,
                               title=a.title, eyebrow=a.eyebrow, lede=a.lede, output_name=a.output_name),
                   encoding="utf-8")
    print(out.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
