#!/usr/bin/env python3
"""Local taxonomy review app server.

127.0.0.1 only, a random token on every /api call, the store opened read-only. It writes
decisions.jsonl and, for a health review's "Redo with a note…", appends to
taxonomy/work/requests.jsonl; nothing else. `serve` blocks until the reviewer submits or cancels (or the
timeout), then prints one JSON line — so an agent can run it in the background and be woken
by its exit.
"""
import copy
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decisions as D  # noqa: E402
import graph_migrate as GM  # noqa: E402
import metrics_gap as MG  # noqa: E402
import taxo_impact as TI  # noqa: E402
from taxo_io import intent, load_json, locate, nid, reviewer_name, sha256_file, utc_now  # noqa: E402
from taxo_ops import ChangesetError, validate as validate_ops  # noqa: E402

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review_ui.html")
EXIT = {"submitted": 0, "timeout": 3, "cancelled": 4}


class ReviewApp:
    def __init__(self, review_path, db_path=None, decisions_path=None, metrics_path=None, reviewer=None,
                 live_channel=False):
        self.review = load_json(review_path)
        self.tax_dir = os.path.dirname(os.path.dirname(os.path.abspath(review_path)))
        self.decisions_path = decisions_path or D.default_path(self.tax_dir)
        self.requests_path = os.path.join(self.tax_dir, "work", "requests.jsonl")
        self.responses_path = os.path.join(self.tax_dir, "work", "responses.jsonl")
        self.live_channel = bool(live_channel)
        base = self.review["base"]
        if sha256_file(base["path"]) != base["sha256"]:
            raise ValueError(f"{base['path']} changed since this review was planned; plan a new review")
        self.base_tax = load_json(base["path"])
        self.db = (sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True, check_same_thread=False)
                   if db_path else None)
        self.governed = MG.load_governed(metrics_path or MG.find_governed(self.tax_dir))
        self.reviewer = reviewer_name(reviewer)
        self.token = secrets.token_hex(16)
        self.lock = threading.Lock()
        self.ready, self.done = threading.Event(), threading.Event()
        self.url, self.outcome = None, None
        # Set under self.lock by the first terminal event (submit, cancel or timeout). Once set,
        # every write is refused, so the exit code always agrees with decisions.jsonl.
        self.closing = False

    # ---- reads ----
    def _records(self):
        return D.read(self.decisions_path)

    def _ops(self, records):
        return [e["op"] for e in D.effective_ops(self.review, records)]

    def _has(self, table):
        return self.db is not None and GM.has_table(self.db, table)

    @staticmethod
    def _read_jsonl(path):
        if not os.path.exists(path):
            return []
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
        return out

    def _requests(self):
        """The latest record per request id, filtered to this review, in first-seen (i.e.
        creation) order — updating an id's value never moves its position, so iterating this
        dict visits each item's requests oldest-to-newest."""
        latest = {}
        for rec in self._read_jsonl(self.requests_path):
            if rec.get("id"):
                latest[rec["id"]] = rec
        return {rid: rec for rid, rec in latest.items() if rec.get("review_id") == self.review["review_id"]}

    def _responses_by_request_id(self):
        """{request_id: latest response record}. `respond` always writes a `request_id`."""
        out = {}
        for rec in self._read_jsonl(self.responses_path):
            rid = rec.get("request_id")
            if rid:
                out[rid] = rec
        return out

    def _total_impact(self, records):
        try:
            return TI.impact(self.db, self.base_tax, self._ops(records))
        except ChangesetError as e:
            return {"errors": e.errors}

    def state(self):
        with self.lock:
            records = self._records()
            st = D.review_state(records, self.review["review_id"])
            counts, totals = {}, {}
            if self._has("chunk_topics"):
                counts = dict(self.db.execute(
                    "SELECT category_id, COUNT(DISTINCT chunk_id) FROM chunk_topics GROUP BY category_id"))
            cov = GM.chunk_coverage(self.db)
            if cov is not None:
                totals = {"chunks": cov["total"], "tagged": cov["tagged"], "untagged": cov["untagged"],
                          "no_topic": cov["no_topic"]}
            caps_path = os.path.join(self.tax_dir, "capabilities.json")
            caps = load_json(caps_path) if os.path.exists(caps_path) else None
            t = copy.deepcopy(self.base_tax)
            item_ids = {i["id"] for i in self.review["items"]}
            requests = self._requests()
            responses = self._responses_by_request_id()
            # A response record marks its OWN request as answered here, in memory only —
            # nothing on disk is ever rewritten. Matching by request_id (not item_id) means a
            # newer open request on the same item is never mistaken for one a stale response
            # already answered.
            requests = {rid: (dict(rec, status="answered") if rid in responses else rec)
                        for rid, rec in requests.items()}
            # revisions[item_id] is the response to that item's LATEST request only. An item
            # whose latest request has no response yet is omitted (the UI then shows "Claude
            # is revising…"/"Queued for next run"), even if an earlier request WAS answered.
            last_request_for_item = {}
            for rid, rec in requests.items():
                iid = rec.get("item_id")
                if iid in item_ids:
                    last_request_for_item[iid] = rid
            revisions = {iid: responses[rid] for iid, rid in last_request_for_item.items() if rid in responses}
            return {"review": self.review,
                    "base": {"intent_taxonomy": intent(t), "entities": list(t.get("entities") or {}),
                             "aliases": t.get("aliases") or {}, "version": t.get("version") or 0,
                             "descriptions": t.get("descriptions") or {}},
                    "metrics": MG.annotate(t.get("metrics") or [], self.governed),
                    "governed_file": bool(self.governed), "counts": counts, "totals": totals,
                    "decisions": st["latest"], "proposals": list(st["proposals"].values()),
                    "submitted": bool(st["submit"]), "reviewer": self.reviewer, "has_db": self.db is not None,
                    "capabilities": caps, "impact": self._total_impact(records),
                    "requests": requests, "revisions": revisions, "live_channel": self.live_channel}

    def node(self, label):
        level, parent = locate(copy.deepcopy(self.base_tax), label)
        if level is None:
            return {"errors": [f"{label!r} is not in the taxonomy"]}
        i, out = nid(label), {"label": label, "id": nid(label), "level": level, "parent": parent}
        out["description"] = (self.base_tax.get("descriptions") or {}).get(label)
        out["aliases"] = [a for a, c in (self.base_tax.get("aliases") or {}).items() if c == label]
        out["item"] = next((it for it in self.review["items"] if it["op"].get("node") == label), None)
        out["tags"], out["samples"], out["siblings"] = 0, [], []
        it = intent(copy.deepcopy(self.base_tax))
        sibs = (it["tree"].get(parent, []) if parent else list(it["tree"])) if level != "entity" else []
        with self.lock:
            if self._has("chunk_topics"):
                out["tags"] = self.db.execute("SELECT COUNT(DISTINCT chunk_id) FROM chunk_topics WHERE category_id=?",
                                              (i,)).fetchone()[0]
                out["samples"] = [
                    {"chunk_id": r[0], "source": r[1], "title": r[2], "preview": " ".join((r[3] or "").split())[:240]}
                    for r in self.db.execute(
                        "SELECT c.id, c.source, c.title, substr(c.text,1,400) FROM chunk_topics t JOIN chunks c "
                        "ON c.id=t.chunk_id WHERE t.category_id=? GROUP BY c.id ORDER BY c.id LIMIT 5", (i,))]
                for s in sibs:
                    if s == label:
                        continue
                    sid = nid(s)
                    tags = self.db.execute("SELECT COUNT(DISTINCT chunk_id) FROM chunk_topics WHERE category_id=?",
                                           (sid,)).fetchone()[0]
                    overlap = self.db.execute(
                        "SELECT COUNT(DISTINCT a.chunk_id) FROM chunk_topics a JOIN chunk_topics b "
                        "ON a.chunk_id=b.chunk_id WHERE a.category_id=? AND b.category_id=?", (i, sid)).fetchone()[0]
                    out["siblings"].append({"label": s, "tags": tags, "overlap": overlap})
            else:
                out["siblings"] = [{"label": s, "tags": 0, "overlap": 0} for s in sibs if s != label]
        return out

    def chunk(self, cid):
        if not self._has("chunks"):
            return 404, {"errors": ["no store attached (serve --db)"]}
        with self.lock:
            r = self.db.execute("SELECT id, source, title, text FROM chunks WHERE id=?", (cid,)).fetchone()
        if not r:
            return 404, {"errors": [f"chunk {cid} not found"]}
        return 200, {"chunk_id": r[0], "source": r[1], "title": r[2], "text": r[3]}

    # ---- writes (decisions.jsonl only) ----
    CLOSED = (409, {"errors": ["this review session is closed (submitted, cancelled or timed out); "
                               "nothing more is recorded"]})

    def _close(self, outcome):
        """Record the terminal outcome. Caller holds self.lock; the first outcome wins."""
        if self.closing:
            return False
        self.outcome, self.closing = outcome, True
        self.done.set()
        return True

    def impact_of(self, body):
        op = body.get("op") or {}
        with self.lock:
            prior = self._ops(self._records())
            errs = validate_ops(self.base_tax, prior + [op])
            if errs:
                return 400, {"errors": errs}
            return 200, {"impact": TI.delta(self.db, self.base_tax, prior, op)}

    def _chunk_errors(self, rec):
        """A tag op (a human proposal in browse mode, an edited amend) may only name chunks the
        store has — checked when the store is open; validate_record only checks they are ints."""
        op = rec.get("op")
        if not (isinstance(op, dict) and op.get("type") == "tag" and self._has("chunks")):
            return []
        ids = sorted(set(op.get("chunk_ids") or []))
        if not ids:
            return []
        q = ",".join("?" * len(ids))
        found = {r[0] for r in self.db.execute(f"SELECT id FROM chunks WHERE id IN ({q})", ids)}
        missing = [i for i in ids if i not in found]
        return [f"{rec.get('item_id')}: no such section(s) in the store: {missing[:10]}"] if missing else []

    def decide(self, body):
        if body.get("action") not in D.ITEM_ACTIONS:
            return 400, {"errors": [f"action must be one of {sorted(D.ITEM_ACTIONS)}, not {body.get('action')!r}"]}
        rec = {k: body[k] for k in ("action", "item_id", "op", "reason") if body.get(k) is not None}
        rec.update({"review_id": self.review["review_id"], "reviewer": self.reviewer, "surface": "browser"})
        if rec.get("action") == "propose" and not rec.get("item_id"):
            rec["item_id"] = D.new_human_id()
        item = next((i for i in self.review["items"] if i["id"] == rec.get("item_id")), None)
        if item and rec.get("action") in ("reject", "reopen"):
            rec["fingerprint"] = item["fingerprint"]
        with self.lock:
            if self.closing:
                return self.CLOSED
            records = self._records()
            errs = D.validate_record(self.review, self.base_tax, records, rec) or self._chunk_errors(rec)
            if errs:
                return 400, {"errors": errs}
            if rec["action"] == "propose":
                rec["impact"] = TI.delta(self.db, self.base_tax, self._ops(records), rec["op"])
            return 200, {"record": D.append(self.decisions_path, rec)}

    def decisions(self, body):
        """POST /api/decisions {"records":[...]}. All-or-nothing: validates every record in
        order against the review's records plus the accepted-so-far set, then appends all of
        them or none."""
        records_in = body.get("records")
        if not isinstance(records_in, list) or not records_in:
            return 400, {"errors": ["records must be a non-empty list"], "index": 0}
        with self.lock:
            if self.closing:
                return self.CLOSED
            accepted = self._records()
            prepared = []
            for idx, body_rec in enumerate(records_in):
                if not isinstance(body_rec, dict) or body_rec.get("action") not in D.ITEM_ACTIONS:
                    action = body_rec.get("action") if isinstance(body_rec, dict) else body_rec
                    return 400, {"errors": [f"action must be one of {sorted(D.ITEM_ACTIONS)}, not {action!r}"],
                                 "index": idx}
                rec = {k: body_rec[k] for k in ("action", "item_id", "op", "reason") if body_rec.get(k) is not None}
                rec.update({"review_id": self.review["review_id"], "reviewer": self.reviewer, "surface": "browser"})
                if rec.get("action") == "propose" and not rec.get("item_id"):
                    rec["item_id"] = D.new_human_id()
                item = next((i for i in self.review["items"] if i["id"] == rec.get("item_id")), None)
                if item and rec.get("action") in ("reject", "reopen"):
                    rec["fingerprint"] = item["fingerprint"]
                errs = D.validate_record(self.review, self.base_tax, accepted, rec) or self._chunk_errors(rec)
                if errs:
                    return 400, {"errors": errs, "index": idx}
                if rec["action"] == "propose":
                    rec["impact"] = TI.delta(self.db, self.base_tax, self._ops(accepted), rec["op"])
                accepted = accepted + [rec]
                prepared.append(rec)
            out = D.append_many(self.decisions_path, prepared)
            return 200, {"records": out}

    def request(self, body):
        """POST /api/request {"item_id","note"}. Requires an existing plan item and a
        non-empty note; refused (409) once the session is closed."""
        with self.lock:
            if self.closing:
                return self.CLOSED
            item_id = body.get("item_id")
            item = next((i for i in self.review["items"] if i["id"] == item_id), None)
            if item is None:
                return 400, {"errors": [f"{item_id!r} is not an item of this review"]}
            if self.review.get("mode") != "health" or item.get("origin") != "health":
                return 400, {"errors": ["redo requests exist only for health review items"]}
            if (item.get("support") or {}).get("pattern"):
                return 400, {"errors": [f"{item_id!r} is a naming pattern; it has no redo channel — "
                                        "change it in the taxonomy editor instead"]}
            note = (body.get("note") or "").strip()
            if not note:
                return 400, {"errors": ["a redo request needs a note"]}
            rec = {"id": "q-" + secrets.token_hex(8), "ts": utc_now(), "review_id": self.review["review_id"],
                   "item_id": item_id, "note": note, "status": "open"}
            os.makedirs(os.path.dirname(self.requests_path), exist_ok=True)
            line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
            fd = os.open(self.requests_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
            return 200, {"request": rec}

    def submit(self, body):
        with self.lock:
            if self.closing:
                return self.CLOSED
            records = self._records()
            rec = {"review_id": self.review["review_id"], "action": "submit", "reviewer": self.reviewer,
                   "surface": "browser"}
            errs = D.validate_record(self.review, self.base_tax, records, rec)
            if errs:
                return 400, {"errors": errs}
            rec["counts"] = D.submit_counts(self.review, records)
            rec["impact"] = self._total_impact(records)
            D.append(self.decisions_path, rec)
            outcome = {"status": "submitted", "review_id": self.review["review_id"], "counts": rec["counts"],
                       "impact": rec["impact"], "decisions": self.decisions_path}
            self._close(outcome)
        return 200, {"outcome": outcome}

    def cancel(self, body):
        with self.lock:
            if self.closing:
                return self.CLOSED
            outcome = {"status": "cancelled", "review_id": self.review["review_id"],
                       "decisions": self.decisions_path}
            self._close(outcome)
        return 200, {"outcome": outcome}

    def expire(self):
        """Close on timeout unless submit/cancel already won; returns the final outcome."""
        with self.lock:
            self._close({"status": "timeout", "review_id": self.review["review_id"],
                         "decisions": self.decisions_path})
            return self.outcome


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _authed(self):
            return secrets.compare_digest(self.headers.get("X-Review-Token", ""), app.token)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                with open(UI, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if not self._authed():
                return self._send(403, {"errors": ["missing or wrong review token"]})
            try:
                if u.path == "/api/state":
                    return self._send(200, app.state())
                if u.path.startswith("/api/node/"):
                    return self._send(200, app.node(unquote(u.path[len("/api/node/"):])))
                if u.path.startswith("/api/chunk/"):
                    code, payload = app.chunk(u.path[len("/api/chunk/"):])
                    return self._send(code, payload)
            except Exception as e:  # surface, never crash the server
                return self._send(500, {"errors": [str(e)]})
            return self._send(404, {"errors": ["not found"]})

        def do_POST(self):
            u = urlparse(self.path)
            if not self._authed():
                return self._send(403, {"errors": ["missing or wrong review token"]})
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"errors": ["request body is not JSON"]})
            routes = {"/api/impact": app.impact_of, "/api/decision": app.decide,
                      "/api/decisions": app.decisions, "/api/request": app.request,
                      "/api/submit": app.submit, "/api/cancel": app.cancel}
            fn = routes.get(u.path)
            if fn is None:
                return self._send(404, {"errors": ["not found"]})
            try:
                code, payload = fn(body)
            except (D.DecisionLogError, ChangesetError) as e:
                code, payload = 400, {"errors": getattr(e, "errors", [str(e)])}
            except Exception as e:
                code, payload = 500, {"errors": [str(e)]}
            return self._send(code, payload)

    return Handler


def serve(app, port=0, open_browser=True, timeout=3600, out=sys.stdout, err=sys.stderr):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    app.url = f"http://127.0.0.1:{httpd.server_address[1]}/?t={app.token}"
    print(f"review app: {app.url}", file=err, flush=True)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    app.ready.set()
    if open_browser:
        webbrowser.open(app.url)
    app.done.wait(timeout)
    outcome = app.expire()  # from here on every write is refused (409), so the outcome is final
    time.sleep(0.2)  # let the final response flush before shutting down
    httpd.shutdown()
    httpd.server_close()
    print(json.dumps(outcome, ensure_ascii=False), file=out, flush=True)
    return EXIT[outcome["status"]]
