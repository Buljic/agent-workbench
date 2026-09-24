import subprocess
from pathlib import Path

from workbench.guardrails.paths import PathPolicy
from workbench.guardrails.secrets import redact
from workbench.tools.registry import ToolResult

OUTPUT_LIMIT = 4000


class RunTestsTool:
    name = "run_tests"
    description = "Run an argv command in the repository and report its exit code."

    def __init__(self, root: str | Path = ".") -> None:
        # the facade's command policy decides what may run; here cwd stays inside root
        self.policy = PathPolicy(root, ["**"], deny_globs=[])

    def run(self, arguments: dict) -> ToolResult:
        argv = arguments.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("argv must be a non-empty list of strings")
        timeout_s = arguments.get("timeout_s", 120)
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
            raise ValueError("timeout_s must be a positive number")
        cwd = self.policy.resolve(arguments.get("cwd", "."))

        try:
            done = subprocess.run(
                argv,
                cwd=cwd,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(tool=self.name, output=f"command timed out after {timeout_s}s")
        except OSError as exc:
            return ToolResult(tool=self.name, output=redact(f"command failed to start: {exc}"))

        # redact before cutting, then keep the exit code as the last line
        body = redact(done.stdout + done.stderr).rstrip("\n")
        truncated = len(body) > OUTPUT_LIMIT
        if truncated:
            body = body[:OUTPUT_LIMIT] + "\n[truncated]"
        lines = [body] if body else []
        lines.append(f"exit code: {done.returncode}")
        return ToolResult(tool=self.name, output="\n".join(lines), truncated=truncated)
