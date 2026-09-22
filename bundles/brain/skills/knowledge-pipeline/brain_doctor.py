#!/usr/bin/env python3
"""Brain doctor — does this machine have what THIS Brain's corpus needs?

Reports only; never installs anything (installing system packages is the user's
call). Corpus-aware: with --config/--corpus it scans the formats actually present,
so a PDF-only Brain is never told to install whisper; without them every system
tool is reported as optional.

Usage:
  brain_doctor.py [--config brain.toml | --corpus DIR] [--json] [--need video,office,evals]
  brain_doctor.py whisper-models [--config brain.toml] [--json]
  brain_doctor.py set-whisper-model --config brain.toml --model PATH [--language auto|en|…]

Exit: 0 every required item present · 1 a required item missing · 2 config unreadable.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_registry  # noqa: E402  (same skill directory)

VIDEO_EXT = frozenset({".mp4", ".mov", ".mkv", ".webm", ".m4v"})
SIDECAR_EXT = (".vtt", ".srt", ".docx")  # priority order, as video_capture.find_sidecar
OFFICE_EXT = frozenset({".pptx", ".ppt", ".docx", ".doc"})
# Pinned by test to render_pages.soffice_bin / parse_corpus._soffice.
SOFFICE_CANDIDATES = ("soffice", "libreoffice", "/opt/homebrew/bin/soffice",
                      "/Applications/LibreOffice.app/Contents/MacOS/soffice")
WHISPER_BINS = ("whisper-cli", "whisper-cpp")
PY_DEPS = (("fitz", "pymupdf"), ("fastembed", "fastembed"), ("sqlite_vec", "sqlite-vec"),
           ("pandas", "pandas"), ("pyarrow", "pyarrow"), ("openpyxl", "openpyxl"),
           ("fastmcp", "fastmcp"))
SHARED_MODEL_DIR = Path.home() / ".cache" / "brain" / "whisper"
INSTALL = {
    "soffice": {"brew": "brew install --cask libreoffice",
                "apt": "sudo apt-get install -y libreoffice-impress libreoffice-writer",
                "winget": "winget install TheDocumentFoundation.LibreOffice"},
    "ffmpeg": {"brew": "brew install ffmpeg", "apt": "sudo apt-get install -y ffmpeg",
               "winget": "winget install Gyan.FFmpeg"},
    "whisper-cli": {"brew": "brew install whisper-cpp",
                    "apt": "build whisper.cpp: https://github.com/ggml-org/whisper.cpp "
                           "(cmake -B build && cmake --build build -j; put build/bin/whisper-cli on PATH)",
                    "winget": "download a release: https://github.com/ggml-org/whisper.cpp/releases"},
    "node": {"brew": "brew install node", "apt": "sudo apt-get install -y nodejs npm",
             "winget": "winget install OpenJS.NodeJS"},
}

which = shutil.which  # patch point for tests


def os_key() -> str:
    if sys.platform == "darwin":
        return "brew"
    return "winget" if sys.platform.startswith("win") else "apt"


def install_hint(tool: str) -> str:
    return INSTALL[tool][os_key()]


_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# Copies of video_capture's Teams-transcript patterns — skills cannot import across skill
# dirs — pinned by the parity test in test_brain_doctor.py.
_DOCX_TS = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
_DOCX_TURN = re.compile(r"^(.+?)\s{2,}(\d{1,2}:\d{2}(?::\d{2})?)(.*)$", re.S)
_DOCX_EVENTS = ("started transcription", "stopped transcription")
_DOCX_DURATION = re.compile(r"^\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?\s*$")


def _docx_paragraphs(path) -> list[list[str]] | None:
    """Per body paragraph, the text of each run with <w:t> (only <w:t> directly inside
    <w:r>; <w:br/>/<w:tab/> -> " "); None when the file is not a readable Word file."""
    import xml.etree.ElementTree as ET
    import zipfile
    import zlib
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("word/document.xml"))
    except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError, EOFError, zlib.error):
        return None
    paras = []
    for p in root.iter(_W_NS + "p"):
        runs = []
        for r in p.findall(_W_NS + "r"):
            if r.find(_W_NS + "t") is None:
                continue
            parts = []
            for child in r:
                if child.tag == _W_NS + "t":
                    parts.append(child.text or "")
                elif child.tag in (_W_NS + "br", _W_NS + "tab"):
                    parts.append(" ")
            runs.append("".join(parts))
        paras.append(runs)
    return paras


def _docx_title(path) -> str | None:
    """First non-empty paragraph, stripped; None when unreadable (= video_capture.docx_title)."""
    for runs in _docx_paragraphs(path) or []:
        t = "".join(runs).strip()
        if t:
            return t
    return None


def _docx_has_turns(path) -> bool:
    """True when video_capture.read_teams_docx would yield at least one turn."""
    paras = [r for r in (_docx_paragraphs(path) or []) if "".join(r).strip()]
    if len(paras) >= 3:
        m = _DOCX_DURATION.match("".join(paras[2]))
        if m and any(m.groups()):
            paras = paras[3:]
    for runs in paras:
        joined = "".join(runs)
        split = len(runs) > 1 and _DOCX_TS.match(runs[1].strip())
        if joined.strip().lower().endswith(_DOCX_EVENTS) and not split:
            continue
        if split:
            text = " ".join(t.strip() for t in runs[2:] if t.strip())
        else:
            m = _DOCX_TURN.match(joined.strip())
            if not m:
                continue
            text = m.group(3)
        if text.strip():
            return True
    return False


def title_claims(video: Path) -> list[str]:
    """Names of the .docx files next to the video whose first paragraph is its stem."""
    try:
        names = sorted(os.listdir(video.parent))
    except OSError:
        return []
    return [n for n in names if os.path.splitext(n)[1].lower() == ".docx"
            and not n.startswith((".", "~$")) and _docx_title(video.parent / n) == video.stem]


def sidecar_of(video: Path) -> Path | None:
    """The transcript video_capture.find_sidecar would pick: a same-stem .vtt/.srt/.docx
    (in that priority, extension case-insensitive; a .docx only if it reads as a Teams
    transcript), else the ONE .docx whose first paragraph is the video's stem. Two title
    claims -> None (probe refuses; run_checks names the conflict)."""
    try:
        names = sorted(os.listdir(video.parent))
    except OSError:
        return None
    for ext in SIDECAR_EXT:
        for n in names:
            if os.path.splitext(n)[0] == video.stem and os.path.splitext(n)[1].lower() == ext:
                if ext == ".docx" and not _docx_has_turns(video.parent / n):
                    continue
                return video.parent / n
    claims = title_claims(video)
    return video.parent / claims[0] if len(claims) == 1 else None


def has_sidecar(video: Path) -> bool:
    """The video has a transcript the video lane will use (see sidecar_of)."""
    return sidecar_of(video) is not None


def scan_corpus(config: str | None = None, corpus: str | None = None) -> dict:
    files: list[Path] = []
    gaps: list[str] = []
    if config:
        cfg = source_registry.load_config(Path(config))
        for key, r in cfg["roots"].items():
            root = r["path"]
            if not root.is_dir():
                continue
            included = set(source_registry.iter_files(root, r["include"]).values())
            files += sorted(included)
            for p in sorted(root.rglob("*")):
                if p.is_file() and p.suffix.lower() in VIDEO_EXT and p.resolve() not in included:
                    gaps.append(f"{key}:{p.relative_to(root).as_posix()}")
    elif corpus:
        files = sorted(p for p in Path(corpus).rglob("*") if p.is_file() and not p.name.startswith("."))
    return {"files": files, "include_gaps": gaps}


def configured_video(config: str | None) -> dict:
    out = {"whisper_model": None, "language": "auto"}
    if not config:
        return out
    data = tomllib.loads(Path(config).read_text(encoding="utf-8"))
    video = data.get("video", {}) if isinstance(data.get("video"), dict) else {}
    model = video.get("whisper_model")
    if model:
        p = Path(os.path.expanduser(str(model)))
        out["whisper_model"] = str(p if p.is_absolute() else Path(config).resolve().parent / p)
    out["language"] = str(video.get("language") or "auto")
    return out


def check_python_deps() -> tuple[bool, str]:
    missing = [pip for mod, pip in PY_DEPS if importlib.util.find_spec(mod) is None]
    return (not missing), ("all importable" if not missing else "missing: " + ", ".join(missing))


def check_sqlite_vec() -> tuple[bool, str]:
    con = sqlite3.connect(":memory:")
    try:
        if not hasattr(con, "enable_load_extension"):
            return False, "this Python's sqlite3 cannot load extensions"
        try:
            import sqlite_vec
        except ImportError:
            return False, "sqlite-vec not installed"
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        return True, "vec " + con.execute("select vec_version()").fetchone()[0]
    except Exception as e:  # any load failure IS the finding
        return False, f"cannot load sqlite-vec: {e}"
    finally:
        con.close()


def soffice_path() -> str | None:
    for c in SOFFICE_CANDIDATES:
        if which(c) or os.path.exists(c):
            return c
    return None


def whisper_bin() -> str | None:
    for b in WHISPER_BINS:
        if which(b):
            return which(b)
    return None


def _eg(paths: list[Path]) -> str:
    return ", ".join(p.name for p in paths[:2])


def run_checks(scan: dict, *, config: str | None = None, need: tuple[str, ...] = ()) -> list[dict]:
    files = scan["files"]
    videos = [p for p in files if p.suffix.lower() in VIDEO_EXT]
    sidecars = {v: sidecar_of(v) for v in videos}
    bare = [v for v in videos if sidecars[v] is None]
    # A .docx the video lane reads as a transcript is never converted by soffice.
    transcripts = {s.resolve() for s in sidecars.values() if s is not None}
    office = [p for p in files if p.suffix.lower() in OFFICE_EXT and p.resolve() not in transcripts]
    checks: list[dict] = []

    def add(name, ok, required, why, detail="", install=None):
        checks.append({"name": name, "ok": bool(ok), "required": bool(required), "why": why,
                       "detail": detail or "", "install": None if ok else install})

    ok, detail = check_python_deps()
    add("python deps", ok, True, "every brain script", detail, "./install.sh --bundle brain --deps")
    ok, detail = check_sqlite_vec()
    add("sqlite-vec", ok, True, "vector index (knowledge-index)", detail,
        "./install.sh --bundle brain --deps  (a uv-managed Python whose sqlite3 loads extensions)")
    so = soffice_path()
    add("soffice", so, bool(office) or "office" in need,
        f"{len(office)} Office file(s), e.g. {_eg(office)}" if office else "only for .pptx/.docx",
        so or "", install_hint("soffice"))
    ff = which("ffmpeg") and which("ffprobe")
    add("ffmpeg", ff, bool(videos) or "video" in need,
        f"{len(videos)} video file(s), e.g. {_eg(videos)}" if videos else "only for meeting recordings",
        ff or "", install_hint("ffmpeg"))
    wb = whisper_bin()
    model = configured_video(config)["whisper_model"]
    model_ok = bool(model) and os.path.isfile(model)
    w_detail = f"binary: {wb or 'missing'}; model: {model or 'not configured'}"
    if model and not model_ok:
        w_detail += " (file not found)"
    conflicts = [(v, c) for v in bare if len(c := title_claims(v)) > 1]
    for v, c in conflicts:
        w_detail += (f"; transcript conflict: {v.name} is claimed by {', '.join(c)} — pick one with "
                     "video_capture.py probe --transcript-file")
    w_install = (install_hint("whisper-cli") + "; " if not wb else "") + \
        ("" if model_ok else "choose a model: brain_doctor.py whisper-models, then set-whisper-model")
    add("whisper-cli", wb and model_ok, bool(bare),
        f"{len(bare)} of {len(videos)} video(s) have no transcript sidecar" if bare
        else "only for videos without a transcript (.vtt/.srt/.docx sidecar)", w_detail, w_install)
    nd = which("node") and which("npx")
    add("node", nd, "evals" in need, "evals skill (promptfoo)", nd or "", install_hint("node"))
    return checks


def exit_code(checks: list[dict]) -> int:
    return 1 if any(c["required"] and not c["ok"] for c in checks) else 0


def format_report(checks: list[dict], gaps=()) -> str:
    out = ["== brain doctor =="]
    for c in checks:
        tag = "ok" if c["ok"] else ("MISSING" if c["required"] else "--")
        level = "REQUIRED" if c["required"] else "optional"
        out.append(f"  [{tag:<7}] {c['name']:<12} {level} — {c['why']}"
                   + (f"  ({c['detail']})" if c["detail"] else ""))
        if c["install"]:
            out.append(f"  {'':9} {'':12} install: {c['install']}")
    gaps = list(gaps)
    if gaps:
        out.append(f"  ⚠ {len(gaps)} video file(s) not matched by any include glob — add "
                   '"**/*.mp4", "**/*.mov", "**/*.mkv", "**/*.webm", "**/*.m4v" to that root\'s '
                   "include in brain.toml to ingest them: " + ", ".join(gaps[:5])
                   + (" …" if len(gaps) > 5 else ""))
    return "\n".join(out)


def cmd_check(a) -> int:
    try:
        scan = scan_corpus(a.config, a.corpus)
        checks = run_checks(scan, config=a.config,
                            need=tuple(x.strip() for x in (a.need or "").split(",") if x.strip()))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
        print(f"error: cannot read config: {e}", file=sys.stderr)
        return 2
    code = exit_code(checks)
    if a.json:
        print(json.dumps({"ok": code == 0, "checks": checks, "include_gaps": scan["include_gaps"]}, indent=2))
    else:
        print(format_report(checks, scan["include_gaps"]))
    return code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config"); ap.add_argument("--corpus"); ap.add_argument("--json", action="store_true")
    ap.add_argument("--need", help="force requirements: comma list of video,office,evals")
    sub = ap.add_subparsers(dest="cmd")
    add_whisper_subcommands(sub)
    a = ap.parse_args(argv)
    return a.func(a) if getattr(a, "func", None) else cmd_check(a)


HF_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
CATALOGUE = [
    {"name": "tiny.en", "file": "ggml-tiny.en.bin", "size": "75 MB", "lang": "English", "note": "fastest, rough", "recommended": False},
    {"name": "tiny", "file": "ggml-tiny.bin", "size": "75 MB", "lang": "multilingual", "note": "fastest, rough", "recommended": False},
    {"name": "base.en", "file": "ggml-base.en.bin", "size": "142 MB", "lang": "English", "note": "fast", "recommended": False},
    {"name": "base", "file": "ggml-base.bin", "size": "142 MB", "lang": "multilingual", "note": "fast", "recommended": False},
    {"name": "small.en", "file": "ggml-small.en.bin", "size": "466 MB", "lang": "English", "note": "good for meetings, ~real time on CPU", "recommended": True},
    {"name": "small", "file": "ggml-small.bin", "size": "466 MB", "lang": "multilingual", "note": "good for meetings, ~real time on CPU", "recommended": True},
    {"name": "medium.en", "file": "ggml-medium.en.bin", "size": "1.5 GB", "lang": "English", "note": "better, slow on CPU", "recommended": False},
    {"name": "medium", "file": "ggml-medium.bin", "size": "1.5 GB", "lang": "multilingual", "note": "better, slow on CPU", "recommended": False},
    {"name": "large-v3-turbo", "file": "ggml-large-v3-turbo.bin", "size": "1.6 GB", "lang": "multilingual", "note": "best quality; fast only with Metal/GPU", "recommended": False},
]


def model_dirs(project: Path | None = None) -> list[Path]:
    dirs: list[Path] = []
    brew = which("brew")
    if brew:
        try:
            prefix = subprocess.run([brew, "--prefix"], capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            prefix = ""
        if prefix:
            dirs.append(Path(prefix) / "share" / "whisper-cpp")
    home = Path.home()
    dirs += sorted((home / ".cache").glob("whisper*"))
    dirs += [home / "whisper.cpp" / "models", SHARED_MODEL_DIR]
    if project:
        dirs.append(Path(project) / "models")
    return dirs


def find_models(dirs) -> list[dict]:
    seen, out = set(), []
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for p in sorted(d.glob("ggml-*.bin")):
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            out.append({"path": str(p), "file": p.name, "bytes": p.stat().st_size})
    return out


def download_command(entry: dict) -> str:
    dest = SHARED_MODEL_DIR / entry["file"]
    return f'mkdir -p "{SHARED_MODEL_DIR}" && curl -L --fail -o "{dest}" "{HF_BASE}{entry["file"]}"'


def set_video_keys(text: str, updates: dict[str, str]) -> str:
    """Set keys under [video], touching no other byte of a hand-edited brain.toml."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.strip() == "[video]"), None)
    if start is None:
        head = text if (not text or text.endswith("\n")) else text + "\n"
        return head + ("\n" if head else "") + "[video]\n" + \
            "".join(f"{k} = {json.dumps(v)}\n" for k, v in updates.items())
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    pending = dict(updates)
    for i in range(start + 1, end):
        m = re.match(r"\s*([A-Za-z0-9_-]+)\s*=", lines[i])
        if m and m.group(1) in pending:
            lines[i] = f"{m.group(1)} = {json.dumps(pending.pop(m.group(1)))}\n"
    j = end
    while j > start + 1 and not lines[j - 1].strip():
        j -= 1
    lines[j:j] = [f"{k} = {json.dumps(v)}\n" for k, v in pending.items()]
    return "".join(lines)


