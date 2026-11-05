"""The judge client: asks the local decision model about (message, task) pairs over HTTP. Fake server, invented data."""
import http.server
import json
import os
import sys
import tempfile
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../skills/run"))

from taska_lib import judge as J

SEEN = []


class _Fake(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append((self.path, body))
        ans = {q: {"type": "choice", "choice": "yes", "probabilities": {"yes": 0.7, "partial": 0.2, "no": 0.1}}
               for q in body["questions"]}
        out = json.dumps({"model": "kev-latest", "answers": ans}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def url():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1/systemone"
    srv.shutdown()


MSG = {"text": "Final stakeholder list is in the folder.", "ctx": "", "files": ["list.xlsx"], "from": "Sam Lee",
       "ts": "2026-10-21T16:42"}
TASK = {"id": "S001", "title": "Stakeholder contact list compiled", "ac": ["List shared with the client"], "parent_title": ""}


def test_returns_yes_and_partial_per_change(url):
    out = J.judge(MSG, TASK, ["done", "moved"], url=url)
    assert out == {"done": {"yes": 0.7, "partial": 0.2}, "moved": {"yes": 0.7, "partial": 0.2}}


def test_request_shape(url):
    SEEN.clear()
    J.judge(MSG, TASK, ["done"], url=url)
    path, body = SEEN[-1]
    assert path == "/v1/systemone"
    assert body["state"]["task"].startswith("S001: Stakeholder")
    assert body["state"]["message"] == MSG["text"] and body["state"]["files"] == ["list.xlsx"]
    q = body["questions"]["done"]
    assert q["type"] == "choice" and set(q["criteria"]) == {"yes", "partial", "no"}


def test_unavailable_server_fails_fast():
    with pytest.raises(J.JudgeUnavailable) as e:
        J.judge(MSG, TASK, ["done"], url="http://127.0.0.1:9/v1/systemone", timeout=1)
    assert "127.0.0.1:9" in str(e.value)


def test_unknown_change_is_refused(url):
    with pytest.raises(ValueError):
        J.judge(MSG, TASK, ["teleported"], url=url)


def _card(d, **kw):
    p = os.path.join(d, "card.json")
    json.dump({"model": "jaredpalmer/kev-4b", "propose_at": 0.75, **kw}, open(p, "w"))
    return p


def test_model_card_propose_threshold():
    with tempfile.TemporaryDirectory() as d:
        c = J.model_card(_card(d))
        assert c["propose_at"] == 0.75 and c["auto_apply_allowed"] is False


def test_auto_apply_needs_measured_lower_bound():
    with tempfile.TemporaryDirectory() as d:
        assert J.model_card(_card(d, test_precision_lb=0.96, auto_apply_at=0.9))["auto_apply_allowed"] is True
        assert J.model_card(_card(d, test_precision_lb=0.60, auto_apply_at=0.9))["auto_apply_allowed"] is False


def test_missing_model_card_fails_fast():
    with pytest.raises(J.JudgeUnavailable):
        J.model_card("/nonexistent/card.json")
