"""Discovery template library — every template in the library must parse,
declare queries on every non-diagram section, avoid client-specific wording,
and validate as a sample task instance (task 15)."""
import os
from pathlib import Path

import pytest
from scribe_fixtures import TEMPLATES_DIR, make_project, fixture_brain_db, write_text, load

LIBRARY = ["domain-profile", "subsystem-profile", "discovery-digest", "engagement-summary",
           "open-questions-decisions", "raid", "glossary", "ai-opportunity-map", "weekly-digest"]
# The words a template must never contain (client/company names) are NOT listed in the
# repo — that would itself put project-specific names here (final-review I7). They come
# from SCRIBE_DENYLIST (comma-separated) or the gitignored tests/.denylist.local (one per
# line); with neither present the check is skipped.
DENYLIST_FILE = Path(__file__).resolve().parent / ".denylist.local"


def _denylist() -> tuple[str, ...]:
    raw = os.environ.get("SCRIBE_DENYLIST", "")
    words = [w for w in raw.split(",")]
    if DENYLIST_FILE.is_file():
        words += DENYLIST_FILE.read_text(encoding="utf-8").splitlines()
    return tuple(w.strip().lower() for w in words if w.strip())


@pytest.mark.parametrize("tid", LIBRARY)
def test_library_template_avoids_denylisted_words(tid):
    denylist = _denylist()
    if not denylist:
        pytest.skip("no SCRIBE_DENYLIST / tests/.denylist.local — client-name check skipped")
    text = (TEMPLATES_DIR / f"{tid}.tmpl.md").read_text().lower()
    assert not [w for w in denylist if w in text]


@pytest.mark.parametrize("tid", LIBRARY)
def test_library_template_validates_with_a_sample_instance(tmp_path, tid):
    from scribe_lib.config import parse_frontmatter
    fm, body = parse_frontmatter(TEMPLATES_DIR / f"{tid}.tmpl.md")
    assert fm["id"] == tid and len(fm["output"]["sections"]) >= 3 and body.strip()
    assert all(s.get("queries") for s in fm["output"]["sections"] if s.get("kind") != "diagram")
    proj = make_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    params = "\n".join(f"  {p}: " + ('["X"]' if p in ("tags", "aliases") else '"X"') for p in fm["params"])
    write_text(proj / "tasks" / "t.task.md",
               f"---\ntemplate: {tid}@{fm['version']}\nid: t\ntitle: \"T\"\nparams:\n{params}\nout: \"T\"\npublish: auto\n---\n")
    load(proj)   # validate_all must accept it


def test_scribe_bundle_contains_no_denylisted_words():
    """Nothing project-specific lives in the repo (CLAUDE.md): no file of the scribe
    bundle — tests included — may carry a denylisted client/company name."""
    denylist = _denylist()
    if not denylist:
        pytest.skip("no SCRIBE_DENYLIST / tests/.denylist.local — client-name check skipped")
    bundle = Path(__file__).resolve().parent.parent
    hits = []
    for path in bundle.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path == DENYLIST_FILE:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        hits += [f"{path.relative_to(bundle)}: {w}" for w in denylist if w in text]
    assert not hits
