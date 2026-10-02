import json

import evals_config as ec
import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    # Never let a real EVALS_CONFIG in the shell leak into these tests.
    monkeypatch.delenv("EVALS_CONFIG", raising=False)


def test_defaults_have_no_project_specifics():
    c = ec.load_config(None)
    assert c["scope"]["exclude_patterns"] == []
    assert c["scope"]["keep_patterns"] == []
    assert c["scope"]["provenance_cutoff"] == ""
    assert c["brain"]["url"] is None
    # universal junk defaults are present
    assert any("tobedeleted" in p for p in c["scope"]["junk_patterns"])


def test_config_file_overrides_and_merges(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({
        "brain": {"url": "https://x/mcp", "key_header": "X-Gateway-Key"},
        "scope": {"exclude_patterns": ["legacy ?suite"], "provenance_cutoff": "2026-06"},
    }))
    c = ec.load_config(str(p))
    assert c["brain"]["url"] == "https://x/mcp"
    assert c["brain"]["key_header"] == "X-Gateway-Key"
    # merge keeps default key_env while overriding header
    assert c["brain"]["key_env"] == "BRAIN_API_KEY"
    assert c["scope"]["exclude_patterns"] == ["legacy ?suite"]
    # untouched defaults survive the merge
    assert c["scope"]["junk_patterns"]


def test_missing_given_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ec.load_config(str(tmp_path / "nope.json"))


def test_resolve_brain_prefers_explicit_then_config_then_env(monkeypatch):
    monkeypatch.delenv("BRAIN_MCP_URL", raising=False)
    c = ec.load_config(None)
    c["brain"]["url"] = "https://cfg/mcp"
    url, key, header = ec.resolve_brain(c, key="K")
    assert url == "https://cfg/mcp" and key == "K" and header == "X-API-Key"


def test_resolve_brain_keyless_local(monkeypatch):
    # Local keyless brain: url required, key optional (returns None -> no auth header).
    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    c = ec.load_config(None)
    c["brain"]["url"] = "http://127.0.0.1:8001/mcp"
    url, key, header = ec.resolve_brain(c, key=None)
    assert url == "http://127.0.0.1:8001/mcp" and key is None


def test_resolve_brain_raises_without_url(monkeypatch):
    monkeypatch.delenv("BRAIN_MCP_URL", raising=False)
    c = ec.load_config(None)
    with pytest.raises(ValueError):
        ec.resolve_brain(c, key="K")
