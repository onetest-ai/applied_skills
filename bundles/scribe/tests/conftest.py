"""Bundle-level fixtures for the scribe plugin's smoke tests.

Per the Agent Skills standard a skill directory ships only SKILL.md plus its
runtime resources; tests are development artifacts and live here at the bundle
root (matching bundles/kb/tests/, bundles/brain/tests/), not inside skills/run.

Puts skills/run on sys.path so a test can `import scribe` and
`from scribe_lib import config` directly.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "run"
TEMPLATES_DIR = RUN_SKILL_DIR / "templates"

if str(RUN_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(RUN_SKILL_DIR))

from scribe_lib import brain as brain_mod  # noqa: E402
from scribe_lib import parsing as parsing_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_brain_cache():
    """Every test resets `scribe_lib.brain`'s (and `parsing`'s) module-level
    caches before and after it runs, so no test can leak a monkeypatched
    `search`/`evidence` or a cached parse into another."""
    brain_mod.reset_cache()
    parsing_mod.reset_cache()
    yield
    brain_mod.reset_cache()
    parsing_mod.reset_cache()
