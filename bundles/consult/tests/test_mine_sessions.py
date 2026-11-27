from __future__ import annotations

import inspect
import json
from pathlib import Path

import mine_sessions

FIXTURE: Path = Path(__file__).parent / "fixtures" / "session_small.jsonl"


def _write(tmp_path: Path, records: list[dict[str, object]]) -> Path:
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def _user(text: object, uuid: str = "u", meta: bool = False) -> dict[str, object]:
    return {
        "type": "user",
        "isMeta": meta,
        "uuid": uuid,
        "timestamp": "t",
        "sessionId": "s",
        "message": {"role": "user", "content": text},
    }


def _queued(text: str, op: str = "enqueue") -> dict[str, object]:
    return {"type": "queue-operation", "operation": op, "content": text, "timestamp": "t", "sessionId": "s"}


def _all() -> list[mine_sessions.Turn]:
    return list(mine_sessions.turns(FIXTURE))


def test_typed_prompt_is_a_turn() -> None:
    first = _all()[0]
    assert first.source == "prompt"
    assert first.text == "Build the widget report for Acme Test"
    assert first.session == "s-aaaa"
    assert first.uuid == "u1"


def test_tool_results_and_meta_are_not_turns() -> None:
    texts = [t.text for t in _all()]
    assert not any("tool_result" in x or "big skill body" in x for x in texts)


def test_command_args_kept_skill_body_dropped() -> None:
    commands = [t for t in _all() if t.source == "command"]
    assert [c.text for c in commands] == ["implement it all"]
    assert not any("/model" in t.text for t in _all())


def test_command_tag_order_and_indentation_do_not_matter(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            _user("<command-message>x</command-message>\n<command-name>/x</command-name>\n<command-args>do it</command-args>", "a"),
            _user("<command-name>/y</command-name>\n    <command-message>y</command-message>\n    <command-args>then this</command-args>", "b"),
            _user("<command-name>/model</command-name>\n    <command-message>model</command-message>\n    <command-args></command-args>", "c"),
        ],
    )
    result = list(mine_sessions.turns(path))
    assert [(t.source, t.text) for t in result] == [("command", "do it"), ("command", "then this")]


def test_no_turn_keeps_command_tags(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            _user("<command-message>x</command-message>\n<command-name>/x</command-name>", "a"),
            _user([{"type": "text", "text": "<command-name>/z</command-name> hi"}], "b"),
            _queued("<command-args>q</command-args>"),
            _user("fine", "c"),
        ],
    )
    result = list(mine_sessions.turns(path))
    assert [t.text for t in result] == ["fine"]
    for t in result:
        assert not any(tag in t.text for tag in ("<command-message>", "<command-name>", "<command-args>"))


def test_queued_messages_kept_and_deduped() -> None:
    result = _all()
    queued = [t for t in result if t.source == "queued"]
    assert [q.text for q in queued] == ["use digits"]
    assert queued[0].uuid is None
    assert [t.text for t in result].count("use digits") == 1


def test_system_reminders_stripped() -> None:
    assert "keep this" in [t.text for t in _all()]
    assert not any("system-reminder" in t.text for t in _all())


def test_image_prompt_keeps_text_and_image_only_is_dropped() -> None:
    texts = [t.text for t in _all()]
    assert "[Image #7] check this" in texts
    assert len(texts) == len(set(texts))
    assert all(t.strip() for t in texts)


def test_before_is_last_assistant_text() -> None:
    result = _all()
    assert result[0].before == ""
    second = next(t for t in result if t.text == "implement it all")
    assert second.before == "I will build it with semicolons; and more."
    last = result[-1]
    assert last.text == "don't use semicolons"
    assert last.before == "Z" * 600


def test_streams_and_skips_bad_lines() -> None:
    gen = mine_sessions.turns(FIXTURE)
    assert inspect.isgenerator(gen)
    assert len(list(gen)) == len(_all())
    assert "keep this" in [t.text for t in _all()]


def test_subagent_and_notification_text_dropped(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            _queued('<agent-message from="x">done</agent-message>'),
            _queued("<task-notification>finished</task-notification>"),
            _user("see <agent-message>report</agent-message>", "a"),
            _user("<task-notification>x</task-notification>", "b"),
            _user("Another Claude session sent a message: hi", "c"),
            _queued("real queued words"),
        ],
    )
    assert [t.text for t in mine_sessions.turns(path)] == ["real queued words"]


def test_compaction_summary_and_interrupt_dropped(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            _user("This session is being continued from a previous conversation that ran out", "a"),
            _user("<local-command-stdout>ok</local-command-stdout>", "b"),
            _user("[Request interrupted by user]", "c"),
            _queued("[Request interrupted by user]"),
            _user("<system-reminder>only this</system-reminder>", "d"),
            _user("keep me", "e"),
        ],
    )
    assert [t.text for t in mine_sessions.turns(path)] == ["keep me"]


def test_cues_flag_corrections() -> None:
    assert "don't" in mine_sessions.cues("don't use semicolons")
    assert mine_sessions.cues("thanks") == []
    assert "?" in mine_sessions.cues("why is it slow?")
    assert set(mine_sessions.cues("Never do that")) >= {"never"}


def test_cli_writes_candidates_and_only_cued(tmp_path: Path) -> None:
    out = tmp_path / "c.jsonl"
    assert mine_sessions.main([str(FIXTURE), "--out", str(out), "--only-cued"]) == 0
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    assert rows
    assert all(r["cues"] for r in rows)
    assert {"session", "uuid", "timestamp", "source", "text", "before", "cues"} <= set(rows[0])
    out_all = tmp_path / "all.jsonl"
    mine_sessions.main([str(FIXTURE), "--out", str(out_all)])
    assert len(out_all.read_text().splitlines()) == len(_all())
