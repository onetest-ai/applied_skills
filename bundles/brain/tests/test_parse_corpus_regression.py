"""Regression tests for parse_corpus.py bug fixes."""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


def test_vtt_single_digit_hour_cue_is_not_dropped(tmp_path):
    """VTT cues with 1-digit hours (e.g. 1:23:45.000) must not be dropped.

    Regression test for fix: timestamp discriminator regex changed from
    \\d{2}:\\d{2} to \\d{1,2}:\\d{2} to match WebVTT spec.
    """
    vtt = tmp_path / "test.vtt"
    vtt.write_text(
        "WEBVTT\n\n"
        "1:23:45.000 --> 1:23:50.000\n"
        "Hello from one-digit hour.\n",
        encoding="utf-8",
    )
    from parse_corpus import _parse_vtt
    result = _parse_vtt(str(vtt))
    assert "Hello from one-digit hour" in result, (
        f"Cue with 1-digit hour was silently dropped. result={result!r}"
    )


def test_json_skip_writes_manifest_entry(tmp_path):
    """A .json file without 'history' key must produce a 'skipped' manifest entry.

    Regression test for fix: parse_corpus.py main loop now writes
    {"source": rel, "skipped": True, "method": method} before continuing.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "config.json").write_text('{"key": "value"}', encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()

    from parse_corpus import main as parse_main
    parse_main([
        "--corpus", str(corpus),
        "--out", str(out),
        "--formats", "json",
    ])

    import json
    manifest = json.loads((out / "manifest.json").read_text())
    skipped = [m for m in manifest if m.get("skipped")]
    assert len(skipped) == 1, f"Expected 1 skipped entry, got {skipped}"
    assert skipped[0]["source"] == "config.json"
    assert skipped[0]["method"] == "ai-dial-json"
