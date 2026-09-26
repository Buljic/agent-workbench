import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from workbench import db
from workbench.agents.planner import PlanError, build_messages, parse_action
from workbench.agents.provider import Provider, ProviderError
from workbench.audit import AuditLog
from workbench.guardrails.budgets import Budget, BudgetExceeded
from workbench.guardrails.facade import Guardrails
from workbench.guardrails.secrets import redact
from workbench.models import Approval, RunStatus, Task, ToolCall
from workbench.tools.registry import ToolRegistry

SUMMARY_LIMIT = 200


@dataclass
class RunOutcome:
    status: RunStatus
    result: str | None
    steps: int
    tokens: int
    error: str | None = None
    approval_id: str | None = None


class Executor:
    def __init__(
        self,
        conn: sqlite3.Connection,
        provider: Provider,
        registry: ToolRegistry,
        guardrails: Guardrails,
        audit: AuditLog,
        budget: Budget,
        max_tokens_per_step: int = 400,
    ) -> None:
        # steps are spent through guardrails and tokens too, so both must share one budget
        if budget is not guardrails.budget:
            raise ValueError("budget must be the same object as guardrails.budget")
        self.conn = conn
        self.provider = provider
        self.registry = registry
        self.guardrails = guardrails
        self.audit = audit
        self.budget = budget
        self.max_tokens_per_step = max_tokens_per_step

    def run(self, task: Task, run_id: str) -> RunOutcome:
        outcome = self._run_loop(task, run_id)
        self._finalize(run_id, outcome)
        return outcome

    def _finalize(self, run_id: str, outcome: RunOutcome) -> None:
        # the run row must never be left at RUNNING after the loop returns
        now = datetime.now(timezone.utc)
        fields = {
            "status": outcome.status,
            "steps": outcome.steps,
            "tokens": outcome.tokens,
            "result": outcome.result,
            "error": outcome.error,
        }
        if outcome.status is not RunStatus.WAITING_APPROVAL:
            fields["finished_at"] = now
        db.update_run(self.conn, run_id, **fields)
        self.audit.record(run_id, "run_finished", {"status": outcome.status.value})

    def _run_loop(self, task: Task, run_id: str) -> RunOutcome:
        self.audit.record(run_id, "run_started", {"task_id": task.id})
        history: list[dict] = []
        steps = 0
        tokens = 0

        while self.budget.remaining_steps > 0:
            self.guardrails.spend_step(run_id)
            steps += 1

            messages = build_messages(task, history, self.registry.describe())
            try:
                raw = self.provider.complete(messages, max_tokens=self.max_tokens_per_step)
            except ProviderError as exc:
                self.audit.record(run_id, "provider_error", {"error": str(exc)})
                return RunOutcome(RunStatus.FAILED, None, steps, tokens, error=f"provider error: {exc}")

            # rough estimate: about four characters per token
            cost = (sum(len(message["content"]) for message in messages) + len(raw)) // 4
            try:
                self.guardrails.budget.spend_tokens(cost)
            except BudgetExceeded as exc:
                self.audit.record(run_id, "budget_exceeded", {"kind": exc.kind, "limit": exc.limit})
                return RunOutcome(RunStatus.BLOCKED, None, steps, tokens, error=str(exc))
            tokens += cost

            try:
                action = parse_action(raw)
            except PlanError as exc:
                self.audit.record(run_id, "parse_error", {"error": str(exc)})
                return RunOutcome(RunStatus.FAILED, None, steps, tokens, error=f"plan error: {exc}")

            if "final" in action:
                return RunOutcome(RunStatus.COMPLETED, action["final"], steps, tokens)

            tool = action["tool"]
            arguments = action["arguments"]
            decision = self.guardrails.check(run_id, tool, arguments)
            db.insert_tool_call(
                self.conn,
                run_id,
                ToolCall(
                    step=steps,
                    tool=tool,
                    arguments=arguments,
                    allowed=decision.allowed,
                    blocked_reason=decision.reason,
                ),
            )

            if not decision.allowed:
                history.append({"role": "assistant", "content": raw})
                history.append({"role": "user", "content": f"blocked: {decision.reason} (tool {tool})"})
                continue

            if decision.needs_approval:
                approval = Approval(
                    id=uuid.uuid4().hex,
                    run_id=run_id,
                    tool=tool,
                    arguments=arguments,
                    requested_at=datetime.now(timezone.utc),
                )
                db.insert_approval(self.conn, approval)
                self.audit.record(run_id, "approval_requested", {"approval_id": approval.id, "tool": tool})
                db.update_run(self.conn, run_id, status=RunStatus.WAITING_APPROVAL)
                return RunOutcome(
                    RunStatus.WAITING_APPROVAL, None, steps, tokens, approval_id=approval.id
                )

            try:
                impl = self.registry.get(tool)
            except KeyError:
                return RunOutcome(RunStatus.FAILED, None, steps, tokens, error=f"unknown tool: {tool}")

            try:
                result = impl.run(arguments)
            except Exception as exc:
                # a failing tool goes back to the model so it can recover
                output = redact(f"error: {exc}")
                self._finish_tool_call(run_id, steps, output, [])
            else:
                output = result.output
                self._finish_tool_call(run_id, steps, output, result.citations)

            history.append({"role": "assistant", "content": raw})
            history.append({"role": "user", "content": f"{tool} -> {output}"})

        self.audit.record(run_id, "budget_exceeded", {"kind": "steps"})
        return RunOutcome(RunStatus.BLOCKED, None, steps, tokens, error="step budget exhausted")

    def _finish_tool_call(self, run_id: str, step: int, output: str, citations: list[str]) -> None:
        # db has no helper to update a tool call, so the row is set here; (run_id, step) is unique
        self.conn.execute(
            "UPDATE tool_calls SET result_summary = ?, citations = ? WHERE run_id = ? AND step = ?",
            (output[:SUMMARY_LIMIT], json.dumps(citations), run_id, step),
        )
        self.conn.commit()
