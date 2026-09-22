import json
from datetime import datetime, timezone

from workbench import db
from workbench.audit import AuditLog
from workbench.models import Run, RunStatus, Task


def _start_run(conn, run_id: str) -> None:
    task = Task(title="review", description="check the diff", repo_path="/tmp/repo", task_type="review")
    db.insert_task(conn, task)
    run = Run(
        id=run_id,
        task_id=task.id,
        status=RunStatus.RUNNING,
        provider="stub",
        started_at=datetime.now(timezone.utc),
    )
    db.insert_run(conn, run)


def test_record_persists_and_increments_seq():
    conn = db.connect()
    _start_run(conn, "run-1")
    log = AuditLog(conn)

    first = log.record("run-1", "run_started", {"provider": "stub"})
    second = log.record("run-1", "tool_call", {"tool": "read_file"})

    assert first.seq == 1
    assert second.seq == 2
    assert [record.seq for record in db.list_audit(conn, "run-1")] == [1, 2]


def test_seq_is_per_run():
    conn = db.connect()
    _start_run(conn, "run-1")
    _start_run(conn, "run-2")
    log = AuditLog(conn)

    log.record("run-1", "run_started", {})
    log.record("run-1", "tool_call", {})

    assert log.record("run-2", "run_started", {}).seq == 1


def test_dump_orders_by_seq_and_is_json_serializable():
    conn = db.connect()
    _start_run(conn, "run-1")
    log = AuditLog(conn)
    events = ["run_started", "tool_call", "run_finished"]
    for event in events:
        log.record("run-1", event, {"count": 1, "tags": ["a", "b"]})

    rows = log.dump("run-1")

    assert [row["seq"] for row in rows] == [1, 2, 3]
    assert [row["event"] for row in rows] == events
    assert isinstance(rows[0]["at"], str)
    datetime.fromisoformat(rows[0]["at"])
    json.dumps(rows)


def test_redact_applies_to_nested_strings():
    conn = db.connect()
    _start_run(conn, "run-1")
    log = AuditLog(conn, redact=lambda text: text.replace("token", "[redacted]"))

    record = log.record("run-1", "tool_call", {"a": "token abc", "b": [{"c": "token def"}]})

    expected = {"a": "[redacted] abc", "b": [{"c": "[redacted] def"}]}
    assert record.payload == expected
    assert log.dump("run-1")[0]["payload"] == expected


def test_records_survive_reconnect(tmp_path):
    path = str(tmp_path / "workbench.db")
    conn = db.connect(path)
    _start_run(conn, "run-1")
    AuditLog(conn).record("run-1", "run_started", {"ok": True})
    conn.close()

    reopened = db.connect(path)
    try:
        records = db.list_audit(reopened, "run-1")
        assert [record.event for record in records] == ["run_started"]
        assert records[0].payload == {"ok": True}
    finally:
        reopened.close()
