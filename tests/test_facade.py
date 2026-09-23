from datetime import datetime, timezone

import pytest

from workbench import db
from workbench.audit import AuditLog
from workbench.guardrails.budgets import Budget, BudgetExceeded
from workbench.guardrails.commands import CommandPolicy
from workbench.guardrails.facade import Guardrails
from workbench.guardrails.paths import PathPolicy
from workbench.models import Run, RunStatus, Task

RUN_ID = "run-1"


@pytest.fixture
def conn():
    conn = db.connect(":memory:")
    # runs.task_id is a foreign key, so the task row has to exist first
    task = Task(title="review", description="check the diff", repo_path="/tmp/repo", task_type="review")
    db.insert_task(conn, task)
    db.insert_run(
        conn,
        Run(
            id=RUN_ID,
            task_id=task.id,
            status=RunStatus.RUNNING,
            provider="stub",
            started_at=datetime.now(timezone.utc),
        ),
    )
    yield conn
    conn.close()


def make_guardrails(conn, root, budget=None):
    return Guardrails(
        paths=PathPolicy(root, ["**"]),
        commands=CommandPolicy([["pytest"]]),
        budget=budget or Budget(10, 1000),
        audit=AuditLog(conn),
    )


@pytest.fixture
def guard(conn, tmp_path):
    return make_guardrails(conn, tmp_path)


def events(conn, event):
    return [record for record in db.list_audit(conn, RUN_ID) if record.event == event]


def test_read_file_allowed_writes_tool_call(conn, guard):
    decision = guard.check(RUN_ID, "read_file", {"path": "src/app.py"})

    assert decision.allowed
    records = db.list_audit(conn, RUN_ID)
    assert [record.event for record in records] == ["tool_call"]
    assert records[0].payload == {"tool": "read_file", "arguments": {"path": "src/app.py"}}


def test_read_file_parent_path_denied_and_audited(conn, guard):
    decision = guard.check(RUN_ID, "read_file", {"path": ".."})

    assert not decision.allowed
    assert decision.reason == "parent segment in path"
    blocked = events(conn, "tool_blocked")
    assert len(blocked) == 1
    assert blocked[0].payload["reason"] == "parent segment in path"


def test_unknown_tool_denied(conn, guard):
    decision = guard.check(RUN_ID, "delete_everything", {})

    assert not decision.allowed
    assert decision.reason == "unknown tool"
    assert events(conn, "tool_blocked")[0].payload["reason"] == "unknown tool"


def test_run_tests_allowlisted_argv_allowed(guard):
    decision = guard.check(RUN_ID, "run_tests", {"argv": ["pytest", "-q"]})

    assert decision.allowed


def test_run_tests_string_argv_denied(conn, guard):
    decision = guard.check(RUN_ID, "run_tests", {"argv": "pytest -q"})

    assert not decision.allowed
    assert decision.reason == "argv must be a list of strings"
    assert len(events(conn, "tool_blocked")) == 1


def test_run_tests_shell_denied(conn, guard):
    decision = guard.check(RUN_ID, "run_tests", {"argv": ["bash", "-c", "id"]})

    assert not decision.allowed
    assert decision.reason == "command not allowlisted"
    assert len(events(conn, "tool_blocked")) == 1


def test_run_tests_cwd_outside_root_denied(guard):
    decision = guard.check(RUN_ID, "run_tests", {"argv": ["pytest", "-q"], "cwd": "../elsewhere"})

    assert not decision.allowed
    assert decision.reason == "parent segment in path"


def test_open_issue_allowed_and_needs_approval(guard):
    decision = guard.check(RUN_ID, "open_issue", {"title": "flaky test"})

    assert decision.allowed
    assert decision.needs_approval


def test_step_budget_exceeded_is_audited(conn, tmp_path):
    guard = make_guardrails(conn, tmp_path, budget=Budget(1, 1000))
    guard.spend_step(RUN_ID)

    with pytest.raises(BudgetExceeded) as exc:
        guard.spend_step(RUN_ID)

    assert exc.value.kind == "steps"
    exceeded = events(conn, "budget_exceeded")
    assert len(exceeded) == 1
    assert exceeded[0].payload == {"kind": "steps", "limit": 1}
