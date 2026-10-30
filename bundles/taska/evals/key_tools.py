"""Answer-key tooling: merge, collapse, version, label (r2 plan, R12).

- merge_key(judged, recheck, user_answers): the key's authority order, last wins: the judges' reading, then a recheck's
  drops, then every answer the user gave. Each item records whether its answer came from the user or a judge.
- collapse_near_duplicates(gold): items of one meeting that resolve to the same existing node are one piece of work;
  they become one item carrying all their quotes, so a hit is never counted twice.
- write_version(dirpath, items, version, date, note): a key version is a new file (items.vN.jsonl) with a fingerprinted
  header and a CHANGELOG line; an existing version is never overwritten.
- label_page(items, stamp): a local page that asks "Is it work?" and then "Is it new?" (showing the node taska already
  has), and refuses to save when it is an older copy than the served items.json (a cached page once put five answers
  on the wrong items).
"""
import html
import json
import sys
from pathlib import Path

_RUN_DIR = Path(__file__).resolve().parent.parent / "skills" / "run"
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

from taska_lib.evalscore import _body_sha  # noqa: E402  (one fingerprint definition for score, load_gold and this)

WORK = ("committed", "proposed", "declined")


def merge_key(judged, recheck, user_answers):
    out = {j["cid"]: dict(j, source="judge") for j in judged}
    for r in recheck or []:
        if r.get("verdict") == "not_work" and r["cid"] in out:
            out[r["cid"]].update(commitment="not_work", kind=None, title=None, owner=None,
                                 note=f"Recheck ({r.get('rule')}): {r.get('note', '')}")
    for cid, u in (user_answers or {}).items():
        if cid not in out or u.get("kind") not in WORK + ("not_work",):
            continue
        j = out[cid]
        j["commitment"] = u["kind"]
        if u["kind"] == "not_work":
            j.update(kind=None, title=None, owner=None)
        else:
            j["kind"] = j.get("kind") or u.get("item_kind") or "task"
            j["title"] = u.get("title") or j.get("title") or "Work item set by the user"
            for k in ("owner", "client_owed"):
                if k in u and u[k] not in (None, ""):
                    j[k] = u[k]
        j["source"] = "user"
    return list(out.values())


def collapse_near_duplicates(gold):
    keep, seen = [], {}
    for g in gold:
        k = (g.get("evidence"), g.get("covered_by"))
        if g.get("covered_by") and k in seen:
            first = seen[k]
            first.setdefault("quotes", [first["quote"]]).append(g["quote"])
            first.setdefault("merged_cids", []).append(g["cid"])
            continue
        g = dict(g)
        keep.append(g)
        if g.get("covered_by"):
            seen[k] = g
    return keep


def write_version(dirpath, items, version, date, note):
    d = Path(dirpath)
    p = d / f"items.{version}.jsonl"
    if p.exists():
        raise FileExistsError(f"{p} exists: a key version is never overwritten; use a new version name")
    lines = [json.dumps(i, ensure_ascii=False) for i in items]
    p.write_text("\n".join([f"# gold {version} {date} n={len(lines)} sha256={_body_sha(lines)}"] + lines) + "\n",
                 encoding="utf-8")
    with open(d / "CHANGELOG.md", "a", encoding="utf-8") as f:
        f.write(f"- {version} ({date}): n={len(lines)}, sha256={_body_sha(lines)[:12]} — {note}\n")
    return p


def label_page(items, stamp):
    e = lambda s: html.escape(str(s if s is not None else ""))
    cards = []
    for i, it in enumerate(items):
        turns = "".join(f"<p class=t>[{e(t.get('ts'))}] <b>{e(t.get('spk'))}:</b> {e(t.get('text'))}</p>" for t in it.get("turns", [])) \
            or f"<p class='t hit'>[{e(it.get('ts'))}] <b>{e(it.get('speaker'))}:</b> {e(it.get('quote'))}</p>"
        ex = it.get("existing")
        has = f"<p class=has>taska already has: {e(ex['id'])} · {e(ex['title'])}</p>" if ex else "<p class=has>taska has nothing that looks like this.</p>"
        cards.append(
            f"<section class=q data-cid='{e(it['cid'])}'><p class=m>{i + 1}. {e(it.get('meeting'))} · {e(it.get('date'))}</p>{turns}"
            f"<p><b>Is it work?</b> " + "".join(f"<label><input type=radio name='w-{e(it['cid'])}' value={v}> {l}</label> "
                                              for v, l in (("committed", "Committed"), ("proposed", "Proposed"),
                                                           ("not_work", "Not work"), ("unsure", "Can't tell"))) + "</p>"
            f"{has}<p><b>Is it new?</b> " + "".join(f"<label><input type=radio name='n-{e(it['cid'])}' value={v}> {l}</label> "
                                                  for v, l in (("new", "New work"), ("tracked", "Already tracked"), ("unsure", "Can't tell"))) +
            "</p><textarea placeholder='Note: why, where you hesitated'></textarea></section>")
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Answer Key Labels</title><style>
:root{{--bg:#f7f8fa;--card:#fff;--fg:#1d2330;--mut:#5b6474;--line:#e3e6eb;--acc:#2f54d4}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#0f1218;--card:#171b23;--fg:#e6e9ef;--mut:#9aa3b2;--line:#2a2f3a;--acc:#8aa4ff}}}}
:root[data-theme="dark"]{{--bg:#0f1218;--card:#171b23;--fg:#e6e9ef;--mut:#9aa3b2;--line:#2a2f3a;--acc:#8aa4ff}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}} .wrap{{max-width:820px;margin:0 auto;padding:24px 16px 100px}}
.q{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:0 0 12px}} .m,.has{{color:var(--mut);font-size:13px}}
.t{{font-size:14px;margin:0 0 6px}} label{{margin-right:10px}} textarea{{width:100%;min-height:34px;background:transparent;color:var(--fg);border:1px solid var(--line);border-radius:8px}}
.bar{{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:10px 16px}}
button{{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:9px 14px;font-weight:600}}</style></head>
<body><div class=wrap><h1>Answer key labels</h1><p class=m>For each item answer two separate questions: whether it is work by your rule, and whether
taska already tracks it. "Already tracked" is still work.</p>{''.join(cards)}</div>
<div class=bar><button id=save>Save answers</button> <span class=m id=st></span></div>
<script>
const STAMP = {json.dumps(stamp)};
document.getElementById("save").onclick = async () => {{
  try {{ const live = await (await fetch("items.json", {{cache: "no-store"}})).json();
         if (live.stamp !== STAMP) {{ document.getElementById("st").textContent = "This is an older page than the items on disk: reload it (Cmd-Shift-R) and answer again."; return; }} }}
  catch (e) {{ document.getElementById("st").textContent = "Could not check the page version; open it from the local server."; return; }}
  const A = {{}};
  document.querySelectorAll(".q").forEach(q => {{ const c = q.dataset.cid;
    const w = q.querySelector(`[name="w-${{c}}"]:checked`), n = q.querySelector(`[name="n-${{c}}"]:checked`);
    A[c] = {{kind: w && w.value, new: n && n.value, note: q.querySelector("textarea").value.trim()}}; }});
  const b = new Blob([JSON.stringify({{stamp: STAMP, saved_at: new Date().toISOString(), answers: A}}, null, 1)], {{type: "application/json"}});
  const a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = "key-labels-" + STAMP + ".json"; a.click();
  document.getElementById("st").textContent = "Saved.";
}};
</script></body></html>"""
