"""Temporal and cross-session memory stored beside the retrieval index.

The transcript pipeline extracts semantic records into a small JSON ledger.  This
module persists that ledger and resolves current/as-of facts deterministically.
Event time drives resolution; ingestion time is retained only for audit.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_assertions(
  assertion_id TEXT PRIMARY KEY,
  entity TEXT NOT NULL,
  predicate TEXT NOT NULL,
  value_json TEXT NOT NULL,
  asserted_at TEXT NOT NULL,
  ingested_at TEXT NOT NULL,
  source TEXT NOT NULL,
  segment_id TEXT NOT NULL,
  authority INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'asserted'
);
CREATE INDEX IF NOT EXISTS idx_memory_fact
  ON memory_assertions(entity, predicate, asserted_at);
CREATE TABLE IF NOT EXISTS memory_assertion_links(
  assertion_id TEXT NOT NULL,
  relation TEXT NOT NULL CHECK(relation IN ('supersedes','contradicts','retracts')),
  target_assertion_id TEXT NOT NULL,
  PRIMARY KEY(assertion_id, relation, target_assertion_id),
  FOREIGN KEY(assertion_id) REFERENCES memory_assertions(assertion_id),
  FOREIGN KEY(target_assertion_id) REFERENCES memory_assertions(assertion_id)
);
CREATE TABLE IF NOT EXISTS memory_questions(
  question_id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  asked_at TEXT NOT NULL,
  source TEXT NOT NULL,
  segment_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_answers(
  answer_id TEXT PRIMARY KEY,
  question_id TEXT NOT NULL,
  text TEXT NOT NULL,
  answered_at TEXT NOT NULL,
  source TEXT NOT NULL,
  segment_id TEXT NOT NULL,
  FOREIGN KEY(question_id) REFERENCES memory_questions(question_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_answer_question
  ON memory_answers(question_id, answered_at);
"""


def _timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA foreign_keys=ON")
    # Bug 7 fix: executescript() implicitly commits any pending transaction in
    # Python's sqlite3 module.  Using individual con.execute() calls instead
    # preserves the caller's outer transaction (if any).
    for stmt in (s.strip() for s in SCHEMA.split(";") if s.strip()):
        con.execute(stmt)


def _insert_immutable(con: sqlite3.Connection, table: str, columns: list[str], values: list[Any]) -> None:
    placeholders = ",".join("?" for _ in columns)
    con.execute(
        f"INSERT OR IGNORE INTO {table}({','.join(columns)}) VALUES({placeholders})",
        values,
    )
    row = con.execute(
        f"SELECT {','.join(columns)} FROM {table} WHERE {columns[0]}=?", (values[0],)
    ).fetchone()
    if tuple(row) != tuple(values):
        raise ValueError(f"{table} id {values[0]!r} was reused with different content")


def load_ledger(con: sqlite3.Connection, ledger: dict[str, Any]) -> dict[str, int]:
    """Load stable records idempotently; reject ID reuse with changed content."""
    if ledger.get("schema_version") != "1.0":
        raise ValueError("temporal ledger schema_version must be '1.0'")
    ensure_schema(con)
    con.execute("SAVEPOINT temporal_load")
    try:
        for item in ledger.get("assertions", []):
            _insert_immutable(
                con,
                "memory_assertions",
                ["assertion_id", "entity", "predicate", "value_json", "asserted_at", "ingested_at", "source", "segment_id", "authority", "status"],
                [
                    item["assertion_id"], item["entity"], item["predicate"],
                    json.dumps(item["value"], sort_keys=True, separators=(",", ":")),
                    _timestamp(item["asserted_at"]), _timestamp(item["ingested_at"]),
                    item["source"], item["segment_id"], int(item.get("authority", 0)),
                    item.get("status", "asserted"),
                ],
            )
        assertions = {
            row[0]: row for row in con.execute(
                "SELECT assertion_id,entity,predicate,asserted_at,authority FROM memory_assertions"
            )
        }
        for item in ledger.get("assertions", []):
            source = assertions[item["assertion_id"]]
            for relation in ("supersedes", "contradicts", "retracts"):
                for target_id in item.get(relation, []):
                    target = assertions.get(target_id)
                    if target is None:
                        raise ValueError(f"{relation} target {target_id!r} does not exist")
                    if source[1:3] != target[1:3]:
                        raise ValueError(f"{relation} must link the same entity and predicate")
                    if source[3] < target[3]:
                        raise ValueError(f"{relation} cannot point from an older assertion to a newer one")
                    if relation in {"supersedes", "retracts"} and source[4] < target[4]:
                        raise ValueError(f"{relation} requires equal or higher authority")
                    con.execute(
                        "INSERT OR IGNORE INTO memory_assertion_links VALUES(?,?,?)",
                        (item["assertion_id"], relation, target_id),
                    )
        for item in ledger.get("questions", []):
            _insert_immutable(
                con, "memory_questions",
                ["question_id", "text", "asked_at", "source", "segment_id"],
                [item["question_id"], item["text"], _timestamp(item["asked_at"]), item["source"], item["segment_id"]],
            )
        question_ids = {row[0] for row in con.execute("SELECT question_id FROM memory_questions")}
        for item in ledger.get("answers", []):
            if item["question_id"] not in question_ids:
                raise ValueError(f"answer references unknown question {item['question_id']!r}")
            _insert_immutable(
                con, "memory_answers",
                ["answer_id", "question_id", "text", "answered_at", "source", "segment_id"],
                [item["answer_id"], item["question_id"], item["text"], _timestamp(item["answered_at"]), item["source"], item["segment_id"]],
            )
        con.execute("RELEASE SAVEPOINT temporal_load")
    except Exception:
        con.execute("ROLLBACK TO SAVEPOINT temporal_load")
        con.execute("RELEASE SAVEPOINT temporal_load")
        raise
    # Bug 7 fix: do NOT call con.commit() here.  load_ledger is a read-write
    # helper that callers may embed inside a larger transaction.  Committing
    # unconditionally prematurely closes any outer transaction, making the
    # caller's ROLLBACK a no-op and breaking atomicity guarantees.
    # The SAVEPOINT above already guarantees load_ledger's own atomicity.
    return {
        "assertions": len(ledger.get("assertions", [])),
        "questions": len(ledger.get("questions", [])),
        "answers": len(ledger.get("answers", [])),
    }


