import argparse
import json
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from workbench import db
from workbench.agents import Executor, LlamaCppProvider, Provider, StubProvider
from workbench.audit import AuditLog
from workbench.guardrails import CommandPolicy, PathPolicy
from workbench.guardrails.budgets import Budget
from workbench.guardrails.facade import Guardrails
from workbench.guardrails.secrets import redact
from workbench.models import Run, RunStatus, Task
from workbench.tools import (
    OpenIssueTool,
    ReadFileTool,
    RunTestsTool,
    SearchChunksTool,
    SearchCodeTool,
    ToolRegistry,
)

from evals.tasks import GOLDEN_TASKS, GoldenTask

FIXTURE = Path(__file__).resolve().parent / "fixture_repo"
DEFAULT_OUT = Path(__file__).resolve().parent / "out" / "results.json"

# same policy as app/main.py
from workbench.config import ALLOWED_COMMANDS
BUDGET_STEPS = 8
BUDGET_TOKENS = 20000

RESULT_CHARS = 120
COLUMNS = ["task", "type", "status", "passed", "steps", "tokens", "seconds"]


def run_suite(
    provider_name: str,
    base_url: str | None = None,
    model: str | None = None,
    workdir: Path | None = None,
    tasks: list[GoldenTask] | None = None,
) -> list[dict]:
    if provider_name not in ("stub", "llama_cpp"):
        raise ValueError(f"unknown provider: {provider_name}")
    if provider_name == "llama_cpp" and not (base_url and model):
        raise ValueError("llama_cpp needs base_url and model")
    return [
        _run_task(golden, provider_name, base_url, model, workdir)
        for golden in (tasks if tasks is not None else GOLDEN_TASKS)
    ]


def _run_task(
    golden: GoldenTask,
    provider_name: str,
    base_url: str | None,
    model: str | None,
    workdir: Path | None,
) -> dict:
    # each task gets its own copy of the fixture and its own database
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(FIXTURE, repo)
        conn = db.connect(str(Path(tmp) / "workbench.db"))
        try:
            task = Task(
                title=golden.name,
                description=golden.description,
                repo_path=str(repo),
                task_type=golden.task_type,
            )
            db.insert_task(conn, task)
            run = Run(
                id=uuid.uuid4().hex,
                task_id=task.id,
                status=RunStatus.RUNNING,
                provider=provider_name,
                started_at=datetime.now(timezone.utc),
            )
            db.insert_run(conn, run)

            budget = Budget(BUDGET_STEPS, BUDGET_TOKENS)
            audit = AuditLog(conn, redact=redact)
            guardrails = Guardrails(
                paths=PathPolicy(repo, ["**"]),
                commands=CommandPolicy(ALLOWED_COMMANDS),
                budget=budget,
                audit=audit,
            )
            registry = ToolRegistry()
            for tool in (
                ReadFileTool(repo),
                SearchCodeTool(repo),
                SearchChunksTool(repo),
                RunTestsTool(repo),
                OpenIssueTool(),
            ):
                registry.register(tool)

            executor = Executor(conn, _make_provider(provider_name, golden, base_url, model),
                                registry, guardrails, audit, budget)
            started = time.perf_counter()
            outcome = executor.run(task, run.id)
            seconds = time.perf_counter() - started
        finally:
            conn.close()

    answer = outcome.result or ""
    passed = outcome.status is RunStatus.COMPLETED and all(
        keyword in answer.lower() for keyword in golden.expected
    )
    return {
        "task": golden.name,
        "type": golden.task_type,
        "status": outcome.status.value,
        "passed": passed,
        "steps": outcome.steps,
        "tokens": outcome.tokens,
        "seconds": round(seconds, 3),
        "provider": provider_name,
        "model": model,
        "base_url": base_url,
        # failed runs have no answer, so show the error instead
        "result": (outcome.result or outcome.error or "")[:RESULT_CHARS],
        "answer": outcome.result or "",
        "error": outcome.error or "",
    }


def _make_provider(name: str, golden: GoldenTask, base_url: str | None, model: str | None) -> Provider:
    if name == "stub":
        return StubProvider(golden.stub_script)
    return LlamaCppProvider(base_url, model)


def render_table(rows: list[dict]) -> str:
    lines = [
        "| " + " | ".join(COLUMNS) + " |",
        "|" + "|".join("---" for _ in COLUMNS) + "|",
    ]
    for row in rows:
        cells = [
            row["task"],
            row["type"],
            row["status"],
            "yes" if row["passed"] else "no",
            str(row["steps"]),
            str(row["tokens"]),
            f"{row['seconds']:.2f}",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="run the golden tasks against the fixture repo")
    parser.add_argument("--provider", choices=["stub", "llama_cpp"], default="stub")
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    try:
        rows = run_suite(args.provider, base_url=args.base_url, model=args.model)
    except ValueError as exc:
        parser.error(str(exc))

    print(render_table(rows))
    passed = sum(1 for row in rows if row["passed"])
    print(f"\npassed {passed} of {len(rows)}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
