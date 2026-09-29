"""chunking.py ships in two skills (each skill is self-contained) and the copies must stay
byte-identical (CLAUDE.md), so a retrieval chunk is exactly the vault note a human sees.
conftest puts both skill dirs on sys.path and tests import whichever comes first, so
without this check a change to one copy would leave the other untested."""
from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / "skills"


def test_chunking_copies_are_byte_identical():
    a = (SKILLS / "knowledge-index" / "chunking.py").read_bytes()
    b = (SKILLS / "corpus-taxonomy-extraction" / "chunking.py").read_bytes()
    assert a == b, "chunking.py copies drifted: change knowledge-index/ and corpus-taxonomy-extraction/ together"