def current_fact(con: sqlite3.Connection, entity: str, predicate: str, as_of: str | None = None) -> dict[str, Any]:
    cutoff = _timestamp(as_of) if as_of else "9999-12-31T23:59:59Z"
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        """SELECT * FROM memory_assertions
           WHERE entity=? AND predicate=? AND asserted_at<=?
           ORDER BY asserted_at, assertion_id""",
        (entity, predicate, cutoff),
    ))
    ids = {row["assertion_id"] for row in rows}
    links = list(con.execute(
        """SELECT assertion_id, relation, target_assertion_id
           FROM memory_assertion_links WHERE assertion_id IN
           (SELECT assertion_id FROM memory_assertions
            WHERE entity=? AND predicate=? AND asserted_at<=?)""",
        (entity, predicate, cutoff),
    ))
    removed = {
        row["target_assertion_id"] for row in links
        if row["relation"] in {"supersedes", "retracts"} and row["target_assertion_id"] in ids
    }
    active = [row for row in rows if row["assertion_id"] not in removed and row["status"] != "retracted"]
    citations = [
        {"source": row["source"], "segment_id": row["segment_id"], "asserted_at": row["asserted_at"]}
        for row in rows
    ]
    history = [
        {"assertion_id": row["assertion_id"], "value": json.loads(row["value_json"]), "asserted_at": row["asserted_at"], "source": row["source"], "segment_id": row["segment_id"]}
        for row in rows
    ]
    if not active:
        return {"status": "not_modeled", "entity": entity, "predicate": predicate, "value": None, "history": history, "citations": citations}
    values = {row["value_json"] for row in active}
    if len(values) > 1:
        return {"status": "conflicted", "entity": entity, "predicate": predicate, "value": None, "active_assertion_ids": [row["assertion_id"] for row in active], "history": history, "citations": citations}
    winner = max(active, key=lambda row: (row["asserted_at"], row["authority"], row["assertion_id"]))
    return {"status": "current", "entity": entity, "predicate": predicate, "value": json.loads(winner["value_json"]), "assertion_id": winner["assertion_id"], "history": history, "citations": citations}


def question_status(con: sqlite3.Connection, question_id: str, as_of: str | None = None) -> dict[str, Any]:
    cutoff = _timestamp(as_of) if as_of else "9999-12-31T23:59:59Z"
    con.row_factory = sqlite3.Row
    question = con.execute(
        "SELECT * FROM memory_questions WHERE question_id=? AND asked_at<=?", (question_id, cutoff)
    ).fetchone()
    if question is None:
        return {"status": "not_modeled", "question_id": question_id, "answers": [], "citations": []}
    answers = list(con.execute(
        "SELECT * FROM memory_answers WHERE question_id=? AND answered_at<=? ORDER BY answered_at, answer_id",
        (question_id, cutoff),
    ))
    citations = [{"source": question["source"], "segment_id": question["segment_id"], "at": question["asked_at"]}]
    citations.extend({"source": row["source"], "segment_id": row["segment_id"], "at": row["answered_at"]} for row in answers)
    return {
        "status": "resolved" if answers else "open",
        "question_id": question_id,
        "question": question["text"],
        "answer": answers[-1]["text"] if answers else None,
        "resolved_at": answers[-1]["answered_at"] if answers else None,
        "answers": [dict(row) for row in answers],
        "citations": citations,
    }
