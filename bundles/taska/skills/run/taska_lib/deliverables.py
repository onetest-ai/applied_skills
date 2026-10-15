"""The Deliverables lens: one row per story, derived from the folded tree and the evidence index.

A story is the deliverable unit — the shippable outcome its acceptance criteria define. `landed`
records the newest file-kind evidence that changed the story: an actual artifact appeared in the
document source (SharePoint/OneDrive) and the agent used it. Read-only — no new stored concept;
every field is already on the node or in the evidence index. Both `pack` and the workbench render
from this one function, so the prep sheet and the board never disagree.
"""


def deliverables(N, ev):
    """[{id, title, internal_owner, due, state, ac_met, ac_total, landed}] for every story.

    `landed` is {date, title} of the newest file evidence in the story's `changed_in`, else None.
    `N` is the folded node map; `ev` is the evidence index (id -> item with kind/date/title).
    """
    out = []
    for n in N.values():
        if n.get("kind") != "story":
            continue
        files = [ev[e] for e in n.get("changed_in", []) if ev.get(e, {}).get("kind") == "file"]
        latest = max(files, key=lambda f: f.get("date") or "", default=None)
        ac = n.get("ac") or []
        out.append({
            "id": n["id"], "title": n.get("title"), "internal_owner": n.get("internal_owner"),
            "due": n.get("due"), "state": n.get("state"),
            "ac_met": sum(1 for x in ac if x.get("met")), "ac_total": len(ac),
            "landed": {"date": latest["date"], "title": latest["title"]} if latest else None,
        })
    return out
