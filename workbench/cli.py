import argparse
import json
import shutil
import sys
import tempfile
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from workbench import db
from workbench.agents import Executor, StubProvider
from workbench.audit import AuditLog
from workbench.guardrails import CommandPolicy, PathPolicy
from workbench.guardrails.budgets import Budget
from workbench.guardrails.facade import Guardrails
from workbench.guardrails.secrets import redact
from workbench.models import Run, RunStatus, Task
from workbench.tools import OpenIssueTool, ReadFileTool, RunTestsTool, SearchCodeTool, ToolRegistry

# same policy as app/main.py and evals/run_eval.py
ALLOWED_COMMANDS = [["pytest"], ["python", "-m", "pytest"]]
BUDGET_STEPS = 6
BUDGET_TOKENS = 12000
PAYLOAD_CHARS = 120

ROOT = Path(__file__).resolve().parent.parent

# the stub replies are scripted, so the answer shows the pipeline, not a model
DEMO_SCRIPT = [
    json.dumps({"tool": "read_file", "arguments": {"path": "app/server.py"}}),
    json.dumps({
        "final": "app/server.py:5 RESULTS is a module level dict and worker writes to it "
                 "at line 37 with no lock, so two workers can race on the same keys.",
    }),
]


def demo(repo_root: str | Path | None = None) -> int:
    fixture = (ROOT if repo_root is None else Path(repo_root)) / "evals" / "fixture_repo"
    # the run works on a copy, so the committed fixture is never touched
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(fixture, repo)
        with closing(db.connect(str(Path(tmp) / "workbench.db"))) as conn:
            task = Task(
                title="shared-state race review",
                description="Find the shared-state race in app/server.py. Name the shared state that has no lock and its line.",
                repo_path=str(repo),
                task_type="review",
            )
            db.insert_task(conn, task)
            run = Run(
                id=uuid.uuid4().hex,
                task_id=task.id,
                status=RunStatus.RUNNING,
                provider="stub",
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
            for tool in (ReadFileTool(repo), SearchCodeTool(repo), RunTestsTool(repo), OpenIssueTool()):
                registry.register(tool)

            executor = Executor(conn, StubProvider(DEMO_SCRIPT), registry, guardrails, audit, budget)
            outcome = executor.run(task, run.id)

            print(f"status: {outcome.status.value}")
            print(f"steps: {outcome.steps}, tokens: {outcome.tokens}")
            print(f"answer: {outcome.result or ''}")
            print("audit:")
            for record in db.list_audit(conn, run.id):
                payload = json.dumps(record.payload, sort_keys=True)
                if len(payload) > PAYLOAD_CHARS:
                    payload = payload[: PAYLOAD_CHARS - 3] + "..."
                print(f"  {record.seq} {record.event} {payload}")

    return 0 if outcome.status is RunStatus.COMPLETED else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m workbench.cli", description="agent workbench command line")
    parser.add_argument("command", nargs="?", help="only 'demo' is supported")
    args = parser.parse_args(argv)
    if args.command != "demo":
        parser.print_usage(sys.stderr)
        return 2
    return demo()


if __name__ == "__main__":
    raise SystemExit(main())
