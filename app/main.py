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
from workbench.models import Run, RunStatus, Task
from workbench.tools import OpenIssueTool, ReadFileTool, RunTestsTool, SearchCodeTool, ToolRegistry

from app.schemas import RunIn, RunOut, TaskIn

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

    return app


app = create_app()
