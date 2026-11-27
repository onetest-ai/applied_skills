from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Literal, Sequence

Source = Literal["prompt", "queued", "command"]

BEFORE_MAX = 600

CUES: tuple[str, ...] = (
    "don't", "do not", "never", "not ", "no need", "instead", "wrong", "must",
    "should", "avoid", "remove", "ai-ish", "aiish", "clean", "concise", "plain",
    "simple", "always", "why", "?",
)

_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_INTERRUPT = "[Request interrupted by user]"
_COMPACTION = "This session is being continued from a previous conversation"


@dataclass(frozen=True)
class Turn:
    session: str
    uuid: str | None
    timestamp: str
    source: Source
    text: str
    before: str


def cues(text: str) -> list[str]:
    lowered = text.lower()
    return [c for c in CUES if c in lowered]


_COMMAND_TAGS = ("<command-message>", "<command-name>", "<command-args>")


def _is_foreign(text: str) -> bool:
    return (
        any(tag in text for tag in _COMMAND_TAGS)
        or "<agent-message" in text
        or "<task-notification>" in text
        or "Another Claude session sent a message" in text
        or text.startswith(_COMPACTION)
        or text.startswith("<local-command")
        or text == _INTERRUPT
    )


def _clean(raw: str) -> str:
    text = _REMINDER.sub("", raw).strip()
    return "" if _is_foreign(text) else text


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _user_text(content: object) -> tuple[Source, str] | None:
    if isinstance(content, str):
        match = _COMMAND_ARGS.search(content)
        if match:
            args = _clean(match.group(1))
            return ("command", args) if args else None
        text = _clean(content)
        return ("prompt", text) if text else None
    if isinstance(content, list):
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        text = _clean("\n".join(parts))
        return ("prompt", text) if text else None
    return None


def _assistant_text(content: object) -> str:
    if not isinstance(content, list):
        return ""
    parts = [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    return "\n".join(parts).strip()[:BEFORE_MAX]


def turns(path: Path) -> Iterator[Turn]:
    seen: set[tuple[str, str]] = set()
    before = ""
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            kind = rec.get("type")
            session = str(rec.get("sessionId") or "")
            timestamp = str(rec.get("timestamp") or "")
            found: tuple[Source, str] | None = None
            uuid: str | None = None
            if kind == "assistant":
                message = rec.get("message")
                if isinstance(message, dict):
                    text = _assistant_text(message.get("content"))
                    if text:
                        before = text
                continue
            if kind == "user":
                if rec.get("isMeta"):
                    continue
                message = rec.get("message")
                if not isinstance(message, dict):
                    continue
                found = _user_text(message.get("content"))
                raw_uuid = rec.get("uuid")
                uuid = raw_uuid if isinstance(raw_uuid, str) else None
            elif kind == "queue-operation" and rec.get("operation") == "enqueue":
                content = rec.get("content")
                if isinstance(content, str):
                    text = _clean(content)
                    found = ("queued", text) if text else None
            if found is None:
                continue
            source, text = found
            key = (session, _normalise(text))
            if key in seen:
                continue
            seen.add(key)
            yield Turn(session, uuid, timestamp, source, text, before)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mine user turns from Claude Code session JSONL.")
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--only-cued", action="store_true")
    args = parser.parse_args(argv)
    count = 0
    with args.out.open("w", encoding="utf-8") as out:
        for session in args.sessions:
            for turn in turns(session):
                found = cues(turn.text)
                if args.only_cued and not found:
                    continue
                out.write(json.dumps({**asdict(turn), "cues": found}, ensure_ascii=False) + "\n")
                count += 1
    print(f"wrote {count} turns to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
