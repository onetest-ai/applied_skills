"""Bundle-level test fixtures for the kb plugin.

Tests live here at the bundle root (matching bundles/brain/tests/), not inside the skill
directories. Because some tests exercise the scripts a skill ships (findings_report.py,
fact_check_invariants.py), this conftest puts every skill's source directory on sys.path so a
test can ``import findings_report`` directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills"
for _d in sorted(_SKILLS_ROOT.iterdir()):
    if _d.is_dir():
        _p = str(_d)
        if _p not in sys.path:
            sys.path.insert(0, _p)
