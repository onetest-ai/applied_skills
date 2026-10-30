"""The workbench page: per-evidence snapshots of the tree for playback, a local server for edits, a static export."""
import datetime as dt
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import ledger as L, pack as PK, staleness as ST, store as S

PAGE = Path(__file__).resolve().parent.parent / "workbench.html"
RUN_DIR = PAGE.parent


def code_version(run_dir=RUN_DIR):
    """10 hex chars over the Python code and the page. A server keeps the code it started with, so comparing its
    start-time version with the version on disk is how a stale server shows itself."""
    run_dir = Path(run_dir)
    h = hashlib.sha1()
    for f in sorted([run_dir / "taska.py", run_dir / "workbench.html", *(run_dir / "taska_lib").rglob("*.py")]):
        h.update(str(f.relative_to(run_dir)).encode() + b"\0" + f.read_bytes())
    return h.hexdigest()[:10]


def version_info(running, on_disk, started):
    return {"running": running, "on_disk": on_disk, "started": started, "stale": running != on_disk}
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
    touched = {e.get("evidence") for e in evs if e.get("by") == "agent" and e.get("type") != "processed"}
    # a Teams message is a step only when it produced a proposal; the rest would flood the timeline
    order = [c for c in S.order(ev) if c in done and (ev[c]["kind"] != "message" or c in touched)]
    snaps, prev, prev_asks, hist, notes = {}, {}, {}, {}, {}
    for c in order:
        # the latest step shows the tree as it is now: later steps hidden from the timeline (a Teams message with no
        # proposal) still carry events, and your edits are stamped with the last processed item, which may be one
        sn = snapshot(store, cfg, evs, ev, None if c == order[-1] else c)
        hist, notes = sn.pop("history"), sn.pop("notes")
        cur = sn.pop("nodes")
        asks = {a["node"]: dict(a, rank=k) for k, a in enumerate(sn.pop("asks"))}
        sn["nodes"] = {i: n for i, n in cur.items() if prev.get(i) != n}
        sn["gone"] = [i for i in prev if i not in cur]
        sn["asks"] = {i: a for i, a in asks.items() if prev_asks.get(i) != a}
        sn["asks_gone"] = [i for i in prev_asks if i not in asks]
        snaps[c], prev, prev_asks = sn, cur, asks
    return {"project": cfg.name, "recap": PK.build(store, cfg, 7), "history": hist, "notes": notes, "snaps": snaps, "delta": True,
            "calls": [{"id": c, "date": ev[c]["date"], "at": ev[c]["at"], "evidence_kind": ev[c]["kind"],
                       "kind": ev[c].get("meeting_kind") or ev[c]["kind"], "title": ev[c]["title"],
                       "source": ev[c]["source"], "date_source": ev[c].get("date_source")} for c in order]}


def _local_origin(headers, port):
    """Only the page itself may talk to this server. A web page in the same browser can POST to 127.0.0.1 (a
    "simple" text/plain request needs no preflight) and DNS rebinding can read it back, so check both headers."""
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    if headers.get("Host", "") not in hosts:
        return False
    origin = headers.get("Origin")
    return origin is None or origin in {f"http://{h}" for h in hosts}


def server(store, cfg, port):
    running, started = code_version(), dt.datetime.now().isoformat(timespec="seconds")

    def version():
        return version_info(running, code_version(), started)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json", headers=None):
            b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if not _local_origin(self.headers, port):
                return self._send(403, {"error": "forbidden host"})
            if self.path in ("/", "/index.html"):
                return self._send(200, PAGE.read_bytes(), "text/html")
            if self.path == "/api/data":
                return self._send(200, dict(data(store, cfg), code=version()))
            if self.path == "/api/version":
                return self._send(200, version())
            if self.path.startswith("/api/recap"):   # the Recap view: taska pack for the chosen period
                from urllib.parse import urlparse, parse_qs
                try:
                    days = max(1, min(90, int((parse_qs(urlparse(self.path).query).get("days") or ["7"])[0])))
                except ValueError:
                    days = 7
                return self._send(200, {"days": days, "markdown": PK.build(store, cfg, days)})
            if self.path == "/api/export":   # the Download button: a read-only copy to share, as it is now
                slug = "".join(c if c.isalnum() else "-" for c in cfg.name.lower()).strip("-")
                name = f"{slug}-workbench-{dt.date.today().isoformat()}.html"
                return self._send(200, export_html(store, cfg).encode(), "text/html",
                                  {"Content-Disposition": f'attachment; filename="{name}"'})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/api/event":
                return self._send(404, {"error": "not found"})
            if not _local_origin(self.headers, port):
                return self._send(403, {"error": "forbidden host"})
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                return self._send(415, {"error": "Content-Type must be application/json"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                e = L.human_event(store, body)
            except (ValueError, TypeError, AttributeError, KeyError) as x:  # json errors are ValueErrors
                return self._send(400, {"error": f"{type(x).__name__}: {x}"})
            self._send(200, {"ok": True, "event": e})

    return ThreadingHTTPServer(("127.0.0.1", port), H)


def serve(store, cfg, port):
    srv = server(store, cfg, port)
    print(json.dumps({"url": f"http://127.0.0.1:{port}/", "store": str(store), "code": code_version()}), flush=True)
    srv.serve_forever()


def export_html(store, cfg):
    """The workbench as one self-contained, read-only page: the data embedded, so the page knows it is a copy."""
    # "<" -> \u003c: "</" alone leaves "<!--<script" able to switch the HTML parser into script-data-double-escaped
    # state, where the page's own closing tag no longer ends the script; U+2028/9 are not valid in older JS strings
    blob = json.dumps(data(store, cfg), ensure_ascii=False).replace("<", "\\u003c") \
        .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return PAGE.read_text().replace("/*__DATA__*/null", blob)


def export(store, cfg, out):
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(export_html(store, cfg))
    return str(out)
