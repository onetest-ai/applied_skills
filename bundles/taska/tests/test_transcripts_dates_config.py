import io
import zipfile

import pytest

from taska_lib import config as C, transcripts as TR
from taska_lib.text import norm, resolve_due
from conftest import VTT_DAY1


def test_vtt_voice_tags_and_backchannel_stitch(tmp_path):
    p = tmp_path / "a.vtt"
    p.write_text(VTT_DAY1)
    turns = TR.stitch(TR.read_cues(p))
    assert [(t["speaker"], t["bc"]) for t in turns] == [("Nora Quill", False), ("Ravi Tamsin", False),
                                                        ("Opal Brennick1", False)]
    assert turns[0]["text"].startswith("Morning everyone") and "warehouse walkthrough" in turns[0]["text"]
    assert turns[1]["text"].startswith("Okay. I still can't log in")  # a short reply joined to a real sentence
    assert turns[0]["ts"] == "00:00:05"


def test_backchannel_between_one_speakers_sentence_is_dropped():
    cues = [{"start": 0, "speaker": "A", "text": "We should move the walkthrough to"},
            {"start": 2, "speaker": "B", "text": "Mm-hmm."},
            {"start": 3, "speaker": "A", "text": "Thursday."}]
    turns = TR.stitch(cues)
    assert [(t["speaker"], t["text"], t["bc"]) for t in turns] == [
        ("A", "We should move the walkthrough to Thursday.", False), ("B", "Mm-hmm.", True)]


def test_srt_and_name_prefix(tmp_path):
    p = tmp_path / "a.srt"
    p.write_text("1\n00:00:01,000 --> 00:00:04,000\nNora Quill: We ship the pilot on Friday.\n\n"
                 "2\n00:00:05,000 --> 00:00:07,000\nNote: this is not a speaker label at all.\n")
    cues = TR.read_cues(p)
    assert cues[0]["speaker"] == "Nora Quill" and cues[1]["speaker"] == ""


def test_unlabelled_asr_becomes_timed_paragraphs():
    cues = [{"start": i * 10.0, "speaker": "", "text": f"sentence {i}"} for i in range(20)]
    turns = TR.stitch(cues)
    assert len(turns) == 4 and turns[1]["ts"] == "00:01:00"


def _docx(paras):
    body = "".join("<w:p>" + "".join(f"<w:r><w:t>{r}</w:t></w:r>" for r in runs) + "</w:p>" for runs in paras)
    xml = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("word/document.xml", xml)
    return b.getvalue()


def test_teams_docx(tmp_path):
    p = tmp_path / "Weekly status.docx"
    p.write_bytes(_docx([["Weekly status"], ["March 5, 2026, 2:03PM"], ["32m 10s"],
                         ["Nora Quill started transcription"], ["Nora Quill", "0:03", "Let's review the plan."],
                         ["Opal Brennick", "1:12", "The data extract is coming next week."]]))
    turns = TR.stitch(TR.read_cues(p))
    assert [(t["speaker"], t["ts"]) for t in turns] == [("Nora Quill", "00:00:03"), ("Opal Brennick", "00:01:12")]
    assert TR.docx_title(p) == "Weekly status"


def test_truncated_docx_is_an_error_not_silence(tmp_path):
    p = tmp_path / "x.docx"
    p.write_bytes(b"PK\x03\x04truncated")
    with pytest.raises(ValueError):
        TR.read_cues(p)


@pytest.mark.parametrize("words,ref,want", [
    ("next Tuesday", "2026-03-02", "2026-03-10"),       # Monday: "next Tuesday" is next week's
    ("tomorrow", "2026-03-06", "2026-03-09"),           # Friday: next working day
    ("the 12th", "2026-03-20", "2026-04-12"),
    ("middle of next week", "2026-03-02", "2026-03-11"),
    ("end of week", "2026-03-03", "2026-03-06"),
    ("March 14", "2026-03-02", "2026-03-14"),
    ("Jan 5", "2026-12-10", "2027-01-05"),
    ("two to three weeks later", "2026-03-02", None),
])
def test_resolve_due(words, ref, want):
    assert resolve_due(words, ref) == want


def test_norm_folds_quotes_and_punctuation():
    assert norm("It’s  DONE, really!") == "it's done really"


def test_people_aliases_and_teams_suffix(project):
    cfg = C.load(project)
    assert cfg.canonical("Opal Brennick1") == "Opal Brennick"
    assert cfg.canonical("opal@harbor.example") == "Opal Brennick"
    assert cfg.side("Opal Brennick (External)") == "client"
    assert cfg.canonical("Somebody Else") == "Somebody Else"


def test_config_errors(tmp_path):
    (tmp_path / "taska.toml").write_text('[project]\nname = "X"\n[sources.a]\nadapter = "carrier_pigeon"\n')
    with pytest.raises(C.ConfigError):
        C.load(tmp_path)
    (tmp_path / "taska.toml").write_text('[project]\n')
    with pytest.raises(C.ConfigError):
        C.load(tmp_path)


def test_repetition_loop_is_detected():
    loop = [{"start": i, "speaker": "", "text": "Let's preview."} for i in range(30)] + \
           [{"start": 40, "speaker": "", "text": "Real words here."}]
    real = [{"start": i, "speaker": "", "text": f"sentence number {i}"} for i in range(30)]
    assert TR.repetition_loop(loop) and not TR.repetition_loop(real)