def cmd_whisper_models(a) -> int:
    try:
        project = Path(a.config).resolve().parent if a.config else None
        found = find_models(model_dirs(project))
        current = configured_video(a.config)["whisper_model"] if a.config else None
    except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
        print(f"error: cannot read config: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps({"configured": current, "installed": found,
                          "catalogue": [{**e, "url": HF_BASE + e["file"], "download": download_command(e)}
                                        for e in CATALOGUE]}, indent=2))
        return 0
    print(f"configured: {current or 'none'}")
    print("installed models:" if found else "installed models: none found")
    for m in found:
        print(f"  {m['file']:<28} {m['bytes'] / 1e6:>7.0f} MB  {m['path']}")
    print("downloadable (whisper.cpp ggml models):")
    for e in CATALOGUE:
        star = "  ← recommended" if e["recommended"] else ""
        print(f"  {e['name']:<15} {e['size']:>7}  {e['lang']:<12} {e['note']}{star}")
        print(f"      {download_command(e)}")
    print("then: brain_doctor.py set-whisper-model --config brain.toml --model <path> [--language en]")
    return 0


def cmd_set_whisper_model(a) -> int:
    if not getattr(a, "config", None):
        print("error: set-whisper-model needs --config brain.toml", file=sys.stderr)
        return 2
    model = Path(os.path.expanduser(a.model))
    if not model.is_file():
        print(f"error: model file not found: {model}", file=sys.stderr)
        return 1
    model = model.resolve()  # brain.toml resolves relative paths against ITS dir, not the CWD
    cfg = Path(a.config)
    try:
        text = cfg.read_text(encoding="utf-8")
        new = set_video_keys(text, {"whisper_model": str(model), "language": a.language})
        tomllib.loads(new)  # never write a config we cannot read back
    except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
        print(f"error: cannot read config: {e}", file=sys.stderr)
        return 2
    tmp = cfg.with_suffix(cfg.suffix + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, cfg)
    print(f"[video] whisper_model = {model}  language = {a.language}  -> {cfg}")
    return 0


def add_whisper_subcommands(sub) -> None:
    w = sub.add_parser("whisper-models", help="list whisper.cpp models on disk and downloadable ones")
    # SUPPRESS: an absent subcommand flag must not overwrite the top-level value
    # (`brain_doctor.py --config X whisper-models` keeps X).
    w.add_argument("--config", default=argparse.SUPPRESS)
    w.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    w.set_defaults(func=cmd_whisper_models)
    s = sub.add_parser("set-whisper-model", help="record the chosen model in brain.toml [video]")
    s.add_argument("--config", default=argparse.SUPPRESS, help="brain.toml (here or before the subcommand)")
    s.add_argument("--model", required=True)
    s.add_argument("--language", default="auto", help="auto, or an ISO code such as en")
    s.set_defaults(func=cmd_set_whisper_model)


if __name__ == "__main__":
    sys.exit(main())
