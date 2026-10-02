"""Classify a brain source as keep / review / fail from a project-supplied scope policy.

The policy (exclude/keep/junk patterns, provenance cutoff) lives in the config, not in code,
so the same mechanism serves any brain. See evals_config.DEFAULTS for the schema.
"""
import re

_YEAR = re.compile(r"(20\d{2})")


def _year_before(text, cutoff):
    """Return the earliest 4-digit year in ``text`` that precedes ``cutoff`` (YYYY or YYYY-MM), else None."""
    try:
        cutoff_year = int(str(cutoff)[:4])
    except (ValueError, TypeError):
        return None
    years = [int(y) for y in _YEAR.findall(text or "")]
    before = [y for y in years if y < cutoff_year]
    return min(before) if before else None


def classify(source, folder, filename, modified, config):
    scope = (config or {}).get("scope", {})
    hay = f"{folder} {filename}"
    for pat in scope.get("exclude_patterns", []):
        if re.search(pat, hay, re.I):
            return {"scope": "fail", "reason": f"excluded by policy (matched /{pat}/)"}
    for pat in scope.get("junk_patterns", []):
        if re.search(pat, filename or "", re.I):
            return {"scope": "fail", "reason": "junk / scratch / duplicate file"}
    for pat in scope.get("keep_patterns", []):
        if re.search(pat, hay, re.I):
            return {"scope": "keep", "reason": "current-state operational/reference data"}
    cutoff = scope.get("provenance_cutoff") or ""
    if cutoff:
        yr = _year_before(filename, cutoff)
        if yr is not None:
            return {"scope": "fail", "reason": f"provenance year {yr} precedes cutoff {cutoff}"}
    return {"scope": "review", "reason": "unclassified — human ruling needed"}
