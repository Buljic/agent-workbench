import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from workbench.models import Approval, AuditRecord, Run, RunStatus, Task, ToolCall

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    task_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    status TEXT NOT NULL,
    provider TEXT NOT NULL,
    steps INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    step INTEGER NOT NULL,
    tool TEXT NOT NULL,
    arguments TEXT NOT NULL,
    allowed INTEGER NOT NULL,
    blocked_reason TEXT,
    result_summary TEXT,
    citations TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    tool TEXT NOT NULL,
    arguments TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    decided_at TEXT,
    approved INTEGER,
    decided_by TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    seq INTEGER NOT NULL,
    at TEXT NOT NULL,
    event TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE (run_id, seq)
);
"""

# columns that update_run may change. id and task_id are fixed at insert time.
RUN_UPDATABLE = {"status", "provider", "steps", "tokens", "started_at", "finished_at", "result", "error"}


def connect(db_path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def insert_task(conn: sqlite3.Connection, task: Task) -> None:
    conn.execute(
        "INSERT INTO tasks (id, title, description, repo_path, created_at, task_type) VALUES (?, ?, ?, ?, ?, ?)",
        (task.id, task.title, task.description, task.repo_path, task.created_at.isoformat(), task.task_type),
    )
    conn.commit()


def get_task(conn: sqlite3.Connection, task_id: str) -> Task | None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    return Task(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        repo_path=row["repo_path"],
        created_at=datetime.fromisoformat(row["created_at"]),
        task_type=row["task_type"],
    )


def insert_run(conn: sqlite3.Connection, run: Run) -> None:
    conn.execute(
        "INSERT INTO runs (id, task_id, status, provider, steps, tokens, started_at, finished_at, result, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run.id,
            run.task_id,
            run.status.value,
            run.provider,
            run.steps,
            run.tokens,
            run.started_at.isoformat(),
            _iso(run.finished_at),
            run.result,
            run.error,
        ),
    )
    conn.commit()


def update_run(conn: sqlite3.Connection, run_id: str, **fields: Any) -> None:
    unknown = set(fields) - RUN_UPDATABLE
    if unknown:
        raise ValueError(f"cannot update run fields: {sorted(unknown)}")
    if not fields:
        return

    values = []
    for value in fields.values():
        if isinstance(value, RunStatus):
            value = value.value
        elif isinstance(value, datetime):
            value = value.isoformat()
        values.append(value)

    # column names come from RUN_UPDATABLE, so the f-string cannot take user input.
    assignments = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(f"UPDATE runs SET {assignments} WHERE id = ?", (*values, run_id))
    conn.commit()


def get_run(conn: sqlite3.Connection, run_id: str) -> Run | None:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return Run(
        id=row["id"],
        task_id=row["task_id"],
        status=RunStatus(row["status"]),
        provider=row["provider"],
        steps=row["steps"],
        tokens=row["tokens"],
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=_parse(row["finished_at"]),
        result=row["result"],
        error=row["error"],
    )


def insert_tool_call(conn: sqlite3.Connection, run_id: str, call: ToolCall) -> None:
    conn.execute(
        "INSERT INTO tool_calls (run_id, step, tool, arguments, allowed, blocked_reason, result_summary, citations) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            call.step,
            call.tool,
            json.dumps(call.arguments),
            int(call.allowed),
            call.blocked_reason,
            call.result_summary,
            json.dumps(call.citations),
        ),
    )
    conn.commit()


def list_tool_calls(conn: sqlite3.Connection, run_id: str) -> list[ToolCall]:
    rows = conn.execute("SELECT * FROM tool_calls WHERE run_id = ? ORDER BY step, id", (run_id,)).fetchall()
    return [
        ToolCall(
            step=row["step"],
            tool=row["tool"],
            arguments=json.loads(row["arguments"]),
            allowed=bool(row["allowed"]),
            blocked_reason=row["blocked_reason"],
            result_summary=row["result_summary"],
            citations=json.loads(row["citations"]),
        )
        for row in rows
    ]


def insert_approval(conn: sqlite3.Connection, approval: Approval) -> None:
    conn.execute(
        "INSERT INTO approvals (id, run_id, tool, arguments, requested_at, decided_at, approved, decided_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            approval.id,
            approval.run_id,
            approval.tool,
            json.dumps(approval.arguments),
            approval.requested_at.isoformat(),
            _iso(approval.decided_at),
            _optional_int(approval.approved),
            approval.decided_by,
        ),
    )
    conn.commit()


def get_pending_approval(conn: sqlite3.Connection, run_id: str) -> Approval | None:
    row = conn.execute(
        "SELECT * FROM approvals WHERE run_id = ? AND decided_at IS NULL ORDER BY requested_at LIMIT 1",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    return Approval(
        id=row["id"],
        run_id=row["run_id"],
        tool=row["tool"],
        arguments=json.loads(row["arguments"]),
        requested_at=datetime.fromisoformat(row["requested_at"]),
        decided_at=_parse(row["decided_at"]),
        approved=None if row["approved"] is None else bool(row["approved"]),
        decided_by=row["decided_by"],
    )


def decide_approval(conn: sqlite3.Connection, approval_id: str, approved: bool, decided_by: str) -> None:
    cursor = conn.execute(
        "UPDATE approvals SET approved = ?, decided_at = ?, decided_by = ? WHERE id = ? AND decided_at IS NULL",
        (int(approved), datetime.now(timezone.utc).isoformat(), decided_by, approval_id),
    )
    if cursor.rowcount == 0:
        conn.rollback()
        raise ValueError(f"no pending approval with id {approval_id}")
    conn.commit()


def append_audit(conn: sqlite3.Connection, record: AuditRecord) -> None:
    conn.execute(
        "INSERT INTO audit (run_id, seq, at, event, payload) VALUES (?, ?, ?, ?, ?)",
        (record.run_id, record.seq, record.at.isoformat(), record.event, json.dumps(record.payload)),
    )
    conn.commit()


def list_audit(conn: sqlite3.Connection, run_id: str) -> list[AuditRecord]:
    rows = conn.execute("SELECT * FROM audit WHERE run_id = ? ORDER BY seq", (run_id,)).fetchall()
    return [
        AuditRecord(
            run_id=row["run_id"],
            seq=row["seq"],
            at=datetime.fromisoformat(row["at"]),
            event=row["event"],
            payload=json.loads(row["payload"]),
        )
        for row in rows
    ]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _optional_int(value: bool | None) -> int | None:
    return int(value) if value is not None else None
