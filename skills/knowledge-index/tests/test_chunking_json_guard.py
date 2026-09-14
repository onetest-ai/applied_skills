"""TDD red-phase tests for JSON guard in chunking.sections().

The JSON guard does NOT exist yet in chunking.sections() — tests must fail (red).
Run: python -m pytest skills/knowledge-index/tests/test_chunking_json_guard.py -v
"""
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from chunking import sections  # noqa: E402


def test_json_object_raises_value_error():
    """sections() must raise ValueError containing 'JSON' when given a JSON object string."""
    with pytest.raises(ValueError, match="JSON"):
        sections('{"key": "value"}')


def test_json_array_raises_value_error():
    """sections() must raise ValueError when given a JSON array string."""
    with pytest.raises(ValueError):
        sections("[1, 2, 3]")


def test_headed_markdown_works_fine():
    """sections() must return a list with at least 1 item for valid headed Markdown."""
    result = sections("## Heading\n\nBody text here.")
    assert isinstance(result, list)
    assert len(result) >= 1
