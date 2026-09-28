"""Discovery template library — every template in the library must parse,
declare queries on every non-diagram section, avoid client-specific wording,
and validate as a sample task instance (task 15)."""
import pytest
from scribe_fixtures import TEMPLATES_DIR, make_project, fixture_brain_db, write_text, load

LIBRARY = ["domain-profile", "subsystem-profile", "discovery-digest", "engagement-summary",
           "open-questions-decisions", "raid", "glossary", "ai-opportunity-map", "weekly-digest"]
CLIENTISH = ("primo", "five9", "epam", "bain")


@pytest.mark.parametrize("tid", LIBRARY)
def test_library_template_validates_with_a_sample_instance(tmp_path, tid):
    from scribe_lib.config import parse_frontmatter
    fm, body = parse_frontmatter(TEMPLATES_DIR / f"{tid}.tmpl.md")
    assert fm["id"] == tid and len(fm["output"]["sections"]) >= 3 and body.strip()
    assert all(s.get("queries") for s in fm["output"]["sections"] if s.get("kind") != "diagram")
    assert not any(w in (TEMPLATES_DIR / f"{tid}.tmpl.md").read_text().lower() for w in CLIENTISH)
    proj = make_project(tmp_path, brain_db=fixture_brain_db(tmp_path / "k.sqlite"))
    params = "\n".join(f"  {p}: " + ('["X"]' if p in ("tags", "aliases") else '"X"') for p in fm["params"])
    write_text(proj / "tasks" / "t.task.md",
               f"---\ntemplate: {tid}@{fm['version']}\nid: t\ntitle: \"T\"\nparams:\n{params}\nout: \"T\"\npublish: auto\n---\n")
    load(proj)   # validate_all must accept it
