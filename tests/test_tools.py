import socket
import sys

import pytest

from workbench.guardrails.paths import PathDenied
from workbench.tools import (
    OpenIssueTool,
    ReadFileTool,
    RunTestsTool,
    SearchCodeTool,
    ToolRegistry,
    ToolResult,
)

SECRET = "sk-abcdefghijklmnop1234"


class EchoTool:
    def __init__(self, name):
        self.name = name
        self.description = f"echo {name}"

    def run(self, arguments):
        return ToolResult(tool=self.name, output=str(arguments))


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\nHello World\nend\n")
    return tmp_path


def test_registry_get_by_name():
    registry = ToolRegistry()
    tool = EchoTool("alpha")
    registry.register(tool)
    assert registry.get("alpha") is tool


def test_registry_duplicate_raises():
    registry = ToolRegistry()
    registry.register(EchoTool("alpha"))
    with pytest.raises(ValueError):
        registry.register(EchoTool("alpha"))


def test_registry_unknown_raises():
    with pytest.raises(KeyError, match="unknown tool: nope"):
        ToolRegistry().get("nope")


def test_registry_describe_sorted():
    registry = ToolRegistry()
    for name in ["zeta", "alpha", "mid"]:
        registry.register(EchoTool(name))
    assert [item["name"] for item in registry.describe()] == ["alpha", "mid", "zeta"]
    assert registry.describe()[0] == {"name": "alpha", "description": "echo alpha"}
    assert registry.names() == ["alpha", "mid", "zeta"]


def test_read_file_reads_with_citation(repo):
    result = ReadFileTool(repo).run({"path": "src/app.py"})
    assert result.tool == "read_file"
    assert result.output == "x = 1\nHello World\nend\n"
    assert result.citations == ["src/app.py:1-3"]
    assert result.truncated is False


def test_read_file_truncation_flag(repo):
    result = ReadFileTool(repo).run({"path": "src/app.py", "max_bytes": 5})
    assert result.truncated is True
    assert result.output.startswith("x = 1")
    assert "truncated" in result.output


def test_read_file_missing_raises(repo):
    with pytest.raises(FileNotFoundError):
        ReadFileTool(repo).run({"path": "src/missing.py"})


def test_read_file_directory_raises(repo):
    with pytest.raises(IsADirectoryError):
        ReadFileTool(repo).run({"path": "src"})


def test_read_file_cannot_escape_root(repo):
    with pytest.raises(PathDenied):
        ReadFileTool(repo).run({"path": "../outside.txt"})


def test_read_file_redacts_secret(repo):
    (repo / "notes.txt").write_text(f"token = {SECRET}\n")
    result = ReadFileTool(repo).run({"path": "notes.txt"})
    assert SECRET not in result.output
    assert "[redacted:api_key]" in result.output


def test_search_code_case_insensitive_line_number(repo):
    result = SearchCodeTool(repo).run({"query": "hello world"})
    assert result.output == "src/app.py:2: Hello World"
    assert result.citations == ["src/app.py:2"]
    assert result.truncated is False


def test_search_code_ignores_pycache_and_git(repo):
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "cached.py").write_text("hello cache\n")
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("hello git\n")
    result = SearchCodeTool(repo).run({"query": "hello"})
    assert "__pycache__" not in result.output
    assert ".git" not in result.output
    assert result.output == "src/app.py:2: Hello World"


def test_search_code_skips_binary(repo):
    (repo / "blob.bin").write_bytes(b"hello\x00world\n")
    result = SearchCodeTool(repo).run({"query": "hello"})
    assert "blob.bin" not in result.output


def test_search_code_redacts_matching_line(repo):
    (repo / "config.txt").write_text("password = hunter2\n")
    result = SearchCodeTool(repo).run({"query": "password"})
    assert "hunter2" not in result.output
    assert "[redacted:password_assignment]" in result.output


def test_search_code_skips_file_with_multiline_secret(repo):
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEabc123\n-----END PRIVATE KEY-----\n"
    (repo / "key.txt").write_text(pem)
    result = SearchCodeTool(repo).run({"query": "abc"})
    assert result.output == ""


def test_search_code_empty_query_raises(repo):
    with pytest.raises(ValueError):
        SearchCodeTool(repo).run({"query": ""})


def test_search_code_max_results_sets_truncated(repo):
    (repo / "many.txt").write_text("hit one\nhit two\n")
    result = SearchCodeTool(repo).run({"query": "hit", "max_results": 1})
    assert result.truncated is True
    assert result.output == "many.txt:1: hit one"
    assert result.citations == ["many.txt:1"]


def test_search_code_missing_path_raises(repo):
    with pytest.raises(FileNotFoundError):
        SearchCodeTool(repo).run({"query": "x", "path": "nowhere"})


def test_run_tests_passes(repo):
    result = RunTestsTool(repo).run({"argv": [sys.executable, "-c", "print('hi')"]})
    assert "hi" in result.output
    assert result.output.splitlines()[-1] == "exit code: 0"
    assert result.citations == []


def test_run_tests_failing_exit_code(repo):
    result = RunTestsTool(repo).run({"argv": [sys.executable, "-c", "import sys; sys.exit(3)"]})
    assert result.output.splitlines()[-1] == "exit code: 3"


def test_run_tests_timeout(repo):
    result = RunTestsTool(repo).run(
        {"argv": [sys.executable, "-c", "import sys,time; time.sleep(5)"], "timeout_s": 0.5}
    )
    assert result.output == "command timed out after 0.5s"
    assert result.truncated is False


def test_run_tests_string_argv_raises(repo):
    with pytest.raises(ValueError):
        RunTestsTool(repo).run({"argv": "python -c 1"})


def test_run_tests_missing_executable(repo):
    result = RunTestsTool(repo).run({"argv": ["no-such-binary-xyz"]})
    assert result.output.startswith("command failed to start:")


def test_run_tests_redacts_and_truncates(repo):
    script = f"print('{SECRET}'); print('x' * 5000)"
    result = RunTestsTool(repo).run({"argv": [sys.executable, "-c", script]})
    assert SECRET not in result.output
    assert result.truncated is True
    assert result.output.splitlines()[-1] == "exit code: 0"


def test_open_issue_dry_run(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "socket", no_network)
    result = OpenIssueTool().run({"title": "Flaky test", "body": "steps"})
    assert result.output.startswith("dry run, nothing was sent")
    assert "Flaky test" in result.output
    assert result.tool == "open_issue"


def test_open_issue_empty_title_raises():
    with pytest.raises(ValueError):
        OpenIssueTool().run({"title": "  ", "body": "x"})
