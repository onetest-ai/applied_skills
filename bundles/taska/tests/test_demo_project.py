"""The invented Harbor demo (evals/demo/build.sh) the runbook is shot on: it builds offline, with a tree, pending cards
and three schedules that are written to taska.toml only (never registered with launchd). Invented data only."""
import subprocess
import tomllib
from pathlib import Path

from taska_lib import config as C, ledger as L

BUILD = Path(__file__).resolve().parents[1] / "evals" / "demo" / "build.sh"


def test_demo_builds_offline(tmp_path):
    proj = tmp_path / "harbor"
    r = subprocess.run(["bash", str(BUILD), str(proj)], capture_output=True, text=True)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert (proj / "taska.toml").is_file()

    cfg = C.load(proj)
    N, *_ = L.fold(cfg.store)
    kinds = [n["kind"] for n in N.values()]
    assert kinds.count("epic") >= 1 and kinds.count("story") >= 2 and kinds.count("task") >= 6
    cards = [s for n in N.values() for s in n["suggestions"]]
    assert len(cards) >= 2

    assert cfg.name == "Harbor"
    assert N["T001"]["state"] == "in_progress"   # a verified progress status was applied
    assert N["T002"]["state"] != "done"           # a done is a Confirm card, never applied by the agent
    pending = [s for n in N.values() for s in n["suggestions"] if s["status"] == "pending"]
    assert sum(s["op"] == "propose" for s in pending) >= 2
    assert any(s["op"] == "status" and s["effect"] == "done" for s in pending)

    toml = tomllib.loads((proj / "taska.toml").read_text())
    assert {s["kind"] for s in toml["schedules"]} == {"preview", "send", "update"}
    assert all(s["on"] is False for s in toml["schedules"])
    addresses = [a for s in toml["schedules"] for a in s["to"] + s["cc"]] + [toml["report"]["owner"]]
    assert addresses and all(a.endswith("@example.com") for a in addresses)
    assert not (proj / "taska.toml.bak").exists()
