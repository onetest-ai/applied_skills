"""similar: token-set Jaccard similarity for nodes in the work tree.

Used by lint (C17) and the CLI `taska similar` command.

t = Jaccard(title_tokens); score = min(1.0, t + 0.25 * Jaccard(parent_title_tokens))
only when t >= 0.3 and both the query node and the candidate have parents; else score = t.
Stopwords and single-character tokens are stripped before scoring.
"""
from .text import norm

STOPWORDS = frozenset({
    "a", "an", "the", "to", "of", "for", "and", "or", "on", "in",
    "with", "from", "by", "is", "be", "our", "we", "it", "this", "that",
})


def _tokens(title):
    """Normalised token set for a title, minus stopwords and single-character tokens."""
    return {t for t in norm(title or "").split() if t not in STOPWORDS and len(t) > 1}


def _jaccard(a, b):
    if not a and not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def node_score(N, a, b):
    """Similarity score between two node dicts a and b, both from N.

    Formula:
        t = Jaccard(title_tokens)
        if t >= 0.3 and both nodes have a parent present in N:
            score = min(1.0, t + 0.25 * Jaccard(parent_title_tokens))
        else:
            score = t

    Returns a float in [0, 1.0].
    """
    ta = _tokens(a["title"])
    tb = _tokens(b["title"])
    t = _jaccard(ta, tb)
    ap = a.get("parent")
    bp = b.get("parent")
    if t >= 0.3 and ap and ap in N and bp and bp in N:
        pa = _tokens(N[ap]["title"])
        pb = _tokens(N[bp]["title"])
        t += 0.25 * _jaccard(pa, pb)
    return min(1.0, t)


def similar(N, title, kind, k=3, min_score=0.5, node_id=None):
    """Return the top-k nodes of `kind` (not merged/deleted) similar to `title`.

    Parameters
    ----------
    N        : dict id → node (from ledger.fold)
    title    : title string to search against
    kind     : node kind to restrict candidates to
    k        : maximum results to return
    min_score: minimum similarity threshold
    node_id  : id of the node this title belongs to (excluded from results;
               its parent is used for the parent bonus)

    Returns a list of {"id", "title", "state", "parent", "parent_state", "score"},
    sorted by score descending then id ascending.
    """
    t_tokens = _tokens(title)

    # Derive query parent tokens (for bonus) from the known node_id
    q_parent_tokens = None
    if node_id and node_id in N:
        qn = N[node_id]
        qp = qn.get("parent")
        if qp and qp in N:
            q_parent_tokens = _tokens(N[qp]["title"])

    results = []
    for nid, n in N.items():
        if nid == node_id:
            continue
        if n["kind"] != kind:
            continue
        if n.get("merged_into") or n.get("state") in ("merged", "deleted"):
            continue

        # Title similarity
        c_tokens = _tokens(n["title"])
        s = _jaccard(t_tokens, c_tokens)

        # Parent bonus: only when title Jaccard >= 0.3 AND both query and candidate have parents
        if s >= 0.3 and q_parent_tokens is not None:
            cp = n.get("parent")
            if cp and cp in N:
                c_parent_tokens = _tokens(N[cp]["title"])
                s += 0.25 * _jaccard(q_parent_tokens, c_parent_tokens)
        s = min(1.0, s)

        if s < min_score:
            continue

        parent_id = n.get("parent")
        parent_state = N[parent_id]["state"] if parent_id and parent_id in N else None
        results.append({
            "id": nid,
            "title": n["title"],
            "state": n["state"],
            "parent": parent_id,
            "parent_state": parent_state,
            "score": round(s, 6),
        })

    results.sort(key=lambda x: (-x["score"], x["id"]))
    return results[:k]
