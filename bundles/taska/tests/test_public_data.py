"""The repo is public, so taska carries invented data only: no real client, person or company domain, in code,
comments, docs or test data. Domains and e-mail addresses are checked everywhere; names come from a deny-list kept
outside the repo ($TASKA_PUBLIC_DENYLIST, default ~/.config/taska/public-denylist.txt), because listing them here
would publish them. Without that file the name check skips, saying so; the domain check always runs."""
import os
import re
from pathlib import Path

import pytest

BUNDLE = Path(__file__).resolve().parents[1]
DENYLIST = Path(os.environ.get("TASKA_PUBLIC_DENYLIST", "~/.config/taska/public-denylist.txt")).expanduser()

HOST = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|co|de|ch|ua|eu|uk|us)\b", re.I)
RESERVED = re.compile(r"(?:^|\.)(?:example\.(?:com|net|org))$", re.I)   # RFC 2606; *.example/.test/.invalid have no TLD above
SERVICES = {   # real services the code talks to or documents, never a client
    "github.com", "dev.azure.com", "graph.microsoft.com", "login.microsoftonline.com", "microsoft.com",
    "example.sharepoint.com", "sharepoint.com", "schemas.openxmlformats.org", "schemas.microsoft.com", "fonts.googleapis.com",
    "unpkg.com", "cdnjs.cloudflare.com", "www.apple.com", "www.w3.org", "purl.org",
}


def _texts():
    for p in sorted(BUNDLE.rglob("*")):
        rel = p.relative_to(BUNDLE).parts
        if not p.is_file() or "__pycache__" in rel or any(s.startswith(".") for s in rel) or p.name == Path(__file__).name:
            continue
        try:
            yield p, p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue   # binary


def _hits(pattern_for_line):
    out = []
    for p, text in _texts():
        for n, line in enumerate(text.splitlines(), 1):
            for bad in pattern_for_line(line):
                out.append(f"{p.relative_to(BUNDLE)}:{n}: {bad}")
    return out


def _bad_hosts(line):
    return [h for h in (m.group(0).lower() for m in HOST.finditer(line))
            if h not in SERVICES and not RESERVED.search(h)]


def test_only_reserved_or_service_domains():
    hits = _hits(_bad_hosts)
    assert not hits, "real domains in a public repo; use example.com / *.example instead:\n" + "\n".join(hits)


def test_no_denylisted_name():
    if not DENYLIST.is_file():
        pytest.skip(f"no deny-list at {DENYLIST}: client and person names not checked")
    names = [s.strip() for s in DENYLIST.read_text(encoding="utf-8").splitlines()
             if s.strip() and not s.lstrip().startswith("#")]
    word = re.compile(r"\b(?:" + "|".join(map(re.escape, names)) + r")\b", re.I)
    hits = _hits(lambda line: [m.group(0) for m in word.finditer(line)])
    assert not hits, "client or person names in a public repo; use invented ones:\n" + "\n".join(hits)


def test_rules_catch_what_they_guard():
    assert _bad_hosts('attendee_domains = ["acme-client.com"]') == ["acme-client.com"]
    assert _bad_hosts("mail ada@corp.co.uk") == ["corp.co.uk"]
    assert _bad_hosts('["client.example", "ada@example.com", "https://example.sharepoint.com/x"]') == []
    assert _bad_hosts("[sources.app]  # a TOML table, not a host") == []
