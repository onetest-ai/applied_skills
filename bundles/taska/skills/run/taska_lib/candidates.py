"""Candidate stories/tasks for a Teams message: stdlib BM25 over title + acceptance criteria + parent title.

Only nodes created on or before the message date, any state (a closed task is still the one a message can be
about). Epics, risks and decisions are never candidates. An exact id in the text ("T056") comes first.
"""
import math
import re
from collections import Counter

STOP = set("a an and are as at be by for from has have i in is it its of on or our that the this to was we will with you "
           "your can do done get got just let lets me my not now please pls so thanks team then there they up us what when "
           "which who would should could about all also any been but did does more need one out see some than them these "
           "those very".split())
KINDS = ("story", "task")
SHORT = 15


def toks(s):
    return [w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in STOP and len(w) > 1]


def node_text(n, by_id):
    par = by_id.get(n.get("parent") or "", {})
    ac = " ".join(a.get("text", "") if isinstance(a, dict) else str(a) for a in n.get("ac") or [])
    return f"{n['title']} {ac} {par.get('title', '')}"


def candidates(msg, nodes, k=8):
    """[(node_id, bm25, rank)] for msg {text, ts, files, ctx}; nodes carry id, kind, title, ac, parent, created."""
    by_id = {n["id"]: n for n in nodes}
    day = (msg.get("ts") or "")[:10]
    elig = [n for n in nodes if n.get("kind") in KINDS and (not n.get("created") or not day or str(n["created"])[:10] <= day)]
    if not elig:
        return []
    docs = [Counter(toks(node_text(n, by_id))) for n in elig]
    lens = [sum(d.values()) for d in docs]
    avg = sum(lens) / len(lens) or 1.0
    df = Counter(w for d in docs for w in d)
    idf = {w: math.log(1 + (len(docs) - c + .5) / (c + .5)) for w, c in df.items()}
    text = msg.get("text") or ""
    q = toks(text + " " + " ".join(msg.get("files") or []))
    if len(text.split()) < SHORT:
        q += toks(msg.get("ctx") or "")
    qs = set(q)

    def score(i):
        d, s = docs[i], 0.0
        for w in qs:
            f = d.get(w)
            if f:
                s += idf[w] * f * 2.4 / (f + 1.4 * (0.25 + 0.75 * lens[i] / avg))
        return s

    scored = [(n["id"], round(score(i), 3)) for i, n in enumerate(elig)]
    exact = [(i, s) for i, s in scored if re.search(r"\b" + re.escape(i) + r"\b", text)]
    rest = sorted((x for x in scored if x[1] > 0 and x not in exact), key=lambda x: -x[1])
    out = list(dict.fromkeys(exact + rest))[:k]
    return [(i, float(s), r) for r, (i, s) in enumerate(out)]
