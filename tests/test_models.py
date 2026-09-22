from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from workbench.models import Run, RunStatus, Task, ToolCall


def test_run_status_values():
    assert {status.value for status in RunStatus} == {
        "PENDING",
        "RUNNING",
        "WAITING_APPROVAL",
        "COMPLETED",
        "FAILED",
        "BLOCKED",
    }


def test_tool_call_defaults():
    call = ToolCall(step=1, tool="read_file", arguments={"path": "README.md"}, allowed=True)
    assert call.citations == []
    assert call.blocked_reason is None


def test_run_requires_status_and_provider():
    started = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        Run(id="run-1", task_id="task-1", started_at=started, provider="stub")
    with pytest.raises(ValidationError):
        Run(id="run-1", task_id="task-1", started_at=started, status=RunStatus.PENDING)

    run = Run(id="run-1", task_id="task-1", started_at=started, status=RunStatus.PENDING, provider="stub")
    assert run.steps == 0
    assert run.tokens == 0


def test_task_generates_id_and_utc_created_at():
    task = Task(title="triage", description="look at failing tests", repo_path="/tmp/repo", task_type="test_triage")
    assert len(task.id) == 32
    assert set(task.id) <= set("0123456789abcdef")
    assert task.created_at.utcoffset() == timedelta(0)
