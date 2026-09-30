import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# the run status value is the same as its name, which is what is stored in sqlite.
class RunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


TaskType = Literal["review", "test_triage", "docs"]


class Task(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    title: str
    description: str
    repo_path: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    task_type: TaskType


class ToolCall(BaseModel):
    step: int
    tool: str
    arguments: dict[str, Any]
    allowed: bool
    blocked_reason: str | None = None
    result_summary: str | None = None
    citations: list[str] = []


class Approval(BaseModel):
    id: str
    run_id: str
    tool: str
    arguments: dict[str, Any]
    requested_at: datetime
    decided_at: datetime | None = None
    approved: bool | None = None
    decided_by: str | None = None


class AuditRecord(BaseModel):
    run_id: str
    seq: int
    at: datetime
    event: str
    payload: dict[str, Any]


class Run(BaseModel):
    id: str
    task_id: str
    status: RunStatus
    provider: str
    steps: int = 0
    tokens: int = 0
    started_at: datetime
    finished_at: datetime | None = None
    result: str | None = None
    error: str | None = None
    # json text of the message history, kept so an approval can resume the loop
    history: str | None = None
    max_steps: int | None = None
    max_tokens: int | None = None
