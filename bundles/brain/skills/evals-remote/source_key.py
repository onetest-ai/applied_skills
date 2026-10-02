"""Map a brain `source` string to an inventory-comparable filename + folder.

A brain source is the parsed file name ``parse_corpus.py`` writes: the corpus-relative path
with every separator replaced by ``__``, plus ``.md``. So every component but the last is a
folder, and the folder is returned as a ``/``-joined path so scope patterns see all of it.
"""
from sharepoint_inventory import normalize


def _strip_md(source):
    return source[:-3] if source.endswith(".md") else source


def brain_source_to_filename(source):
    return _strip_md(source).split("__")[-1]


def brain_source_folder(source):
    return "/".join(_strip_md(source).split("__")[:-1])


def match_key(filename):
    return normalize(filename)
