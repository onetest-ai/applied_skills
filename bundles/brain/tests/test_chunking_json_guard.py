"""TDD red-phase tests for JSON guard in chunking.sections().

The JSON guard does NOT exist yet in chunking.sections() — tests must fail (red).
Run: python -m pytest bundles/brain/tests/test_chunking_json_guard.py -v
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


def test_json_array_of_objects_raises_value_error():
    """sections() must raise ValueError when given a JSON array-of-objects string.

    '[1, 2, 3]' is valid Markdown (list items) so it must NOT raise.  The guard
    only triggers for '[{' which is unambiguously a JSON array of objects.
    """
    with pytest.raises(ValueError, match="JSON"):
        sections('[{"key": "val"}, {"key2": "val2"}]')


def test_plain_list_markdown_does_not_raise():
    """A Markdown document starting with '[' must NOT raise ValueError.

    GFM table-of-contents directives, reference link definitions, and plain
    list items all start with '[' but are valid Markdown, not JSON.
    Bug 3: the original guard `startswith(('{', '['))` falsely rejected these.
    """
    result = sections("[TOC]\n\n## Heading\n\nBody text.")
    assert isinstance(result, list)
    assert len(result) >= 1

    result = sections("[reference link]: http://example.com\n\n## Heading\n\nBody.")
    assert isinstance(result, list)
    assert len(result) >= 1


def test_headed_markdown_works_fine():
    """sections() must return a list with at least 1 item for valid headed Markdown."""
    result = sections("## Heading\n\nBody text here.")
    assert isinstance(result, list)
    assert len(result) >= 1
