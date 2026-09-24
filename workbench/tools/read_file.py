from pathlib import Path

from workbench.guardrails.paths import PathPolicy
from workbench.guardrails.secrets import redact
from workbench.tools.registry import ToolResult


class ReadFileTool:
    name = "read_file"
    description = "Read a text file from the repository."

    def __init__(self, root: str | Path = ".") -> None:
        # the policy only confines the path to root; allowlists live in the facade
        self.policy = PathPolicy(root, ["**"], deny_globs=[])

    def run(self, arguments: dict) -> ToolResult:
        path = arguments["path"]
        max_bytes = arguments.get("max_bytes", 65536)
        target = self.policy.resolve(path)
        if not target.exists():
            raise FileNotFoundError(f"no such file: {path}")
        if target.is_dir():
            raise IsADirectoryError(f"is a directory: {path}")

        raw = target.read_bytes().decode("utf-8", errors="replace")
        last_line = max(1, len(raw.splitlines()))
        # redact before cutting so a secret cannot survive as a partial prefix
        text = redact(raw)

        truncated = len(text) > max_bytes
        output = text[:max_bytes]
        if truncated:
            output += f"\n[truncated: output limited to {max_bytes} characters]"
        citation = redact(f"{path}:1-{last_line}")
        return ToolResult(
            tool=self.name,
            output=output,
            citations=[citation],
            truncated=truncated,
        )
