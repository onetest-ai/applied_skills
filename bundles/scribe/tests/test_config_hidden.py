"""F1: scribe's raw lane ignores hidden files/dirs with the same rule as the Brain's
scribe_marker.is_hidden — implemented locally in scribe_lib.config (scribe must not
import brain modules at module level), pinned here so the two rules cannot drift
silently.

A path is hidden when any component (dir or file, relative to raw_root) starts with
"." (dot-files, dot-dirs, macOS AppleDouble ``._x``), or the file name starts with
"~$" (Office lock/owner files), or the OS marks it hidden.
"""
from __future__ import annotations

import stat
import tempfile
from pathlib import Path

import pytest

from scribe_lib.config import _is_hidden, raw_root_status, select_raw_files

from test_config_plan import _make_project, _write_raw


class TestIsHidden:
    def test_dot_file_is_hidden(self, tmp_path):
        p = tmp_path / ".DS_Store"
        p.write_bytes(b"x")
        assert _is_hidden(p, ".DS_Store") is True

    def test_appledouble_file_is_hidden(self, tmp_path):
        p = tmp_path / "._report.pdf"
        p.write_bytes(b"x")
        assert _is_hidden(p, "._report.pdf") is True

    def test_dot_dir_component_is_hidden(self, tmp_path):
        p = tmp_path / ".hidden" / "dir" / "a.pdf"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"x")
        assert _is_hidden(p, ".hidden/dir/a.pdf") is True

    def test_office_lock_file_is_hidden(self, tmp_path):
        p = tmp_path / "~$lock.docx"
        p.write_bytes(b"x")
        assert _is_hidden(p, "~$lock.docx") is True

    def test_normal_file_is_not_hidden(self, tmp_path):
        p = tmp_path / "real.pdf"
        p.write_bytes(b"x")
        assert _is_hidden(p, "real.pdf") is False

    def test_uf_hidden_flag_is_hidden(self, tmp_path):
        uf_hidden = getattr(stat, "UF_HIDDEN", None)
        if uf_hidden is None:
            pytest.skip("UF_HIDDEN unavailable on this platform")
        p = tmp_path / "flagged.pdf"
        p.write_bytes(b"x")
        import os
        try:
            os.chflags(p, uf_hidden)
        except (AttributeError, OSError):
            pytest.skip("chflags(UF_HIDDEN) unavailable on this platform")
        assert _is_hidden(p, "flagged.pdf") is True

    def test_never_raises_on_missing_file(self, tmp_path):
        missing = tmp_path / "gone.pdf"
        assert _is_hidden(missing, "gone.pdf") is False


def test_select_raw_files_skips_hidden_file_and_dir(tmp_path):
    proj = _make_project(tmp_path)
    raw_root = proj / "raw-replay"
    _write_raw(raw_root, "real.txt", "Real content.")
    _write_raw(raw_root, ".DS_Store", "junk")
    _write_raw(raw_root, ".hidden/dir/a.txt", "Hidden dir content.")
    _write_raw(raw_root, "~$lock.txt", "lock file")
    from scribe_lib.config import load_config
    config = load_config(proj)
    selected = select_raw_files(config, {"globs": ["**/*"], "exclude": [], "match": []})
    rels = sorted(p.relative_to(config.raw_root).as_posix() for p in selected)
    assert rels == ["real.txt"]


def test_raw_root_status_with_only_hidden_files_reads_empty_when_previously_nonempty(tmp_path):
    proj = _make_project(tmp_path)
    raw_root = proj / "raw-replay"
    from scribe_lib.config import load_config, raw_snapshot

    config = load_config(proj)
    # A prior, non-empty snapshot (real file existed before).
    _write_raw(raw_root, "was-here.txt", "content")
    raw_inputs = {"globs": ["**/*"], "exclude": [], "match": []}
    prior = raw_snapshot(config, raw_inputs)
    assert prior

    # Write that prior snapshot into a task's state.json so _any_prior_raw_snapshot sees it.
    state_dir = proj / "out" / "t1" / "_src"
    state_dir.mkdir(parents=True)
    import json
    (state_dir / "state.json").write_text(json.dumps({"raw_snapshot": prior}), encoding="utf-8")

    # Now only hidden files remain.
    (raw_root / "was-here.txt").unlink()
    _write_raw(raw_root, ".DS_Store", "junk")
    _write_raw(raw_root, "._x", "junk")

    assert raw_root_status(config) == "empty"
