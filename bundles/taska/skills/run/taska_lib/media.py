"""Recordings (video or audio) -> WebVTT via ffmpeg + whisper.cpp. Used only when no transcript came with them."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v")
AUDIO_EXT = (".m4a", ".mp3", ".wav", ".aac", ".ogg", ".opus", ".flac", ".wma")
MEDIA_EXT = VIDEO_EXT + AUDIO_EXT
WHISPER_BINS = ("whisper-cli", "whisper-cpp", "main")


class MediaError(RuntimeError):
    pass


def tools():
    """Which external tools are on PATH (doctor reports this; nothing is installed)."""
    return {"ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
            "whisper": next((shutil.which(b) for b in WHISPER_BINS if shutil.which(b)), None)}


MODEL_DIRS = ("~/.cache/taska/whisper", "~/.cache/brain/whisper", "~/models", "~/.cache/whisper")


def whisper_model(media_cfg):
    """[media].whisper_model, else the largest ggml model in the usual caches (brain's included)."""
    if media_cfg.get("whisper_model"):
        return os.path.expanduser(media_cfg["whisper_model"])
    found = [os.path.join(d, f) for d in map(os.path.expanduser, MODEL_DIRS) if os.path.isdir(d)
             for f in os.listdir(d) if f.startswith("ggml-") and f.endswith(".bin")]
    return max(found, key=os.path.getsize) if found else None


def probe(path):
    """Duration (s), whether it has audio/video, and the recording's creation time when the container has one."""
    if not shutil.which("ffprobe"):
        raise MediaError("ffprobe not found on PATH")
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                             capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise MediaError("ffprobe timed out (corrupt or unreadable recording)")
    if out.returncode:
        raise MediaError(out.stderr.strip()[:300])
    j = json.loads(out.stdout or "{}")
    kinds = {s.get("codec_type") for s in j.get("streams", [])}
    fmt = j.get("format", {})
    return {"duration": float(fmt.get("duration") or 0), "has_audio": "audio" in kinds,
            "has_video": "video" in kinds and not path.lower().endswith(AUDIO_EXT),
            "creation_time": (fmt.get("tags") or {}).get("creation_time")}


def sha(path, n=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(n))
    h.update(str(os.path.getsize(path)).encode())
    return h.hexdigest()[:16]


def transcribe(path, out_vtt, model, language="auto"):
    """ffmpeg -> 16 kHz mono wav -> whisper.cpp -> out_vtt. Raises MediaError with an actionable message."""
    t = tools()
    if not t["ffmpeg"]:
        raise MediaError("ffmpeg not found on PATH (needed to read the recording)")
    if not t["whisper"]:
        raise MediaError("whisper.cpp (whisper-cli) not found on PATH; install it or provide a transcript")
    if not model or not os.path.exists(os.path.expanduser(model)):
        raise MediaError(f"whisper model not found: {model!r}; set [media].whisper_model in taska.toml")
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "a.wav")
        try:
            r = subprocess.run([t["ffmpeg"], "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000", wav],
                               capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            raise MediaError("ffmpeg timed out extracting audio")
        if r.returncode:
            raise MediaError("ffmpeg failed: " + r.stderr.strip()[-300:])
        base = os.path.join(td, "out")
        # -mc 0: no text context carried between segments; without it one hallucinated line ("Let's preview.")
        # can repeat for the rest of a long recording
        try:
            r = subprocess.run([t["whisper"], "-m", os.path.expanduser(model), "-f", wav, "-l", language or "auto",
                                "-mc", "0", "-ovtt", "-of", base, "-np"], capture_output=True, text=True, timeout=3600)
        except subprocess.TimeoutExpired:
            raise MediaError("whisper timed out (stuck decode loop or recording too long)")
        if r.returncode or not os.path.exists(base + ".vtt"):
            raise MediaError("whisper failed: " + (r.stderr or r.stdout).strip()[-300:])
        os.makedirs(os.path.dirname(out_vtt) or ".", exist_ok=True)
        shutil.move(base + ".vtt", out_vtt)
    return out_vtt
