import json
import os
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Callable

from fastapi import FastAPI, HTTPException

from workbench import db
from workbench.agents import Executor, Provider, make_provider
from workbench.audit import AuditLog
from workbench.guardrails.secrets import redact
from workbench.guardrails import CommandPolicy, PathPolicy
from workbench.guardrails.budgets import Budget
from workbench.guardrails.facade import Guardrails
from workbench.models import Approval, Run, RunStatus, Task, ToolCall
from workbench.tools import OpenIssueTool, ReadFileTool, RunTestsTool, SearchChunksTool, SearchCodeTool, ToolRegistry

from app.schemas import ApprovalDecision, RunIn, RunOut, TaskIn

DEFAULT_DB = "workbench.db"
ALLOWED_COMMANDS = [["pytest"], ["python", "-m", "pytest"]]


def _default_provider(run_in: RunIn) -> Provider:
    if run_in.provider == "stub":
        return make_provider("stub", script=run_in.script)
    # None values are left out so make_provider reports the missing ones
    options = {"base_url": run_in.base_url, "model": run_in.model}
    return make_provider(run_in.provider, **{key: value for key, value in options.items() if value is not None})


def create_app(
    db_path: str | None = None,
    provider_factory: Callable[[RunIn], Provider] | None = None,
) -> FastAPI:
    path = os.environ.get("WORKBENCH_DB", DEFAULT_DB) if db_path is None else db_path
    build_provider = provider_factory or _default_provider

    def _guardrails(conn, task: Task, max_steps: int, max_tokens: int):
        budget = Budget(max_steps, max_tokens)
        audit = AuditLog(conn, redact=redact)
        guardrails = Guardrails(
            paths=PathPolicy(task.repo_path, ["**"]),
            commands=CommandPolicy(ALLOWED_COMMANDS),
            budget=budget,
            audit=audit,
        )
        registry = ToolRegistry()
        for tool in (
            ReadFileTool(task.repo_path),
            SearchCodeTool(task.repo_path),
            RunTestsTool(task.repo_path),
            OpenIssueTool(),
        ):
            registry.register(tool)
        return guardrails, registry, audit, budget


    app = FastAPI(title="agent-workbench")

    # each endpoint opens its own connection: sqlite objects cannot cross threads
    # and sync endpoints run in a threadpool.

    @app.post("/tasks", status_code=201, response_model=Task)
    def create_task(body: TaskIn) -> Task:
        task = Task(**body.model_dump())
        with closing(db.connect(path)) as conn:
            db.insert_task(conn, task)
        return task

    @app.get("/tasks/{task_id}", response_model=Task)
    def read_task(task_id: str) -> Task:
        with closing(db.connect(path)) as conn:
            task = db.get_task(conn, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return task

    @app.post("/runs", status_code=201, response_model=RunOut)
    def create_run(body: RunIn) -> RunOut:
        with closing(db.connect(path)) as conn:
            task = db.get_task(conn, body.task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="task not found")
            try:
                provider = build_provider(body)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

            # the facade is the only path to the tools, so every denial is audited
            budget = Budget(body.max_steps, body.max_tokens)
            audit = AuditLog(conn, redact=redact)
            guardrails = Guardrails(
                paths=PathPolicy(task.repo_path, ["**"]),
                commands=CommandPolicy(ALLOWED_COMMANDS),
                budget=budget,
                audit=audit,
            )
            registry = ToolRegistry()
            for tool in (
                ReadFileTool(task.repo_path),
                SearchCodeTool(task.repo_path),
                SearchChunksTool(task.repo_path),
                RunTestsTool(task.repo_path),
                OpenIssueTool(),
            ):
                registry.register(tool)

            run = Run(
                id=uuid.uuid4().hex,
                task_id=task.id,
                status=RunStatus.RUNNING,
                provider=body.provider,
                started_at=datetime.now(timezone.utc),
            )
            db.insert_run(conn, run)
            Executor(conn, provider, registry, guardrails, audit, budget).run(task, run.id)
            return RunOut(**db.get_run(conn, run.id).model_dump())

    @app.get("/runs/{run_id}", response_model=RunOut)
    def read_run(run_id: str) -> RunOut:
        with closing(db.connect(path)) as conn:
            run = db.get_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return RunOut(**run.model_dump())

    @app.get("/runs/{run_id}/audit")
    def read_audit(run_id: str) -> dict:
        with closing(db.connect(path)) as conn:
            if db.get_run(conn, run_id) is None:
                raise HTTPException(status_code=404, detail="run not found")
            records = AuditLog(conn, redact=redact).dump(run_id)
        return {"records": records}

    @app.get("/runs/{run_id}/approvals")
    def read_approvals(run_id: str) -> dict:
        with closing(db.connect(path)) as conn:
            if db.get_run(conn, run_id) is None:
                raise HTTPException(status_code=404, detail="run not found")
            pending = db.get_pending_approval(conn, run_id)
        return {"pending": pending.model_dump(mode="json") if pending else None}

    @app.post("/runs/{run_id}/approvals/{approval_id}", response_model=RunOut)
    def decide_approval(run_id: str, approval_id: str, body: ApprovalDecision) -> RunOut:
        with closing(db.connect(path)) as conn:
            run = db.get_run(conn, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="run not found")
            approval = db.get_pending_approval(conn, run_id)
            if approval is None or approval.id != approval_id:
                raise HTTPException(status_code=404, detail="approval not found")
            task = db.get_task(conn, run.task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="task not found")

            db.decide_approval(conn, approval_id, body.approved, body.decided_by)
            audit = AuditLog(conn, redact=redact)
            audit.record(run_id, "approval_decided",
                         {"approval_id": approval_id, "approved": body.approved,
                          "decided_by": body.decided_by})

            if not body.approved:
                error = f"rejected by {body.decided_by}"
                db.update_run(conn, run_id, status=RunStatus.FAILED, error=error,
                              finished_at=datetime.now(timezone.utc))
                audit.record(run_id, "run_finished", {"status": RunStatus.FAILED.value})
                return RunOut(**db.get_run(conn, run_id).model_dump())

            # approved: run the pending tool once, then let the loop continue from its history
            guardrails, registry, audit, budget = _guardrails(conn, task, 8, 20000)
            try:
                result = registry.get(approval.tool).run(approval.arguments)
            except Exception as exc:
                error = f"approved tool failed: {exc}"
                db.update_run(conn, run_id, status=RunStatus.FAILED, error=error,
                              finished_at=datetime.now(timezone.utc))
                audit.record(run_id, "run_finished", {"status": RunStatus.FAILED.value})
                return RunOut(**db.get_run(conn, run_id).model_dump())

            conn.execute(
                "UPDATE tool_calls SET result_summary = ? WHERE run_id = ? AND tool = ? "
                "AND result_summary IS NULL",
                (result.output[:200], run_id, approval.tool),
            )
            conn.commit()

            history = json.loads(run.history) if run.history else []
            history.append({"role": "assistant",
                            "content": json.dumps({"tool": approval.tool,
                                                   "arguments": approval.arguments})})
            history.append({"role": "user", "content": f"{approval.tool} -> {result.output}"})

            run_in = RunIn(task_id=task.id, provider=run.provider, script=body.script,
                           model=body.model, base_url=body.base_url)
            try:
                provider = build_provider(run_in)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

            db.update_run(conn, run_id, status=RunStatus.RUNNING)
            Executor(conn, provider, registry, guardrails, audit, budget).run(
                task, run_id, history=history
            )
            return RunOut(**db.get_run(conn, run_id).model_dump())

    return app


app = create_app()
