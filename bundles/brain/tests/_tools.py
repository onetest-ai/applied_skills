"""Tests that need a system tool skip WITH A WARNING, never silently.

`require_tool` skips with a reason in one fixed format; conftest's
`pytest_terminal_summary` collects those reasons and prints a MISSING SYSTEM
TOOLS section at the end of every run, so "not verified on this machine" is
visible without -rs.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PREFIX = "MISSING TOOL: "
INSTALL = {
    "ffmpeg": "brew install ffmpeg",
    "ffprobe": "brew install ffmpeg",
    "whisper-cli": "brew install whisper-cpp + a model (brain_doctor.py whisper-models)",
    "soffice": "brew install --cask libreoffice",
}


def skip_reason(tool: str) -> str:
    return f"{PREFIX}{tool} (install: {INSTALL.get(tool, 'see brain_doctor.py')}; see brain_doctor.py)"


def require_tool(*tools: str):
    missing = [t for t in tools if not shutil.which(t)]
    return pytest.mark.skipif(bool(missing), reason=skip_reason(missing[0]) if missing else "")


def missing_tools_report(reasons: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    for r in reasons:
        i = r.find(PREFIX)
        if i < 0:
            continue
        tool = r[i + len(PREFIX):].split(" ", 1)[0]
        counts[tool] = counts.get(tool, 0) + 1
    if not counts:
        return []
    n = sum(counts.values())
    lines = [f"WARNING: {n} test{'s' if n != 1 else ''} skipped — "
             "these system-tool paths were NOT verified on this machine"]
    for tool, c in sorted(counts.items()):
        lines.append(f"  {tool:<12} {c} test{'s' if c != 1 else ' '}   install: "
                     f"{INSTALL.get(tool, 'see brain_doctor.py')}")
    return lines


def make_synthetic_video(path: Path, audio: bool = False) -> Path:
    """40 s, 640x360, 10 fps: bars | solid blue | noise | grey slide (10 s each)."""
    src = [
        "smptebars=s=640x360:d=10:r=10",
        "color=c=0x2050a0:s=640x360:d=10:r=10",
        "nullsrc=s=640x360:d=10:r=10,geq=random(1)*255:128:128",
        "color=c=0xe0e0e0:s=640x360:d=10:r=10,"
        "drawbox=x=40:y=40:w=560:h=60:color=black:t=fill,"
        "drawbox=x=40:y=140:w=300:h=20:color=black:t=fill",
    ]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for s in src:
        cmd += ["-f", "lavfi", "-i", s]
    graph = "[0][1][2][3]concat=n=4:v=1:a=0,format=yuv420p[v]"
    maps = ["-map", "[v]"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=40"]
        maps += ["-map", "4:a", "-c:a", "aac"]
    cmd += ["-filter_complex", graph, *maps, str(path)]
    subprocess.run(cmd, check=True)
    return path


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _run(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r>'


_AVATAR = ('<w:r><w:drawing><wp:inline><wp:positionH><wp:posOffset>576072</wp:posOffset></wp:positionH>'
           '<wp:positionV><wp:posOffset>914400</wp:posOffset></wp:positionV>'
           '<wp:extent cx="365760" cy="365760"/><wp:docPr id="7" name="Picture 7"/></wp:inline>'
           '</w:drawing></w:r>')


def make_teams_docx(path, title, duration_line, turns, events=("started",), avatar=True,
                    split_runs=True) -> Path:
    """A minimal Teams-shaped transcript .docx, synthetic (invented names only).

    Body: title, date line, duration line, a "<Name> started transcription" event, then
    one paragraph per turn — run[0] speaker (trailing space), run[1] timestamp, then text
    chunks separated by <w:br/> — each with an avatar <w:drawing> whose <wp:posOffset>
    digits must never reach the transcript. `turns` = [(speaker, "M:SS", "text" | [chunks])];
    a tuple speaker is written as several runs (a name split by Word).
    split_runs=False writes each turn as ONE run ("Speaker  0:13  text") for the fallback.
    """
    import zipfile
    host = ("".join(turns[0][0]) if isinstance(turns[0][0], (tuple, list)) else turns[0][0]) if turns else "Host"
    paras = [_run(title), _run("January 5, 2026, 3:04PM"), _run(duration_line)]
    body = [f"<w:p>{r}</w:p>" for r in paras]
    if "started" in events:
        body.append(f"<w:p>{_run(host + ' ')}{_run('started transcription')}</w:p>")
    for speaker, ts, text in turns:
        chunks = [text] if isinstance(text, str) else list(text)
        pic = _AVATAR if avatar else ""
        if isinstance(speaker, (tuple, list)):  # a speaker name split across several runs
            runs = "".join(_run(x) for x in speaker) + pic + _run(ts) + \
                '<w:r><w:br/></w:r>'.join(_run(c) for c in chunks)
        elif split_runs:
            runs = _run(speaker + " ") + pic + _run(ts) + \
                '<w:r><w:br/></w:r>'.join(_run(c) for c in chunks)
        else:
            runs = pic + _run(f"{speaker}  {ts}  " + " ".join(chunks))
        body.append(f"<w:p>{runs}</w:p>")
    if "stopped" in events:
        body.append(f"<w:p>{_run(host + ' ')}{_run('stopped transcription')}</w:p>")
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<w:document xmlns:w="{_W}" xmlns:wp="{_WP}"><w:body>{"".join(body)}</w:body></w:document>')
    ct = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
          'content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
          'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/'
          'document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.'
          'main+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return path


def truncate_file(path, keep: int = 200) -> Path:
    """Keep only the first `keep` bytes — a local header with no central directory,
    like an interrupted download."""
    path = Path(path)
    path.write_bytes(path.read_bytes()[:keep])
    return path
