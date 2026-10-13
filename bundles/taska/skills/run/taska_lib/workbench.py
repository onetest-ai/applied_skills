"""The workbench page: per-evidence snapshots of the tree for playback, a local server for edits, a static export."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import ledger as L, staleness as ST, store as S

PAGE = Path(__file__).resolve().parent.parent / "workbench.html"
KEEP = ("id", "kind", "type", "title", "parent", "state", "derived", "internal_owner", "client_owner", "client_owed",
        "due", "due_history", "ac", "depends_on", "waiting_on", "waiting_for", "blocks", "children", "pinned",
        "suggestions", "merged_into", "window", "plan", "phase", "created", "created_in", "by")


def snapshot(store, cfg, evs, ev, c):
    folded = L.fold(store, c, evs=evs, ev=ev)
    rows, N = ST.classify(store, cfg, folded=folded)
    nodes = {i: {k: n.get(k) for k in KEEP} | {"changed_in": sorted(set(n["changed_in"]))} for i, n in N.items()}
    return {"nodes": nodes, "history": {i: n["history"] for i, n in N.items()},
            "asks": [{"node": r["node"]["id"], "flags": r["flags"], "score": r["score"], "ask": r["ask"],
                      "shadow": r["shadow"], "tier": r["tier"], "top": r["top"], "q": ST.question(r, N)} for r in rows],
            "notes": {k: [{x: v.get(x) for x in ("date", "ts", "speaker", "quote", "text", "evidence")} for v in vs]
                      for k, vs in folded[1].items()}}


def data(store, cfg):
    """Per-evidence snapshots for playback, delta-encoded: each snapshot carries only the nodes that changed since
    the previous one (`nodes`) and the ids that disappeared (`gone`); the page rebuilds full states in order.
    History and notes are sent once, from the latest fold, and filtered by evidence position in the page."""
    evs, ev = S.events(store), S.evidence(store)
    done = {e.get("evidence") for e in evs if e.get("by") == "agent"} | S.processed(evs)
    order = [c for c in S.order(ev) if c in done]
    snaps, prev, prev_asks, hist, notes = {}, {}, {}, {}, {}
    for c in order:
        sn = snapshot(store, cfg, evs, ev, c)
        hist, notes = sn.pop("history"), sn.pop("notes")
        cur = sn.pop("nodes")
        asks = {a["node"]: dict(a, rank=k) for k, a in enumerate(sn.pop("asks"))}
        sn["nodes"] = {i: n for i, n in cur.items() if prev.get(i) != n}
        sn["gone"] = [i for i in prev if i not in cur]
        sn["asks"] = {i: a for i, a in asks.items() if prev_asks.get(i) != a}
        sn["asks_gone"] = [i for i in prev_asks if i not in asks]
        snaps[c], prev, prev_asks = sn, cur, asks
    return {"project": cfg.name, "history": hist, "notes": notes, "snaps": snaps, "delta": True,
            "calls": [{"id": c, "date": ev[c]["date"], "at": ev[c]["at"], "evidence_kind": ev[c]["kind"],
                       "kind": ev[c].get("meeting_kind") or ev[c]["kind"], "title": ev[c]["title"],
                       "source": ev[c]["source"]} for c in order]}


def serve(store, cfg, port):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self._send(200, PAGE.read_bytes(), "text/html")
            if self.path == "/api/data":
                return self._send(200, data(store, cfg))
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/api/event":
                return self._send(404, {"error": "not found"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                e = L.human_event(store, body)
            except (ValueError, json.JSONDecodeError) as x:
                return self._send(400, {"error": str(x)})
            self._send(200, {"ok": True, "event": e})

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(json.dumps({"url": f"http://127.0.0.1:{port}/", "store": str(store)}), flush=True)
    srv.serve_forever()


def export(store, cfg, out):
    blob = json.dumps(data(store, cfg), ensure_ascii=False).replace("</", "<\\/")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(PAGE.read_text().replace("/*__DATA__*/null", blob))
    return str(out)
