"""Central, brain-agnostic configuration for the remote-eval skill.

Nothing about any one brain is hardcoded in the scripts. A consuming project supplies a
JSON config (path via ``--config`` or the ``EVALS_CONFIG`` env var); everything project-
specific — endpoint, key env var, corpus inventory, scope policy — lives there. The
defaults below are universal (empty exclusion/keep policy, common file extensions, common
junk patterns), so with no config the mechanism is inert rather than brain-specific.
"""
import copy
import json
import os

# Universal defaults. Project-specific policy (exclude/keep patterns, cutoff, endpoint)
# is intentionally empty here and supplied per brain via the config file.
DEFAULTS = {
    "brain": {
        # url / key resolved from config first, then these env var names.
        "url": None,
        "url_env": "BRAIN_MCP_URL",
        "key_env": "BRAIN_API_KEY",
        "key_header": "X-API-Key",
    },
    "corpus": {
        "inventory_xlsx": None,
        "inventory_sheet": "Query",
        # Files that become narrative sources (search_knowledge) vs tabular marts (get_metric).
        "narrative_exts": [".pdf", ".pptx", ".docx", ".vtt", ".srt", ".loop", ".md"],
        "tabular_exts": [".xlsx", ".xlsm", ".xlsb", ".csv"],
        # Extra sweep seeds for finding superseded sources (the brain's own taxonomy and
        # metric names are always added). Domain words belong in the per-brain config.
        "seed_terms": [],
    },
    "scope": {
        # Empty by default: a brain with no policy has no exclusions (gate is inert).
        "provenance_cutoff": "",          # e.g. "2026-06"; empty disables the date rule
        "exclude_patterns": [],           # prior-engagement / out-of-scope markers (regex, case-insensitive)
        "keep_patterns": [],              # current-state operational/reference markers (regex)
        # Universally-junk artifacts, safe to default on for every brain.
        "junk_patterns": [r"tobedeleted", r"~\$", r"\.tmp$", r"\bcopy\b", r"^book\.xlsx$"],
    },
}


def _deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path=None):
    """Return the merged config dict. ``path`` overrides the ``EVALS_CONFIG`` env var.

    A missing path yields the universal defaults (no project specifics). A given-but-
    unreadable path is an error, not a silent fallback — a misconfigured run must fail loud.
    """
    path = path or os.environ.get("EVALS_CONFIG")
    if not path:
        return copy.deepcopy(DEFAULTS)
    with open(path, encoding="utf-8") as f:
        user = json.load(f)
    return _deep_merge(DEFAULTS, user)


def resolve_brain(config, url=None, key=None, key_header=None):
    """Resolve (url, key, key_header) from explicit args → config → env. Raises on missing key."""
    b = config.get("brain", {})
    url = url or b.get("url") or os.environ.get(b.get("url_env", "BRAIN_MCP_URL"))
    key_header = key_header or b.get("key_header") or "X-API-Key"
    if key is None:
        key = os.environ.get(b.get("key_env", "BRAIN_API_KEY"))
    if not url:
        raise ValueError("brain url not set: config brain.url or env "
                         f"{b.get('url_env', 'BRAIN_MCP_URL')}")
    # key is OPTIONAL — a local keyless brain sends no auth header. Set brain.key_env to ""
    # (or leave the env unset) for a local http://127.0.0.1 MCP.
    return url, (key or None), key_header
