import json
from datetime import datetime, timezone

import pytest

from workbench import db
from workbench.agents import Executor, StubProvider
from workbench.audit import AuditLog
from workbench.guardrails.budgets import Budget
from workbench.guardrails.commands import CommandPolicy
from workbench.guardrails.facade import Guardrails
from workbench.guardrails.paths import PathPolicy
from workbench.models import Run, RunStatus, Task
from workbench.tools import OpenIssueTool, ReadFileTool, RunTestsTool, SearchCodeTool, ToolRegistry

RUN_ID = "run-1"


def step(**action):
    return json.dumps(action)


@pytest.fixture
def conn():
    conn = db.connect(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def task(conn, tmp_path):
    task = Task(
        title="review app",
        description="check the app for sockets",
        repo_path=str(tmp_path),
        task_type="review",
    )
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
    return task


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("import socket\nx = 1\n")
    return tmp_path


def build(conn, root, script, max_steps=5, max_tokens=10000):
    budget = Budget(max_steps=max_steps, max_tokens=max_tokens)
    audit = AuditLog(conn)
    guardrails = Guardrails(
        paths=PathPolicy(root, ["**"]),
        commands=CommandPolicy([["pytest"]]),
        budget=budget,
        audit=audit,
    )
    registry = ToolRegistry()
    for tool in (ReadFileTool(root), SearchCodeTool(root), RunTestsTool(root), OpenIssueTool()):
        registry.register(tool)
    provider = StubProvider(script)
    executor = Executor(conn, provider, registry, guardrails, audit, budget)
    return executor, provider


def events(conn):
    return [record.event for record in db.list_audit(conn, RUN_ID)]


def test_happy_path_reads_file_then_finishes(conn, repo, task):
    script = [
        step(tool="read_file", arguments={"path": "src/app.py"}),
        step(final="it opens a socket"),
    ]
    executor, provider = build(conn, repo, script)

    outcome = executor.run(task, RUN_ID)

    assert outcome.status is RunStatus.COMPLETED
    assert outcome.result == "it opens a socket"
    assert outcome.steps == 2
    assert outcome.tokens > 0

    calls = db.list_tool_calls(conn, RUN_ID)
    assert len(calls) == 1
    assert calls[0].allowed is True
    assert calls[0].result_summary.startswith("import socket")
    assert calls[0].citations == ["src/app.py:1-2"]

    assert events(conn) == ["run_started", "tool_call", "run_finished"]
    # the second provider call sees the tool output as a user observation
    observation = provider.calls[1]["messages"][-1]
    assert observation == {"role": "user", "content": "read_file -> import socket\nx = 1\n"}


def test_blocked_read_is_fed_back_and_run_completes(conn, repo, task):
    script = [
        step(tool="read_file", arguments={"path": "../outside.txt"}),
        step(final="could not read it"),
    ]
    executor, provider = build(conn, repo, script)

    outcome = executor.run(task, RUN_ID)

    assert outcome.status is RunStatus.COMPLETED
    calls = db.list_tool_calls(conn, RUN_ID)
    assert calls[0].allowed is False
    assert calls[0].blocked_reason is not None
    assert "tool_blocked" in events(conn)
    observation = provider.calls[1]["messages"][-1]["content"]
    assert observation.startswith("blocked: ") and observation.endswith("(tool read_file)")


def test_write_tool_waits_for_approval(conn, repo, task):
    script = [step(tool="open_issue", arguments={"title": "x", "body": "y"})]
    executor, _ = build(conn, repo, script)

    outcome = executor.run(task, RUN_ID)

    assert outcome.status is RunStatus.WAITING_APPROVAL
    assert outcome.approval_id is not None
    pending = db.get_pending_approval(conn, RUN_ID)
    assert pending is not None
    assert pending.id == outcome.approval_id
    assert pending.tool == "open_issue"
    assert db.get_run(conn, RUN_ID).status is RunStatus.WAITING_APPROVAL
    assert "approval_requested" in events(conn)


def test_step_budget_stops_a_run_that_never_finishes(conn, repo, task):
    script = [step(tool="read_file", arguments={"path": "src/app.py"})]
    executor, provider = build(conn, repo, script, max_steps=1)

    outcome = executor.run(task, RUN_ID)

    assert outcome.status is RunStatus.BLOCKED
    assert outcome.error == "step budget exhausted"
    assert outcome.steps == 1
    assert len(provider.calls) == 1
    assert "budget_exceeded" in events(conn)
    assert events(conn)[-1] == "run_finished"


def test_invalid_plan_fails_with_plan_error(conn, repo, task):
    executor, _ = build(conn, repo, ["not json"])

    outcome = executor.run(task, RUN_ID)

    assert outcome.status is RunStatus.FAILED
    assert outcome.error.startswith("plan error:")
    assert "parse_error" in events(conn)


def test_token_budget_blocks_the_run(conn, repo, task):
    executor, _ = build(conn, repo, [step(final="done")], max_tokens=1)

    outcome = executor.run(task, RUN_ID)

    assert outcome.status is RunStatus.BLOCKED
    assert outcome.tokens == 0
    assert "tokens budget" in outcome.error


def test_executor_rejects_a_separate_budget(conn, repo, task):
    executor, _ = build(conn, repo, [])
    other = Budget(max_steps=5, max_tokens=10000)
    with pytest.raises(ValueError, match="same object"):
        Executor(conn, executor.provider, executor.registry, executor.guardrails, executor.audit, other)


def test_terminal_state_is_persisted_on_the_run_row(conn, repo, task):
    script = [step(tool="read_file", arguments={"path": "src/app.py"}), step(final="done")]
    executor, _provider = build(conn, repo, script)
    outcome = executor.run(task, RUN_ID)
    assert outcome.status is RunStatus.COMPLETED
    row = db.get_run(conn, RUN_ID)
    assert row.status is RunStatus.COMPLETED
    assert row.finished_at is not None
    assert row.result == "done"
    assert events(conn)[-1] == "run_finished"
