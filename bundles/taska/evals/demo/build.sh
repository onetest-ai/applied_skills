#!/usr/bin/env bash
# Build the invented "Harbor" demo project that the runbook's screenshots and walkthrough use.
#
# Usage: build.sh OUT_DIR   (OUT_DIR must not exist or be empty)
#
# Offline and deterministic: two planted Harbor meetings (seeds 1 and 2) are synced and then the ops in ops.json are
# applied and verified by hand, so no model runs. The three [[schedules]] are written into the demo's own taska.toml
# (all off, addresses on example.com); `schedules --set/--register` is never called, so nothing reaches launchd.
set -euo pipefail

[ $# -eq 1 ] || { echo "usage: build.sh OUT_DIR" >&2; exit 2; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$HERE/../../skills/run"
P="$1"
if [ -e "$P" ] && [ -n "$(ls -A "$P")" ]; then echo "$P exists and is not empty" >&2; exit 1; fi
mkdir -p "$P/calls"
P="$(cd "$P" && pwd)"
taska() { python3 "$RUN/taska.py" --project "$P" "$@"; }

python3 - "$HERE" "$P" <<'PY'
import sys
from pathlib import Path
here, proj = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(here.parent / "planted"))
from generate import generate, vtt_name
for seed in (1, 2):
    (proj / "calls" / vtt_name(seed)).write_text(generate(seed)[0])
(proj / "epics.csv").write_text("name,start,end\nOnboarding,2026-03-01,2026-03-20\nDiscovery,2026-03-09,2026-04-03\n")
PY

taska init --name Harbor --timezone UTC >/dev/null
cat >> "$P/taska.toml" <<TOML

[[people]]
name = "Nora Quill"
side = "internal"
[[people]]
name = "Ravi Tamsin"
side = "internal"
[[people]]
name = "Opal Brennick"
side = "client"
[[teams]]
name = "Yard team"
members = ["Nora Quill", "Ravi Tamsin"]
aliases = ["yard guys", "the yard people"]
[sources.meetings]
adapter = "meetings_folder"
path = "calls"
TOML
python3 - "$P" <<'PY'
import re, sys
from pathlib import Path
p = Path(sys.argv[1]) / "taska.toml"
p.write_text(re.sub(r"(?m)^(\[plan\]\n)", r'\1file = "epics.csv"\n', p.read_text(), count=1))
PY

taska setup >/dev/null
taska sync >/dev/null

python3 - "$HERE" "$P" "$RUN" <<'PY'
import json, subprocess, sys
from pathlib import Path
here, proj, run = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
taska = lambda *a: json.loads(subprocess.run([sys.executable, f"{run}/taska.py", "--project", str(proj), *a],
                                             capture_output=True, text=True, check=True).stdout)
pending = taska("pending")["items"]
for m in json.loads((here / "ops.json").read_text())["meetings"]:
    stem = m["file"].removesuffix(".vtt")
    item = next((i for i in pending if stem in json.dumps(i)), None)
    if item is None:
        sys.exit(f"{m['file']}: no pending evidence for this meeting (is it in calls/ and synced?)")
    f = proj / ".taska" / "demo-ops.json"
    f.write_text(json.dumps(m["proposals"]))
    r = taska("apply", "--evidence", item["id"], "--proposals", str(f))
    if r.get("rejected"):
        sys.exit(f"{m['file']}: ops rejected: {r['rejected']}")
    f.unlink()
packets = proj / ".taska" / "demo-packets.json"
taska("verify-prep", "--out", str(packets))
verdicts = [{"mid": p["mid"], "verdict": "supports", "reason": "the quote says it"} for p in json.loads(packets.read_text())]
packets.write_text(json.dumps(verdicts))
taska("verify-apply", "--file", str(packets))
packets.unlink()

sys.path.insert(0, run)
from taska_lib import schedules as W
inc = dict(W.INCLUDE)
W.save(proj, [W.Schedule("Friday preview", "preview", (5,), "20:00", [], [], inc, False, tz="UTC"),
              W.Schedule("Monday send", "send", (1,), "14:00", ["reader@example.com"], ["lead@example.com"], inc, False, tz="UTC"),
              W.Schedule("Nightly update", "update", (1, 2, 3, 4, 5), "06:00", [], [], inc, False, tz="UTC")],
       owner="owner@example.com")
(proj / "taska.toml.bak").unlink()
PY
echo "Harbor demo project built at $P"
