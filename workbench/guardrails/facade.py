from dataclasses import dataclass
from typing import Any

from workbench.audit import AuditLog
from workbench.guardrails.budgets import Budget, BudgetExceeded
from workbench.guardrails.commands import CommandPolicy
from workbench.guardrails.paths import PathPolicy

KNOWN_TOOLS = {"read_file", "search_code", "search_chunks", "run_tests", "open_issue"}


@dataclass
class ToolDecision:
    allowed: bool
    reason: str | None = None
    needs_approval: bool = False


def _escapes_repository(arg: str) -> bool:
    if arg.startswith("@"):
        # pytest reads the file named after @, including deny-listed files
        return True
    if arg.startswith("-") and "=" in arg and _path_escapes(arg.split("=", 1)[1]):
        return True
    if len(arg) > 2 and arg.startswith("-") and not arg.startswith("--") and _path_escapes(arg[2:]):
        # a short flag glued to a path, for example -c/etc/passwd
        return arg[2] in "/~"
    return _path_escapes(arg)


def _path_escapes(value: str) -> bool:
    return value.startswith("/") or value.startswith("~") or ".." in value.split("/")


class Guardrails:
    def __init__(
        self,
        paths: PathPolicy,
        commands: CommandPolicy,
        budget: Budget,
        audit: AuditLog,
        write_tools: set[str] | None = None,
    ) -> None:
        self.paths = paths
        self.commands = commands
        self.budget = budget
        self.audit = audit
        self.write_tools = {"open_issue"} if write_tools is None else set(write_tools)

    def check(self, run_id: str, tool: str, arguments: dict[str, Any]) -> ToolDecision:
        decision = self._decide(tool, arguments)
        if decision.allowed:
            self.audit.record(run_id, "tool_call", {"tool": tool, "arguments": arguments})
        else:
            self.audit.record(
                run_id, "tool_blocked", {"tool": tool, "arguments": arguments, "reason": decision.reason}
            )
        return decision

    def spend_step(self, run_id: str) -> None:
        try:
            self.budget.spend_step()
        except BudgetExceeded as exc:
            self.audit.record(run_id, "budget_exceeded", {"kind": exc.kind, "limit": exc.limit})
            raise

    def _decide(self, tool: str, arguments: dict[str, Any]) -> ToolDecision:
        if tool not in KNOWN_TOOLS:
            return ToolDecision(False, "unknown tool")
        if tool in ("read_file", "search_code"):
            return self._check_path(arguments.get("path", "."))
        if tool == "run_tests":
            return self._check_run_tests(arguments)
        # open_issue is a dry run; nothing is sent anywhere
        return ToolDecision(True, needs_approval=tool in self.write_tools)

    def _check_run_tests(self, arguments: dict[str, Any]) -> ToolDecision:
        argv = arguments.get("argv")
        if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
            return ToolDecision(False, "argv must be a list of strings")
        command = self.commands.check(argv)
        if not command.allowed:
            return ToolDecision(False, command.reason)
        # arguments must not point at absolute paths, walk upwards, or pull in a file
        # through a pytest argfile or an attached flag value; argv[0] is the program
        for arg in argv[1:]:
            if _escapes_repository(arg):
                return ToolDecision(False, f"argument escapes the repository: {arg}")
        cwd = self._check_path(arguments.get("cwd", "."))
        if not cwd.allowed:
            return cwd
        return ToolDecision(True)

    def _check_path(self, value: Any) -> ToolDecision:
        if not isinstance(value, str):
            return ToolDecision(False, "path must be a string")
        decision = self.paths.check(value)
        return ToolDecision(decision.allowed, decision.reason)
