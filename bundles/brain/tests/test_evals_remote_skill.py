import re
from pathlib import Path

SKILL = Path("bundles/brain/skills/evals-remote/SKILL.md")
RUNNER = Path("bundles/brain/skills/evals-remote/run_eval.sh")


def test_skill_description_starts_with_use_when():
    text = SKILL.read_text()
    m = re.search(r"^description:\s*(.+)$", text, re.M)
    assert m and m.group(1).lstrip().startswith("Use when ")


def test_orchestrator_is_executable_bash():
    sh = RUNNER.read_text()
    assert sh.startswith("#!/usr/bin/env bash")
    assert "baseline_reconcile.py" in sh and "consistency_diff.py" in sh


def test_no_brain_endpoint_or_client_host_in_shipped_files():
    # Reusability and data hygiene: no shipped file may name a real host. Only placeholders
    # (example domains, the config template's your-* host) and loopback are allowed.
    allowed = re.compile(r"^(localhost|127\.0\.0\.1|([a-z0-9-]+\.)*example\.(com|org)|your-[a-z0-9-]+|x|host|u)$")
    skill_dir = Path("bundles/brain/skills/evals-remote")
    for f in sorted(p for p in skill_dir.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        for host in re.findall(r"https?://([^/\s:'\"`)]+)", f.read_text(errors="ignore")):
            assert allowed.match(host), f"{f} names host {host!r}"
