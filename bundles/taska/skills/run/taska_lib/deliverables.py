"""The Deliverables lens: one row per story, derived from the folded tree and the evidence index.

A story is the deliverable unit — the shippable outcome its acceptance criteria define. `landed`
records the newest file-kind evidence whose change to the story was applied: an actual artifact appeared in
the document source (SharePoint/OneDrive) and moved the story. An unverified or pin-refused claim changed
nothing, and a manual edit is stamped with the latest evidence (often a file) without coming from it, so
neither counts. Merged and deleted stories are not deliverables. Read-only — no new stored concept;
every field is already on the node or in the evidence index. `pack` renders from this function; the
workbench has a JS twin, `deliverableRows` in workbench.html, computed per snapshot so the view follows
the timeline. The two must apply the same rules: test_workbench_timeline.py runs both on one fixture.
"""


_CLOSED = {"done", "cancelled", "merged", "deleted"}


def _deliverable_rag(n, N):
    """Green/Amber/Red from deliverable state + overdue signal."""
    state = n.get("state") or "open"
    if state in ("done",):
        return "Green"
    if state == "blocked":
        return "Red"
    # overdue: has a due date in the past relative to latest history date
    due = n.get("due")
    if due and n.get("history"):
        last_date = max((h.get("date") or "") for h in n["history"])
        if last_date and due < last_date:
            return "Red"
    if state == "in_progress":
        return "Amber"
    return "Green"


def deliverables(N, ev):
    """[{id, title, internal_owner, due, first_draft_due, state, rag, ac_met, ac_total, landed}] for every story.

    `first_draft_due`: earliest due date among non-closed child tasks.
    `rag`: Green/Amber/Red derived from state + overdue signal.
    `landed` is {date, title} of the newest file evidence with an applied agent change, else None.
    `N` is the folded node map; `ev` is the evidence index (id -> item with kind/date/title).
    """
    out = []
    for n in N.values():
        if n.get("kind") != "story" or n.get("state") in ("merged", "deleted"):
            continue
        files = [ev[h["evidence"]] for h in n.get("history", [])
                 if h.get("applied") and h.get("by") != "human" and ev.get(h.get("evidence"), {}).get("kind") == "file"]
        latest = max(files, key=lambda f: f.get("date") or "", default=None)
        ac = n.get("ac") or []

        # first_draft_due: min due across non-closed child tasks that have a due date
        child_dues = [
            N[c]["due"] for c in n.get("children", [])
            if c in N and N[c].get("due") and N[c].get("state") not in _CLOSED
        ]
        first_draft_due = min(child_dues) if child_dues else None

        out.append({
            "id": n["id"], "title": n.get("title"), "internal_owner": n.get("internal_owner"),
            "due": n.get("due"), "first_draft_due": first_draft_due,
            "state": n.get("state"), "rag": _deliverable_rag(n, N),
            "ac_met": sum(1 for x in ac if x.get("met")), "ac_total": len(ac),
            "landed": {"date": latest["date"], "title": latest["title"]} if latest else None,
        })
    return out
