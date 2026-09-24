from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ToolResult:
    tool: str
    output: str
    citations: list[str] = field(default_factory=list)
    truncated: bool = False


class Tool(Protocol):
    name: str
    description: str

    def run(self, arguments: dict) -> ToolResult: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self) -> list[dict]:
        return [
            {"name": name, "description": self._tools[name].description}
            for name in self.names()
        ]
