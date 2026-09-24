from workbench.guardrails.secrets import redact
from workbench.tools.registry import ToolResult


class OpenIssueTool:
    name = "open_issue"
    description = "Draft an issue. Dry run only: nothing is sent or written."

    def run(self, arguments: dict) -> ToolResult:
        title = arguments.get("title")
        body = arguments.get("body", "")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string")
        if not isinstance(body, str):
            raise ValueError("body must be a string")

        # no network and no file writes here; the text is only rendered
        rendered = f"title: {title}\n\nbody: {body}"
        output = redact(f"dry run, nothing was sent\n\n{rendered}")
        return ToolResult(tool=self.name, output=output)
