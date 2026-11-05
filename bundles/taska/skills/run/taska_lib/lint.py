"""lint: deterministic tree-health checks on a folded work tree.

C17  duplicate pairs   — open (not done/cancelled) nodes of same kind with Jaccard ≥ 0.5
C18  open-under-closed — open task or story whose parent is done or cancelled
C19  dependency cycles — cycles in depends_on among stories/tasks
C20  zombie story      — open story with ≥ 1 task child, all task children done

All checks ignore nodes that are merged or deleted.
"""
from .similar import node_score

# Kinds checked for duplicate pairs (C17)
_C17_KINDS = frozenset({"task", "story", "decision", "risk"})
# Closed states for C18 parent check
_CLOSED = frozenset({"done", "cancelled"})
# States that disqualify a node from being "open" for C17/C18/C20
_INACTIVE = frozenset({"merged", "deleted"})


def _active(n):
    """True when the node should be included in lint (not merged/deleted)."""
    return not n.get("merged_into") and n.get("state") not in _INACTIVE


def _open(n):
    """True when the node is active and not done/cancelled."""
    return _active(n) and n.get("state") not in _CLOSED


def _find_cycles(graph):
    """Find all cycles in a directed graph.

    Parameters
    ----------
    graph : dict id -> list[id]  (adjacency list; edges to unknown nodes are ignored)

    Returns a list of sorted id lists, one per unique cycle, sorted overall.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    seen = set()   # frozensets of already-reported cycle node sets
    cycles = []

    def _dfs(start):
        stack = []          # (node, children_iter)
        path = []           # current DFS path
        path_pos = {}       # node -> index in path

        def _enter(u):
            color[u] = GRAY
            path.append(u)
            path_pos[u] = len(path) - 1
            stack.append((u, iter(graph.get(u, []))))

        _enter(start)
        while stack:
            u, children = stack[-1]
            try:
                v = next(children)
                if v not in color:
                    continue           # edge to node outside the graph
                if color[v] == GRAY:
                    # Back edge: cycle found
                    cycle_nodes = path[path_pos[v]:]
                    key = frozenset(cycle_nodes)
                    if key not in seen:
                        seen.add(key)
                        cycles.append(sorted(cycle_nodes))
                elif color[v] == WHITE:
                    _enter(v)
            except StopIteration:
                stack.pop()
                if path and path[-1] == u:
                    path.pop()
                    del path_pos[u]
                color[u] = BLACK

    for u in list(graph):
        if color[u] == WHITE:
            _dfs(u)

    return sorted(cycles)


def lint(N):
    """Run all four tree-health checks and return a dict with four lists.

    Returns
    -------
    {
        "C17_duplicates":              [[id_a, id_b, score], ...],
        "C18_open_under_closed":       [id, ...],
        "C19_cycles":                  [[id, ...], ...],
        "C20_open_with_done_children_all": [id, ...],
    }
    """
    # ------------------------------------------------------------------ C17
    c17_seen = set()
    c17 = []
    candidates_17 = [n for n in N.values()
                     if _open(n) and n["kind"] in _C17_KINDS]
    for i, a in enumerate(candidates_17):
        for b in candidates_17[i + 1:]:
            if a["kind"] != b["kind"]:
                continue
            score = node_score(N, a, b)
            if score >= 0.5:
                id_a, id_b = (a["id"], b["id"]) if a["id"] < b["id"] else (b["id"], a["id"])
                pair = (id_a, id_b)
                if pair not in c17_seen:
                    c17_seen.add(pair)
                    c17.append([id_a, id_b, round(score, 6)])
    c17.sort(key=lambda x: (x[0], x[1]))

    # ------------------------------------------------------------------ C18
    c18 = []
    for n in N.values():
        if not _open(n):
            continue
        if n["kind"] not in ("task", "story"):
            continue
        pid = n.get("parent")
        if not pid or pid not in N:
            continue
        parent = N[pid]
        if parent.get("state") in _CLOSED:
            c18.append(n["id"])
    c18.sort()

    # ------------------------------------------------------------------ C19
    graph = {}
    for nid, n in N.items():
        if not _active(n) or n["kind"] not in ("task", "story"):
            continue
        deps = [d for d in (n.get("depends_on") or [])
                if d in N and _active(N[d]) and N[d]["kind"] in ("task", "story")]
        graph[nid] = deps
    c19 = _find_cycles(graph)

    # ------------------------------------------------------------------ C20
    # Build task-children index for stories
    story_tasks = {}  # story_id -> list of task nodes
    for n in N.values():
        if not _active(n) or n["kind"] != "task":
            continue
        pid = n.get("parent")
        if pid and pid in N and N[pid]["kind"] == "story":
            story_tasks.setdefault(pid, []).append(n)

    c20 = []
    for nid, n in N.items():
        if n["kind"] != "story":
            continue
        if not _open(n):
            continue
        tasks = story_tasks.get(nid, [])
        if not tasks:
            continue
        active_tasks = [t for t in tasks if _active(t)]
        if not active_tasks:
            continue
        if all(t["state"] == "done" for t in active_tasks):
            c20.append(nid)
    c20.sort()

    return {
        "C17_duplicates": c17,
        "C18_open_under_closed": c18,
        "C19_cycles": c19,
        "C20_open_with_done_children_all": c20,
    }
