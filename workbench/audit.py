import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable

from workbench import db
from workbench.models import AuditRecord


SAFE_KEYS = {"task_id", "run_id", "approval_id"}


def _redact_value(value: Any, redact: Callable[[str], str], key: str | None = None) -> Any:
    if isinstance(value, str):
        # identifiers are not secrets; 32-character hex ids would trip the hex rule
        return value if key in SAFE_KEYS else redact(value)
    if isinstance(value, dict):
        return {name: _redact_value(item, redact, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, redact, key) for item in value]
    return value


class AuditLog:
    def __init__(self, conn: sqlite3.Connection, redact: Callable[[str], str] | None = None) -> None:
        self._conn = conn
        self._redact = redact or (lambda text: text)

    def record(self, run_id: str, event: str, payload: dict[str, Any]) -> AuditRecord:
        record = AuditRecord(
            run_id=run_id,
            seq=self._next_seq(run_id),
            at=datetime.now(timezone.utc),
            event=event,
            payload=_redact_value(payload, self._redact),
        )
        db.append_audit(self._conn, record)
        return record

    def dump(self, run_id: str) -> list[dict[str, Any]]:
        return [record.model_dump(mode="json") for record in db.list_audit(self._conn, run_id)]

    def _next_seq(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM audit WHERE run_id = ?", (run_id,)
        ).fetchone()
        return row[0]
