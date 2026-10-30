"""Findings page for Teams proposals: every finding with its quote, task, suggestion and gate, plus rejection counts.

Reads out/teams-findings.jsonl and chat_rejected.jsonl; writes out/teams-findings-<date>.html. Self-contained HTML.
"""
import html
import json
import os
from collections import Counter

CSS = """:root{--bg:#f6f7f9;--s:#fff;--l:#e3e6eb;--i:#16181d;--i2:#4a505c;--a:#2f54d4;--r:#b4233c;--w:#a15c07;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1115;--s:#16191f;--l:#262b34;--i:#e8eaee;--i2:#a9b0bc;--a:#7d97ff;--r:#f07a8c;--w:#e3a54e;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0f1115;--s:#16191f;--l:#262b34;--i:#e8eaee;--i2:#a9b0bc;--a:#7d97ff;--r:#f07a8c;--w:#e3a54e;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--i);font:14px/1.5 system-ui,sans-serif;padding:16px}
.w{max-width:900px;margin:0 auto}.tiles{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.tile{background:var(--s);border:1px solid var(--l);border-radius:8px;padding:8px 12px}
.tile b{display:block;font-size:20px}.f{background:var(--s);border:1px solid var(--l);border-radius:8px;padding:12px;margin:8px 0;overflow-wrap:anywhere}
.f.Blocker{border-left:3px solid var(--r)}.f.Major{border-left:3px solid var(--w)}.m{color:var(--i2);font-size:12px}.q{font-style:italic;margin:6px 0}
a{color:var(--a)}code{font-family:ui-monospace,monospace}"""


def _jsonl(p):
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def render(store):
    f = list({x["id"] + ":" + x["node"]: x for x in _jsonl(os.path.join(store, "out", "teams-findings.jsonl"))}.values())
    rej = Counter(r.get("reason", "").split(":")[0] for r in _jsonl(os.path.join(store, "chat_rejected.jsonl")))
    e = html.escape
    tiles = [("proposals", sum(x["verdict"] != "Verified" for x in f)), ("already true", sum(x["verdict"] == "Verified" for x in f)),
             ("below threshold", rej.get("below_threshold", 0)), ("too short", rej.get("too_short", 0)),
             ("no candidate", rej.get("no_candidate", 0)), ("refused by ledger", rej.get("ledger_refused", 0))]
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Teams Findings</title><style>{CSS}</style></head><body><div class='w'><h1>Teams findings</h1>",
           "<div class='tiles'>" + "".join(f"<div class='tile'><b>{n}</b>{e(k)}</div>" for k, n in tiles) + "</div>"]
    for x in sorted(f, key=lambda x: (x["severity"] != "Blocker", x["at"])):
        g = x.get("gate") or {}
        out.append(f"<div class='f {e(x['severity'])}'><div class='m'>{e(x['verdict'])} · {e(x['severity'])} · "
                   f"score {g.get('score')} (propose ≥ {g.get('threshold')}) · {e(str(g.get('model') or ''))} · <code>{e(x['id'])}</code></div>"
                   f"<div><b>{e(x['node'])}</b> {e(x['title'])}</div><div class='q'>“{e(x['quote'])}”</div>"
                   f"<div class='m'>{e(x['sender'])} · {e(x['at'])}{' · files: ' + e(', '.join(x['files'])) if x.get('files') else ''}"
                   f"{f' · <a href=' + chr(34) + e(x['link']) + chr(34) + ' target=_blank rel=noopener>Open in Teams</a>' if x.get('link') else ''}</div>"
                   f"<div><b>Why:</b> {e(x['why'])}</div><div><b>Suggested:</b> <code>{e(x['suggested'])}</code></div></div>")
    out.append("</div></body></html>")
    return "".join(out)


def write(store, date):
    p = os.path.join(store, "out", f"teams-findings-{date}.html")
    with open(p, "w") as fh:
        fh.write(render(store))
    return p
