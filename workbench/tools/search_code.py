import os
from collections.abc import Iterator
from pathlib import Path

from workbench.guardrails.paths import PathPolicy
from workbench.guardrails.secrets import redact
from workbench.tools.registry import ToolResult

SKIP_PARTS = {".git", "__pycache__"}
BINARY_PROBE = 8192


class SearchCodeTool:
    name = "search_code"
    description = "Case-insensitive text search across the repository."

    def __init__(self, root: str | Path = ".") -> None:
        # default deny globs stay on: search must not read env files or keys
        self.policy = PathPolicy(root, ["**"])

    def run(self, arguments: dict) -> ToolResult:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        max_results = arguments.get("max_results", 20)
        if not isinstance(max_results, int) or max_results < 1:
            raise ValueError("max_results must be a positive integer")

        path = arguments.get("path", ".")
        start = self.policy.resolve(path)
        if not start.exists():
            raise FileNotFoundError(f"no such path: {path}")

        needle = query.lower()
        hits: list[tuple[str, int, str]] = []
        truncated = False
        for hit in self._matches(start, needle):
            if len(hits) == max_results:
                # one more match exists past the limit
                truncated = True
                break
            hits.append(hit)

        output = redact("\n".join(f"{rel}:{number}: {line}" for rel, number, line in hits))
        citations = [redact(f"{rel}:{number}") for rel, number, _ in hits]
        return ToolResult(
            tool=self.name,
            output=output,
            citations=citations,
            truncated=truncated,
        )

    def _allowed(self, path: Path) -> bool:
        try:
            relative = path.resolve().relative_to(self.policy.root).as_posix()
        except ValueError:
            return False
        return self.policy.check(relative).allowed

    def _files(self, start: Path) -> Iterator[Path]:
        if start.is_file():
            if self._allowed(start):
                yield start
            return
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_PARTS)
            for filename in sorted(filenames):
                candidate = Path(dirpath) / filename
                if self._allowed(candidate):
                    yield candidate

    def _matches(self, start: Path, needle: str) -> Iterator[tuple[str, int, str]]:
        for path in self._files(start):
            rel_path = path.relative_to(self.policy.root)
            if SKIP_PARTS.intersection(rel_path.parts):
                continue
            # a symlinked file may point outside root, so confine the real path
            if not path.resolve().is_relative_to(self.policy.root):
                continue
            try:
                with path.open("rb") as handle:
                    head = handle.read(BINARY_PROBE)
                    if b"\x00" in head:
                        continue
                    data = head + handle.read()
            except OSError:
                continue

            text = data.decode("utf-8", errors="replace")
            lines = text.splitlines()
            # redaction keeps line numbers only when no secret spans lines;
            # otherwise the file holds a multi-line secret and is skipped
            safe_lines = redact(text).splitlines()
            if len(safe_lines) != len(lines):
                continue

            rel = rel_path.as_posix()
            for number, (line, safe) in enumerate(zip(lines, safe_lines), 1):
                if needle in line.lower():
                    yield rel, number, safe
